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


class TestQuizTitlesComeFromCanvasBlock:
    """Quiz handlers read canvas.title and ignore the top-level title:."""

    def test_classic_quiz_uses_canvas_title(self, tmp_path):
        f = tmp_path / "02_Concept.qmd"
        f.write_text("---\ncanvas:\n  type: quiz\n  title: Beam Quiz\n---\n", encoding="utf-8")
        assert expected_canvas_title(str(f)) == "Beam Quiz"

    def test_new_quiz_uses_canvas_title(self, tmp_path):
        f = tmp_path / "02_Concept.qmd"
        f.write_text("---\ncanvas:\n  type: new_quiz\n  title: Beam Quiz\n---\n", encoding="utf-8")
        assert expected_canvas_title(str(f)) == "Beam Quiz"

    def test_canvas_title_wins_over_top_level_title(self, tmp_path):
        f = tmp_path / "02_Concept.qmd"
        f.write_text(
            "---\ntitle: Ignored\ncanvas:\n  type: new_quiz\n  title: Beam Quiz\n---\n",
            encoding="utf-8")
        assert expected_canvas_title(str(f)) == "Beam Quiz"

    def test_top_level_title_alone_is_ignored_for_quizzes(self, tmp_path):
        f = tmp_path / "02_Concept.qmd"
        f.write_text("---\ntitle: Ignored\ncanvas:\n  type: quiz\n---\n", encoding="utf-8")
        assert expected_canvas_title(str(f)) == "Concept"

    def test_structurally_detected_quiz_uses_canvas_title(self, tmp_path):
        """A classic quiz may carry no canvas.type at all."""
        f = tmp_path / "02_Pop.qmd"
        f.write_text(
            "---\ncanvas:\n  title: Pop Quiz\n---\n"
            ":::: {.question name=\"Q1\"}\nText\n\n- [x] Yes\n::::\n",
            encoding="utf-8")
        assert expected_canvas_title(str(f)) == "Pop Quiz"

    def test_study_guide_filename_wins_over_quiz_type(self, tmp_path):
        """StudyGuideHandler is first in the chain and titles from the top level."""
        f = tmp_path / "02_KursPM.qmd"
        f.write_text(
            "---\ntitle: Course PM\ncanvas:\n  type: quiz\n  title: Ignored\n---\n",
            encoding="utf-8")
        assert expected_canvas_title(str(f)) == "Course PM"

    def test_json_new_quiz_uses_canvas_title(self, tmp_path):
        f = tmp_path / "04_Quiz.json"
        f.write_text('{"canvas": {"quiz_engine": "new", "title": "JSON Quiz"}}',
                     encoding="utf-8")
        assert expected_canvas_title(str(f)) == "JSON Quiz"


class TestUnclaimedFilesKeepTheirExtension:
    """No handler claims these, so they upload as solo assets."""

    def test_qmd_without_canvas_metadata(self, tmp_path):
        f = tmp_path / "02_Template.qmd"
        f.write_text("---\ntitle: A Template\n---\nBody\n", encoding="utf-8")
        assert expected_canvas_title(str(f)) == "Template.qmd"

    def test_qmd_with_unknown_canvas_type(self, tmp_path):
        f = tmp_path / "02_Thing.qmd"
        f.write_text("---\ncanvas:\n  type: webinar\n---\n", encoding="utf-8")
        assert expected_canvas_title(str(f)) == "Thing.qmd"

    def test_json_that_is_not_a_quiz(self, tmp_path):
        f = tmp_path / "04_Data.json"
        f.write_text('{"rows": [1, 2, 3]}', encoding="utf-8")
        assert expected_canvas_title(str(f)) == "Data.json"


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

    def test_counts_a_quiz_sibling_titled_via_canvas_block(self, tmp_path):
        """Regression: a quiz sibling titled under canvas.title must still match.

        expected_canvas_title() used to read the top-level `title:` for every
        .qmd, so a quiz using the documented `canvas.title` never matched its
        module item and was not counted - pushing the new item too far up.
        """
        (tmp_path / "01_Intro.qmd").write_text(
            "---\ntitle: Intro\ncanvas:\n  type: page\n---\n", encoding="utf-8")
        (tmp_path / "02_Check.qmd").write_text(
            "---\ncanvas:\n  type: new_quiz\n  title: Knowledge Check\n---\n",
            encoding="utf-8")
        (tmp_path / "03_Lab.qmd").write_text(
            "---\ntitle: Lab\ncanvas:\n  type: assignment\n---\n", encoding="utf-8")

        module = _fake_module(["Intro", "Knowledge Check"])
        assert compute_insert_position(module, str(tmp_path), "03_Lab.qmd") == 3

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
