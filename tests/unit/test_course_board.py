"""Tests for course_board.py: NN_ -> order files, and the Course Board's edits.

All local. The migration is checked on what a course actually contains:
links between modules, assets inside a module folder, the sync map and its
drift snapshots, and a description file that must not turn into a page.
"""

import json
import subprocess

import pytest

import course_board as cb
from handlers import course_layout as cl
from handlers.content_utils import load_sync_map, save_sync_map


def _page(path, title="T", body="Body."):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'---\ntitle: "{title}"\ncanvas:\n  type: page\n---\n\n{body}\n', encoding="utf-8")
    return path


def _nn_course(root):
    """Two modules that link to each other, an asset, a PDF and a map entry."""
    _page(root / "01_Intro" / "01_Welcome.qmd", "Welcome",
          "See [lab one](../02_Labs/01_One.qmd#goal) and ![logo](assets/logo.png).")
    (root / "01_Intro" / "assets").mkdir()
    (root / "01_Intro" / "assets" / "logo.png").write_bytes(b"png")
    (root / "01_Intro" / "02_Syllabus.pdf").write_bytes(b"%PDF")
    _page(root / "02_Labs" / "01_One.qmd", "One",
          "Back to [welcome](../01_Intro/01_Welcome.qmd). Root link [/x](/02_Labs/02_Two.qmd).")
    _page(root / "02_Labs" / "02_Two.qmd", "Two", "Text.")
    (root / "graphics").mkdir()
    (root / "graphics" / "g.png").write_bytes(b"png")
    save_sync_map(str(root), {"_course_id": 1, "02_Labs/01_One.qmd": {"id": 5},
                              "01_Intro/assets/logo.png": {"id": 9}})
    snaps = root / ".canvas_snapshots"
    snaps.mkdir()
    (snaps / "02_Labs__01_One.qmd.html").write_text("<p>x</p>", encoding="utf-8")


class TestMigrate:

    def test_dry_run_changes_nothing(self, tmp_path):
        _nn_course(tmp_path)
        before = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*"))

        report = cb.migrate(str(tmp_path), dry_run=True)

        assert sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*")) == before
        assert ("01_Intro", "Intro") in report["renames"]

    def test_converts_folders_files_and_order(self, tmp_path):
        _nn_course(tmp_path)

        cb.migrate(str(tmp_path))

        layout = cl.load_layout(str(tmp_path))
        assert layout.mode == cl.ORDER_FILES
        assert [(m.dir, m.name) for m in layout.modules] == [("Intro", "Intro"), ("Labs", "Labs")]
        # The PDF keeps its place, which an order file has to say for it.
        assert layout.module("Intro").items == ["Welcome.qmd", "Syllabus.pdf"]
        assert layout.module("Labs").items == ["One.qmd", "Two.qmd"]
        assert not (tmp_path / "01_Intro").exists()
        assert (tmp_path / "graphics" / "g.png").exists()

    def test_links_follow_their_targets(self, tmp_path):
        _nn_course(tmp_path)

        cb.migrate(str(tmp_path))

        welcome = (tmp_path / "Intro" / "Welcome.qmd").read_text(encoding="utf-8")
        one = (tmp_path / "Labs" / "One.qmd").read_text(encoding="utf-8")
        assert "[lab one](../Labs/One.qmd#goal)" in welcome
        assert "![logo](assets/logo.png)" in welcome
        assert "[welcome](../Intro/Welcome.qmd)" in one
        assert "[/x](/Labs/Two.qmd)" in one

    def test_sync_map_and_snapshots_keep_their_canvas_ids(self, tmp_path):
        _nn_course(tmp_path)

        cb.migrate(str(tmp_path))

        m = load_sync_map(str(tmp_path))
        assert m["Labs/One.qmd"] == {"id": 5}
        assert m["Intro/assets/logo.png"] == {"id": 9}
        assert "02_Labs/01_One.qmd" not in m
        assert (tmp_path / ".canvas_snapshots" / "Labs__One.qmd.html").exists()

    def test_ignored_content_is_kept_out_with_an_underscore(self, tmp_path):
        """Unprefixed content was ignored; under order files it would join."""
        _nn_course(tmp_path)
        _page(tmp_path / "02_Labs" / "draft.qmd", "Draft")
        (tmp_path / "02_Labs" / "description.md").write_text("plain", encoding="utf-8")

        report = cb.migrate(str(tmp_path))

        assert ("02_Labs/draft.qmd", "Labs/_draft.qmd") in report["hidden"]
        assert (tmp_path / "Labs" / "_draft.qmd").exists()
        # A plain description file declares nothing and stays as it is.
        assert (tmp_path / "Labs" / "description.md").exists()
        assert cl.load_layout(str(tmp_path)).module("Labs").items == ["One.qmd", "Two.qmd"]

    def test_a_taken_name_keeps_its_prefix(self, tmp_path):
        _page(tmp_path / "01_Labs" / "01_Intro.qmd", "A")
        _page(tmp_path / "01_Labs" / "02_Intro.qmd", "B")

        report = cb.migrate(str(tmp_path))

        assert ("01_Labs/02_Intro.qmd", "'Intro.qmd' already exists") in report["kept"]
        assert cl.load_layout(str(tmp_path)).module("Labs").items == ["Intro.qmd", "02_Intro.qmd"]

    def test_marks_order_files_union_merge(self, tmp_path):
        _nn_course(tmp_path)
        cb.migrate(str(tmp_path))
        assert "_order.yml merge=union" in (tmp_path / ".gitattributes").read_text(encoding="utf-8")

    def test_refuses_an_ordered_course(self, tmp_path):
        (tmp_path / "_modules.yml").write_text("- Labs\n", encoding="utf-8")
        with pytest.raises(cb.BoardError, match="already"):
            cb.migrate(str(tmp_path))

    def test_refuses_uncommitted_changes(self, tmp_path):
        _nn_course(tmp_path)
        subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
        with pytest.raises(cb.BoardError, match="uncommitted"):
            cb.migrate(str(tmp_path))
        cb.migrate(str(tmp_path), allow_dirty=True)
        assert cl.is_ordered(str(tmp_path))


def _ordered_course(root):
    _page(root / "Intro" / "Welcome.qmd", "Welcome", "See [one](../Labs/One.qmd).")
    _page(root / "Labs" / "One.qmd", "One", "Picture ![p](pic.png) and [two](Two.qmd).")
    _page(root / "Labs" / "Two.qmd", "Two")
    (root / "Labs" / "pic.png").write_bytes(b"png")
    (root / "Labs" / "notes.pdf").write_bytes(b"%PDF")
    cl.write_modules(str(root), [("Intro", None), ("Labs", None)])
    save_sync_map(str(root), {"Labs/One.qmd": {"id": 5}})


class TestBoardOps:

    def test_reorder_within_a_module(self, tmp_path):
        _ordered_course(tmp_path)
        cb.apply_op(str(tmp_path), {"op": "move-item", "path": "Labs/Two.qmd", "to_dir": "Labs", "index": 0})
        assert cl.load_layout(str(tmp_path)).module("Labs").items == ["Two.qmd", "One.qmd"]

    def test_move_to_another_module_carries_links_and_map(self, tmp_path):
        _ordered_course(tmp_path)

        out = cb.apply_op(str(tmp_path), {"op": "move-item", "path": "Labs/One.qmd", "to_dir": "Intro", "index": 0})

        assert out["path"] == "Intro/One.qmd"
        layout = cl.load_layout(str(tmp_path))
        assert layout.module("Intro").items == ["One.qmd", "Welcome.qmd"]
        assert layout.module("Labs").items == ["Two.qmd"]
        assert load_sync_map(str(tmp_path)) == {"Intro/One.qmd": {"id": 5}}
        welcome = (tmp_path / "Intro" / "Welcome.qmd").read_text(encoding="utf-8")
        assert "[one](One.qmd)" in welcome
        # Its own relative links still reach what they reached before.
        moved = (tmp_path / "Intro" / "One.qmd").read_text(encoding="utf-8")
        assert "![p](../Labs/pic.png)" in moved and "[two](../Labs/Two.qmd)" in moved

    def test_move_module(self, tmp_path):
        _ordered_course(tmp_path)
        cb.apply_op(str(tmp_path), {"op": "move-module", "dir": "Labs", "index": 0})
        assert [m.dir for m in cl.load_layout(str(tmp_path)).modules] == ["Labs", "Intro"]

    def test_include_and_exclude_an_attachment(self, tmp_path):
        _ordered_course(tmp_path)
        cb.apply_op(str(tmp_path), {"op": "include-item", "path": "Labs/notes.pdf"})
        assert cl.load_layout(str(tmp_path)).module("Labs").items[-1] == "notes.pdf"
        cb.apply_op(str(tmp_path), {"op": "exclude-item", "path": "Labs/notes.pdf"})
        assert "notes.pdf" in cl.load_layout(str(tmp_path)).module("Labs").unlisted

    def test_add_and_remove_a_module(self, tmp_path):
        _ordered_course(tmp_path)
        _page(tmp_path / "Extra" / "X.qmd")
        cb.apply_op(str(tmp_path), {"op": "add-module", "dir": "Extra"})
        assert [m.dir for m in cl.load_layout(str(tmp_path)).modules] == ["Intro", "Labs", "Extra"]
        cb.apply_op(str(tmp_path), {"op": "remove-module", "dir": "Extra"})
        assert (tmp_path / "Extra" / "X.qmd").exists()
        assert cl.load_layout(str(tmp_path)).unlisted_dirs == ["Extra"]

    def test_ops_need_order_files(self, tmp_path):
        _page(tmp_path / "01_Labs" / "01_A.qmd")
        with pytest.raises(cb.BoardError, match="NN_"):
            cb.apply_op(str(tmp_path), {"op": "move-module", "dir": "01_Labs", "index": 0})

    def test_cli_reports_errors_as_json(self, tmp_path, capsys, monkeypatch):
        _ordered_course(tmp_path)
        monkeypatch.setattr("sys.argv", ["course_board.py", str(tmp_path), "op",
                                         json.dumps({"op": "move-item", "path": "Labs/Nope.qmd", "to_dir": "Intro"})])
        assert cb.main() == 1
        line = capsys.readouterr().out.strip()
        assert line.startswith("BOARD_RESULT_JSON:")
        assert json.loads(line.split(":", 1)[1]) == {"success": False, "error": "no file Labs/Nope.qmd"}
