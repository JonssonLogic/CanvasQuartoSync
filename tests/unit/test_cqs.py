"""cqs: one command that hands each verb to an existing script.

What matters most here is that each verb reaches the right script with the
right arguments, from wherever in the course it is typed, and that bare `cqs`
touches nothing: writing to Canvas must always take a verb somebody typed.
"""

import os
import shutil
import subprocess
import sys
import types
import venv

import pytest

import cqs

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
IS_WINDOWS = os.name == "nt"
BASH = shutil.which("bash")


@pytest.fixture
def course(tmp_path):
    """A course folder with one module, and a subfolder to stand in."""
    root = tmp_path / "Course"
    mod = root / "04_Labs"
    mod.mkdir(parents=True)
    (root / "config.toml").write_text('course_id = 18289\ncourse_name = "Hållfasthetslära"\n',
                                      encoding="utf-8")
    (mod / "01_Tensile.qmd").write_text("---\ntitle: T\n---\n", encoding="utf-8")
    return root


@pytest.fixture
def calls(monkeypatch):
    """Replace every script with a stub that records the argv it was given."""
    seen = []

    def fake_import(name):
        def main():
            seen.append((name, list(sys.argv)))
            return 0
        return types.SimpleNamespace(main=main)

    monkeypatch.setattr(cqs.importlib, "import_module", fake_import)
    return seen


def run(args, cwd, monkeypatch):
    monkeypatch.chdir(cwd)
    return cqs.main(args)


# ---------------------------------------------------------------------------
# Finding the course
# ---------------------------------------------------------------------------

class TestCourseRoot:

    def test_found_from_a_subfolder(self, course):
        assert cqs.find_course_root(str(course / "04_Labs")) == str(course)

    def test_none_outside_a_course(self, tmp_path):
        assert cqs.find_course_root(str(tmp_path)) is None

    def test_an_older_course_without_config_toml_is_still_found(self, tmp_path):
        (tmp_path / "course_id.txt").write_text("1434")
        assert cqs.find_course_root(str(tmp_path)) == str(tmp_path)

    def test_outside_a_course_says_so_and_runs_nothing(self, tmp_path, calls, monkeypatch, capsys):
        assert run(["check"], tmp_path, monkeypatch) == 2
        assert calls == []
        assert "not inside a course folder" in capsys.readouterr().err

    def test_dash_C_points_somewhere_else(self, course, tmp_path, calls, monkeypatch):
        run(["-C", str(course), "check"], tmp_path, monkeypatch)
        assert calls[0][1][1] == str(course)


# ---------------------------------------------------------------------------
# Bare cqs and unknown verbs
# ---------------------------------------------------------------------------

class TestNoVerb:

    def test_bare_cqs_prints_help_and_runs_nothing(self, course, calls, monkeypatch, capsys):
        assert run([], course, monkeypatch) == 0
        assert calls == []
        assert "usage: cqs" in capsys.readouterr().out

    def test_unknown_verb(self, course, calls, monkeypatch, capsys):
        assert run(["deploy"], course, monkeypatch) == 2
        assert calls == []
        assert "unknown command" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Each verb reaches its script
# ---------------------------------------------------------------------------

class TestVerbs:

    def test_check_from_a_subfolder_checks_the_whole_course(self, course, calls, monkeypatch):
        run(["check"], course / "04_Labs", monkeypatch)
        name, argv = calls[0]
        assert name == "validate_content"
        assert argv[1:] == [str(course), "--content-root", str(course)]

    def test_check_one_file_relative_to_where_you_stand(self, course, calls, monkeypatch):
        run(["check", "01_Tensile.qmd", "--errors-only"], course / "04_Labs", monkeypatch)
        argv = calls[0][1]
        assert argv[1] == str(course / "04_Labs" / "01_Tensile.qmd")
        assert argv[-1] == "--errors-only"

    def test_sync_a_file_becomes_course_relative_only(self, course, calls, monkeypatch):
        run(["sync", "01_Tensile.qmd"], course / "04_Labs", monkeypatch)
        name, argv = calls[0]
        assert name == "sync_to_canvas"
        assert argv[1:] == [str(course), "--only", "04_Labs/01_Tensile.qmd"]

    def test_sync_accepts_the_course_relative_form_too(self, course, calls, monkeypatch):
        run(["sync", "--only", "04_Labs/01_Tensile.qmd"], course / "04_Labs", monkeypatch)
        assert calls[0][1][-2:] == ["--only", "04_Labs/01_Tensile.qmd"]

    def test_sync_refuses_a_file_outside_the_course(self, course, tmp_path, calls, monkeypatch):
        outside = tmp_path / "elsewhere.qmd"
        outside.write_text("x")
        assert run(["sync", str(outside)], course, monkeypatch) == 2
        assert calls == []

    def test_sync_passes_other_options_through(self, course, calls, monkeypatch):
        run(["sync", "--force", "--sync-calendar"], course, monkeypatch)
        assert calls[0][1][1:] == [str(course), "--force", "--sync-calendar"]

    def test_diff_is_a_drift_check(self, course, calls, monkeypatch):
        run(["diff", "04_Labs/01_Tensile.qmd", "--show-diff"], course, monkeypatch)
        name, argv = calls[0]
        assert name == "sync_to_canvas"
        assert argv[1:] == [str(course), "--check-drift", "--only", "04_Labs/01_Tensile.qmd", "--show-diff"]

    def test_rollup_only_is_rewritten(self, course, calls, monkeypatch):
        run(["rollup", "--status", "--only", "01_Tensile.qmd"], course / "04_Labs", monkeypatch)
        name, argv = calls[0]
        assert name == "rollup"
        assert argv[1:] == [str(course), "--status", "--only", "04_Labs/01_Tensile.qmd"]

    @pytest.mark.parametrize("verb,module", [("import", "import_from_canvas"),
                                             ("purge", "purge_course")])
    def test_course_verbs_get_the_root(self, course, calls, monkeypatch, verb, module):
        run([verb, "--dry-run"], course / "04_Labs", monkeypatch)
        assert calls[0][0] == module
        assert calls[0][1][1:] == [str(course), "--dry-run"]

    def test_init_needs_no_course(self, tmp_path, calls, monkeypatch):
        run(["init", "NewCourse", "--with-example"], tmp_path, monkeypatch)
        name, argv = calls[0]
        assert name == "init_content_project"
        assert argv[1:] == [str(tmp_path / "NewCourse"), "--with-example"]

    def test_kit_update(self, course, calls, monkeypatch):
        run(["kit", "update"], course / "04_Labs", monkeypatch)
        assert calls[0] == ("init_content_project", ["cqs kit", str(course), "--update"])

    def test_kit_without_update_is_refused(self, course, calls, monkeypatch):
        assert run(["kit"], course, monkeypatch) == 2
        assert calls == []

    def test_usage_names_the_cqs_verb(self, course, calls, monkeypatch):
        run(["sync"], course, monkeypatch)
        assert calls[0][1][0] == "cqs sync"


class TestExitCodes:

    def test_the_scripts_exit_code_comes_back(self, course, monkeypatch):
        def fake_import(name):
            def main():
                raise SystemExit(3)
            return types.SimpleNamespace(main=main)
        monkeypatch.setattr(cqs.importlib, "import_module", fake_import)
        assert run(["check"], course, monkeypatch) == 3

    def test_a_script_this_copy_lacks_is_reported(self, course, monkeypatch, capsys):
        def fake_import(name):
            raise ModuleNotFoundError(f"No module named '{name}'", name=name)
        monkeypatch.setattr(cqs.importlib, "import_module", fake_import)
        assert run(["rollup"], course, monkeypatch) == 2
        assert "Update the tool" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# where, for real: no stubs, run as a subprocess
# ---------------------------------------------------------------------------

def test_where_names_tool_course_and_canvas_id(course):
    out = subprocess.run([sys.executable, os.path.join(REPO, "cqs.py"), "where"],
                         cwd=course / "04_Labs", capture_output=True, text=True,
                         encoding="utf-8", env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    assert out.returncode == 0, out.stderr
    assert f"tool:    {REPO}" in out.stdout
    assert f"course:  {course}" in out.stdout
    assert "course 18289, Hållfasthetslära" in out.stdout


# ---------------------------------------------------------------------------
# The PATH shims in bin/
# ---------------------------------------------------------------------------

STUB = "import sys\nprint('STUB ' + ' '.join(sys.argv[1:]))\n"


@pytest.fixture(scope="module")
def fake_tool(tmp_path_factory):
    """A stand-in install: bin/ from the repo, a stub cqs.py and a real, empty venv."""
    tool = tmp_path_factory.mktemp("tool") / "CanvasQuartoSync"
    shutil.copytree(os.path.join(REPO, "bin"), tool / "bin")
    (tool / "cqs.py").write_text(STUB)
    venv.create(tool / ".venv", with_pip=False)
    return tool


def _env(**extra):
    env = {k: v for k, v in os.environ.items() if k != "CANVAS_QUARTO_SYNC_DIR"}
    env.update(extra)
    return env


@pytest.mark.skipif(not IS_WINDOWS, reason="cqs.cmd is for Windows")
class TestCmdShim:

    def test_finds_the_install_it_lives_in(self, fake_tool, tmp_path):
        out = subprocess.run([str(fake_tool / "bin" / "cqs.cmd"), "check", "a b"],
                             cwd=tmp_path, capture_output=True, text=True, env=_env())
        assert out.returncode == 0, out.stdout + out.stderr
        assert "STUB check a b" in out.stdout

    def test_dev_clone_wins(self, fake_tool, tmp_path):
        dev = tmp_path / "dev"
        shutil.copytree(fake_tool, dev, ignore=shutil.ignore_patterns("bin"))
        (dev / "cqs.py").write_text("print('DEV')\n")
        out = subprocess.run([str(fake_tool / "bin" / "cqs.cmd"), "where"], cwd=tmp_path,
                             capture_output=True, text=True, env=_env(CANVAS_QUARTO_SYNC_DIR=str(dev)))
        assert "DEV" in out.stdout

    def test_a_broken_dev_clone_says_so(self, fake_tool, tmp_path):
        out = subprocess.run([str(fake_tool / "bin" / "cqs.cmd")], cwd=tmp_path, capture_output=True,
                             text=True, env=_env(CANVAS_QUARTO_SYNC_DIR=str(tmp_path / "nope")))
        assert out.returncode == 1
        assert "CANVAS_QUARTO_SYNC_DIR is set" in out.stdout


@pytest.mark.skipif(BASH is None, reason="bash not available")
def test_sh_shim_follows_a_symlink_back_to_the_install(fake_tool, tmp_path):
    link_dir = tmp_path / "localbin"
    link_dir.mkdir()
    target = fake_tool / "bin" / "cqs"
    try:
        (link_dir / "cqs").symlink_to(target)
        shim = link_dir / "cqs"
    except OSError:
        shim = target  # no symlink rights (Windows without developer mode)
    as_posix = subprocess.run([BASH, "-c", 'cygpath -u "$1" 2>/dev/null || echo "$1"', "_", str(shim)],
                              capture_output=True, text=True).stdout.strip()
    out = subprocess.run([BASH, as_posix, "check"], cwd=tmp_path, capture_output=True, text=True, env=_env())
    assert out.returncode == 0, out.stdout + out.stderr
    assert "STUB check" in out.stdout
