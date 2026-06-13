"""Unit tests for handlers/single_sync.py and expected_canvas_title()."""

import os
from types import SimpleNamespace

from handlers.content_utils import expected_canvas_title
from handlers.single_sync import compute_insert_position, build_handlers


# --- expected_canvas_title ---

class TestExpectedCanvasTitle:

    def test_qmd_explicit_frontmatter_title(self, tmp_path):
        f = tmp_path / "02_Welcome.qmd"
        f.write_text("---\ntitle: My Welcome\ncanvas:\n  type: page\n---\nBody\n", encoding="utf-8")
        assert expected_canvas_title(str(f)) == "My Welcome"

    def test_qmd_default_title_from_stem(self, tmp_path):
        f = tmp_path / "02_Welcome.qmd"
        f.write_text("---\ncanvas:\n  type: page\n---\nBody\n", encoding="utf-8")
        assert expected_canvas_title(str(f)) == "Welcome"

    def test_md_default_title_from_stem(self, tmp_path):
        f = tmp_path / "03_Resources.md"
        f.write_text("---\ncanvas:\n  type: subheader\n---\n", encoding="utf-8")
        assert expected_canvas_title(str(f)) == "Resources"

    def test_json_canvas_title(self, tmp_path):
        f = tmp_path / "04_Quiz.json"
        f.write_text('{"canvas": {"title": "Final Quiz"}, "questions": []}', encoding="utf-8")
        assert expected_canvas_title(str(f)) == "Final Quiz"

    def test_json_default_title_from_stem(self, tmp_path):
        f = tmp_path / "04_Quiz.json"
        f.write_text('{"questions": []}', encoding="utf-8")
        assert expected_canvas_title(str(f)) == "Quiz"

    def test_solo_asset_keeps_extension(self, tmp_path):
        f = tmp_path / "05_Syllabus.pdf"
        f.write_bytes(b"%PDF-1.4")
        assert expected_canvas_title(str(f)) == "Syllabus.pdf"

    def test_malformed_json_falls_back_to_stem(self, tmp_path):
        f = tmp_path / "06_Broken.json"
        f.write_text("{not valid json", encoding="utf-8")
        assert expected_canvas_title(str(f)) == "Broken"


# --- compute_insert_position ---

def _fake_module(items):
    """Module whose get_module_items() returns objects with .title."""
    objs = [SimpleNamespace(title=t) for t in items]
    return SimpleNamespace(get_module_items=lambda: objs)


def _make_module_dir(tmp_path, names):
    for name in names:
        (tmp_path / name).write_text(
            "---\ncanvas:\n  type: page\n---\n", encoding="utf-8"
        )
    return str(tmp_path)


class TestComputeInsertPosition:

    def test_first_file_goes_to_position_one(self, tmp_path):
        module_dir = _make_module_dir(tmp_path, ["01_A.qmd", "02_B.qmd", "03_C.qmd"])
        # No siblings present yet in the module.
        module = _fake_module([])
        assert compute_insert_position(module, module_dir, "01_A.qmd") == 1

    def test_middle_file_after_one_present_sibling(self, tmp_path):
        module_dir = _make_module_dir(tmp_path, ["01_A.qmd", "02_B.qmd", "03_C.qmd"])
        # A is already present; B should land at position 2.
        module = _fake_module(["A"])
        assert compute_insert_position(module, module_dir, "02_B.qmd") == 2

    def test_last_file_after_two_present_siblings(self, tmp_path):
        module_dir = _make_module_dir(tmp_path, ["01_A.qmd", "02_B.qmd", "03_C.qmd"])
        module = _fake_module(["A", "B"])
        assert compute_insert_position(module, module_dir, "03_C.qmd") == 3

    def test_only_later_sibling_present_inserts_first(self, tmp_path):
        """A later sibling (C) is present but no earlier ones — B still goes first."""
        module_dir = _make_module_dir(tmp_path, ["01_A.qmd", "02_B.qmd", "03_C.qmd"])
        module = _fake_module(["C"])
        assert compute_insert_position(module, module_dir, "02_B.qmd") == 1

    def test_ignores_non_prefixed_and_dirs(self, tmp_path):
        (tmp_path / "graphics").mkdir()
        (tmp_path / "notes.txt").write_text("x", encoding="utf-8")
        module_dir = _make_module_dir(tmp_path, ["01_A.qmd", "02_B.qmd"])
        module = _fake_module(["A"])
        # 'graphics' dir and 'notes.txt' are not syncable siblings.
        assert compute_insert_position(module, module_dir, "02_B.qmd") == 2


# --- build_handlers ---

class TestBuildHandlers:

    def test_returns_handler_chain_in_order(self):
        from handlers.study_guide_handler import StudyGuideHandler
        from handlers.page_handler import PageHandler
        from handlers.subheader_handler import SubHeaderHandler

        handlers = build_handlers()
        assert isinstance(handlers[0], StudyGuideHandler)
        assert isinstance(handlers[1], PageHandler)
        assert isinstance(handlers[-1], SubHeaderHandler)
