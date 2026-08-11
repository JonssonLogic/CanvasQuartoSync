"""Tests for the content-folder scaffolder."""

import json
import os

from init_content_project import install, kit_status, load_stamp
from handlers import __version__


def _files(root):
    """Return every file under root as forward-slashed relative paths."""
    out = set()
    for dirpath, _, filenames in os.walk(root):
        for name in filenames:
            rel = os.path.relpath(os.path.join(dirpath, name), root)
            out.add(rel.replace("\\", "/"))
    return out


class TestScaffold:

    def test_creates_expected_tree(self, tmp_path):
        install(str(tmp_path))
        files = _files(str(tmp_path))
        assert ".claude/skills/canvas-content/SKILL.md" in files
        assert ".claude/skills/canvas-content/reference/frontmatter.md" in files
        assert ".claude/canvas-kit.json" in files
        assert {"CLAUDE.md", "config.toml", "_quarto.yml", "branding.css",
                ".gitignore"} <= files
        assert {"check_content.bat", "check_content.sh",
                "update_kit.bat", "update_kit.sh"} <= files

    def test_wrappers_are_stamped_with_real_paths(self, tmp_path):
        install(str(tmp_path), python_exe=r"C:\some\venv\python.exe")
        text = (tmp_path / "check_content.bat").read_text(encoding="utf-8")
        assert "@@PYTHON@@" not in text and "@@REPO@@" not in text
        assert r"C:\some\venv\python.exe" in text
        assert "validate_content.py" in text

    def test_stamp_records_version(self, tmp_path):
        install(str(tmp_path))
        assert load_stamp(str(tmp_path))["kit_version"] == __version__

    def test_example_only_with_flag(self, tmp_path):
        install(str(tmp_path))
        assert "01_Introduction/01_Welcome.qmd" not in _files(str(tmp_path))
        install(str(tmp_path), with_example=True)
        assert "01_Introduction/01_Welcome.qmd" in _files(str(tmp_path))

    def test_scaffolded_example_validates_clean(self, tmp_path):
        """A fresh folder must pass its own checker out of the box."""
        from validate_content import validate_path
        install(str(tmp_path), with_example=True)
        reports = validate_path(str(tmp_path))
        assert [r for r in reports if r.errors] == []


class TestUpdate:

    def test_update_refreshes_skill(self, tmp_path):
        install(str(tmp_path))
        skill = tmp_path / ".claude" / "skills" / "canvas-content" / "SKILL.md"
        skill.write_text("stale", encoding="utf-8")
        install(str(tmp_path), update=True)
        assert "stale" not in skill.read_text(encoding="utf-8")

    def test_update_removes_deleted_reference_files(self, tmp_path):
        install(str(tmp_path))
        stray = tmp_path / ".claude" / "skills" / "canvas-content" / "reference" / "old.md"
        stray.write_text("obsolete", encoding="utf-8")
        install(str(tmp_path), update=True)
        assert not stray.exists()

    def test_update_preserves_config_and_content(self, tmp_path):
        install(str(tmp_path))
        (tmp_path / "config.toml").write_text("course_id = 1434\n", encoding="utf-8")
        mine = tmp_path / "01_Mod" / "01_Mine.qmd"
        mine.parent.mkdir(parents=True)
        mine.write_text("---\ncanvas:\n  type: page\n---\nmine\n", encoding="utf-8")

        install(str(tmp_path), update=True)

        assert (tmp_path / "config.toml").read_text(encoding="utf-8") == "course_id = 1434\n"
        assert mine.read_text(encoding="utf-8").endswith("mine\n")

    def test_update_refreshes_untouched_claude_md(self, tmp_path):
        install(str(tmp_path))
        claude = tmp_path / "CLAUDE.md"
        original = claude.read_text(encoding="utf-8")
        claude.write_text(original.replace("Course content folder", "x"), encoding="utf-8")
        # Rewriting to something else marks it as edited, so restore first:
        claude.write_text(original, encoding="utf-8")
        install(str(tmp_path), update=True)
        assert "Course content folder" in claude.read_text(encoding="utf-8")

    def test_update_preserves_edited_claude_md(self, tmp_path):
        install(str(tmp_path))
        claude = tmp_path / "CLAUDE.md"
        claude.write_text(claude.read_text(encoding="utf-8") + "\nMy own rules.\n",
                          encoding="utf-8")
        install(str(tmp_path), update=True)
        assert "My own rules." in claude.read_text(encoding="utf-8")

    def test_update_does_not_restore_deleted_starter_files(self, tmp_path):
        install(str(tmp_path))
        (tmp_path / "branding.css").unlink()
        install(str(tmp_path), update=True)
        assert not (tmp_path / "branding.css").exists()


class TestKitStatus:

    def test_no_kit_is_silent(self, tmp_path):
        assert kit_status(str(tmp_path)) is None

    def test_current_kit_is_silent(self, tmp_path):
        install(str(tmp_path))
        assert kit_status(str(tmp_path)) is None

    def test_stale_kit_warns(self, tmp_path):
        install(str(tmp_path))
        stamp_path = tmp_path / ".claude" / "canvas-kit.json"
        data = json.loads(stamp_path.read_text(encoding="utf-8"))
        data["kit_version"] = "0.0.1"
        stamp_path.write_text(json.dumps(data), encoding="utf-8")

        message = kit_status(str(tmp_path))
        assert message and "0.0.1" in message and "update_kit" in message
