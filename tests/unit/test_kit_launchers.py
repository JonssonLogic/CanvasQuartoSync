"""Run the course-folder launchers against stand-in tool installs.

check_content looks the tool up at run time instead of carrying a stamped
path. These tests build fake installs (a stub validate_content.py plus a real,
empty venv) under a temporary home and check which one the launcher picks.
The .bat runs on Windows only, the .sh wherever bash is available.

update_kit and run_sync_here carry the same lookup; test_doc_consistency
keeps the copies identical, so testing check_content covers all of them.
"""

import os
import shutil
import subprocess
import sys
import venv

import pytest

from init_content_project import install

IS_WINDOWS = os.name == "nt"
BASH = shutil.which("bash")

STUB = "import sys\nprint('STUB ' + __file__)\nprint('ARGS ' + ' '.join(sys.argv[1:]))\n"


@pytest.fixture(scope="module")
def template_venv(tmp_path_factory):
    """One empty venv, copied into each fake install (cheaper than creating many)."""
    path = tmp_path_factory.mktemp("venv") / ".venv"
    venv.create(path, with_pip=False, symlinks=not IS_WINDOWS)
    return path


def _python_in(venv_dir):
    return venv_dir / ("Scripts/python.exe" if IS_WINDOWS else "bin/python")


def _fake_tool(tool_dir, venv_dir, template_venv):
    """A tool folder the launcher accepts, with its venv at venv_dir."""
    tool_dir.mkdir(parents=True, exist_ok=True)
    (tool_dir / "sync_to_canvas.py").write_text("", encoding="utf-8")
    (tool_dir / "validate_content.py").write_text(STUB, encoding="utf-8")
    if not _python_in(venv_dir).exists():
        shutil.copytree(template_venv, venv_dir, symlinks=True, dirs_exist_ok=True)
    assert _python_in(venv_dir).exists()
    return tool_dir


def _app_dir(home):
    if IS_WINDOWS:
        return home / "AppData" / "Local" / "CanvasQuartoSync"
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / "CanvasQuartoSync"
    return home / ".local" / "share" / "canvasquartosync"


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "home"
    h.mkdir()
    return h


@pytest.fixture
def course(tmp_path):
    c = tmp_path / "course"
    install(str(c))
    return c


def _run(course, home, extra_env=None):
    env = {k: v for k, v in os.environ.items()
           if k not in ("CANVAS_QUARTO_SYNC_DIR", "TOOL_DIR", "PYTHON", "XDG_DATA_HOME")}
    env["HOME"] = str(home)
    env["USERPROFILE"] = str(home)
    env["LOCALAPPDATA"] = str(home / "AppData" / "Local")
    env.update(extra_env or {})
    if IS_WINDOWS:
        cmd = ["cmd", "/c", str(course / "check_content.bat")]
    else:
        cmd = [BASH, str(course / "check_content.sh")]
    return subprocess.run(cmd, env=env, capture_output=True, text=True,
                          stdin=subprocess.DEVNULL, timeout=60)


pytestmark = pytest.mark.skipif(not IS_WINDOWS and not BASH, reason="needs cmd or bash")


class TestLookup:

    def test_finds_the_install_location(self, course, home, template_venv):
        app = _fake_tool(_app_dir(home), _app_dir(home) / ".venv", template_venv)
        r = _run(course, home)
        assert r.returncode == 0, r.stdout + r.stderr
        assert f"STUB {app / 'validate_content.py'}" in r.stdout
        assert "--content-root" in r.stdout

    def test_install_location_beats_the_old_layouts(self, course, home, template_venv):
        _fake_tool(home / "CanvasQuartoSync", home / ".venvs" / "canvas_quarto_env", template_venv)
        app = _fake_tool(_app_dir(home), _app_dir(home) / ".venv", template_venv)
        r = _run(course, home)
        assert f"STUB {app / 'validate_content.py'}" in r.stdout, r.stdout + r.stderr

    def test_falls_back_to_the_previous_layout(self, course, home, template_venv):
        old = _fake_tool(home / "CanvasQuartoSync", home / ".venvs" / "canvas_quarto_env",
                         template_venv)
        r = _run(course, home)
        assert r.returncode == 0, r.stdout + r.stderr
        assert f"STUB {old / 'validate_content.py'}" in r.stdout

    def test_falls_back_to_the_oldest_layout(self, course, home, template_venv):
        env_dir = home / "venvs" / "canvas_quarto_env"
        old = _fake_tool(env_dir / "CanvasQuartoSync", env_dir, template_venv)
        r = _run(course, home)
        assert r.returncode == 0, r.stdout + r.stderr
        assert f"STUB {old / 'validate_content.py'}" in r.stdout

    def test_env_var_wins_over_the_install(self, course, home, tmp_path, template_venv):
        _fake_tool(_app_dir(home), _app_dir(home) / ".venv", template_venv)
        clone = _fake_tool(tmp_path / "my clone", tmp_path / "my clone" / ".venv", template_venv)
        r = _run(course, home, {"CANVAS_QUARTO_SYNC_DIR": str(clone)})
        assert r.returncode == 0, r.stdout + r.stderr
        assert f"STUB {clone / 'validate_content.py'}" in r.stdout

    def test_bad_env_var_fails_instead_of_falling_back(self, course, home, tmp_path, template_venv):
        """A typo must not silently run a different copy of the tool."""
        _fake_tool(_app_dir(home), _app_dir(home) / ".venv", template_venv)
        r = _run(course, home, {"CANVAS_QUARTO_SYNC_DIR": str(tmp_path / "nowhere")})
        assert r.returncode == 2
        assert "STUB" not in r.stdout
        assert "CANVAS_QUARTO_SYNC_DIR" in r.stdout

    def test_nothing_installed_says_where_it_looked(self, course, home):
        r = _run(course, home)
        assert r.returncode == 2
        assert "not found" in r.stdout
        assert str(_app_dir(home)) in r.stdout

    def test_tool_folder_without_its_venv_is_skipped(self, course, home, template_venv):
        """A half-finished install (no venv yet) must not be picked."""
        app = _app_dir(home)
        app.mkdir(parents=True)
        (app / "sync_to_canvas.py").write_text("", encoding="utf-8")
        old = _fake_tool(home / "CanvasQuartoSync", home / ".venvs" / "canvas_quarto_env",
                         template_venv)
        r = _run(course, home)
        assert f"STUB {old / 'validate_content.py'}" in r.stdout, r.stdout + r.stderr
