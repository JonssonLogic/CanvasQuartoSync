"""Tests for the offline content validator."""

import json
import os

import pytest

from validate_content import (
    CANVAS_SCHEMA,
    detect_kind,
    validate_file,
    validate_path,
)


def _write(tmp_path, relname, content):
    """Write a content file inside a module folder and return its path."""
    p = tmp_path / relname
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return str(p)


def _messages(report):
    return " | ".join(i.message for i in report.issues)


def _errors(report):
    return " | ".join(i.message for i in report.errors)


# --- Type detection ---------------------------------------------------------

class TestDetectKind:

    @pytest.mark.parametrize("canvas_type,expected", [
        ("page", "page"),
        ("assignment", "assignment"),
        ("subheader", "subheader"),
        ("new_quiz", "new_quiz"),
    ])
    def test_detects_declared_type(self, tmp_path, canvas_type, expected):
        path = _write(tmp_path, f"01_X.qmd", f"---\ncanvas:\n  type: {canvas_type}\n---\n")
        assert detect_kind(path) == expected

    def test_detects_classic_quiz_from_question_blocks(self, tmp_path):
        path = _write(tmp_path, "01_Q.qmd",
                      "---\ntitle: Q\n---\n:::: {.question name=\"A\"}\nText\n\n- [x] Yes\n::::\n")
        assert detect_kind(path) == "quiz"

    def test_unknown_type_is_unclaimed(self, tmp_path):
        path = _write(tmp_path, "01_X.qmd", "---\ncanvas:\n  type: webinar\n---\n")
        assert detect_kind(path) == "unclaimed"


# --- Naming -----------------------------------------------------------------

class TestNaming:

    def test_missing_nn_prefix_is_an_error(self, tmp_path):
        path = _write(tmp_path, "Welcome.qmd", "---\ncanvas:\n  type: page\n---\n")
        report = validate_file(path, str(tmp_path))
        assert "NN_ prefix" in _errors(report)

    def test_prefixed_file_passes(self, tmp_path):
        path = _write(tmp_path, "01_Welcome.qmd", "---\ncanvas:\n  type: page\n---\n")
        assert validate_file(path, str(tmp_path)).errors == []

    def test_unprefixed_parent_folder_is_an_error(self, tmp_path):
        path = _write(tmp_path, "drafts/01_Welcome.qmd", "---\ncanvas:\n  type: page\n---\n")
        report = validate_file(path, str(tmp_path))
        assert "parent folder" in _errors(report)

    def test_file_without_canvas_metadata_is_not_flagged(self, tmp_path):
        """Description files and templates legitimately have no prefix."""
        path = _write(tmp_path, "Quiz_Description.qmd", "---\ntitle: Intro\n---\nText")
        assert validate_file(path, str(tmp_path)).errors == []


# --- Frontmatter ------------------------------------------------------------

class TestFrontmatter:

    def test_unknown_key_warns_with_suggestion(self, tmp_path):
        path = _write(tmp_path, "01_P.qmd", "---\ncanvas:\n  type: page\n  publish: true\n---\n")
        report = validate_file(path, str(tmp_path))
        assert "Did you mean 'published'" in _messages(report)

    def test_invalid_date_is_an_error(self, tmp_path):
        path = _write(tmp_path, "01_A.qmd",
                      "---\ncanvas:\n  type: assignment\n  due_at: \"next tuesday\"\n---\n")
        assert "ISO 8601" in _errors(validate_file(path, str(tmp_path)))

    def test_iso_date_accepted(self, tmp_path):
        path = _write(tmp_path, "01_A.qmd",
                      "---\ncanvas:\n  type: assignment\n  due_at: 2026-03-15T23:59:00Z\n---\n")
        assert validate_file(path, str(tmp_path)).errors == []

    def test_invalid_enum_lists_valid_choices(self, tmp_path):
        path = _write(tmp_path, "01_A.qmd",
                      "---\ncanvas:\n  type: assignment\n  grading_type: stars\n---\n")
        errors = _errors(validate_file(path, str(tmp_path)))
        assert "stars" in errors and "pass_fail" in errors

    def test_indent_out_of_range(self, tmp_path):
        path = _write(tmp_path, "01_P.qmd", "---\ncanvas:\n  type: page\n  indent: 9\n---\n")
        assert "between 0 and 5" in _errors(validate_file(path, str(tmp_path)))

    def test_wrong_value_type(self, tmp_path):
        path = _write(tmp_path, "01_A.qmd",
                      "---\ncanvas:\n  type: assignment\n  points: \"ten\"\n---\n")
        assert "expected a number" in _errors(validate_file(path, str(tmp_path)))

    def test_external_url_requires_url(self, tmp_path):
        path = _write(tmp_path, "01_L.qmd", "---\ncanvas:\n  type: external_url\n---\n")
        assert "canvas.url" in _errors(validate_file(path, str(tmp_path)))

    def test_quiz_title_at_top_level_warns(self, tmp_path):
        path = _write(tmp_path, "01_Q.qmd",
                      "---\ntitle: My Quiz\ncanvas:\n  type: new_quiz\n---\n"
                      ":::: {.question name=\"A\"}\nT\n\n- [x] Yes\n::::\n")
        assert "canvas.title" in _messages(validate_file(path, str(tmp_path)))

    def test_hide_in_gradebook_with_points_is_an_error(self, tmp_path):
        path = _write(tmp_path, "01_Q.qmd",
                      "---\ncanvas:\n  type: new_quiz\n  title: Q\n  points: 5\n"
                      "  hide_in_gradebook: true\n---\n"
                      ":::: {.question name=\"A\"}\nT\n\n- [x] Yes\n::::\n")
        assert "hide_in_gradebook" in _errors(validate_file(path, str(tmp_path)))

    def test_nested_result_view_keys_checked(self, tmp_path):
        path = _write(tmp_path, "01_Q.qmd",
                      "---\ncanvas:\n  type: new_quiz\n  title: Q\n  result_view:\n"
                      "    show_responses_frequency: sometimes\n---\n"
                      ":::: {.question name=\"A\"}\nT\n\n- [x] Yes\n::::\n")
        assert "show_responses_frequency" in _errors(validate_file(path, str(tmp_path)))


# --- Quizzes ----------------------------------------------------------------

class TestQuizzes:

    def test_mixed_answer_styles_is_an_error(self, tmp_path):
        path = _write(tmp_path, "01_Q.qmd",
                      "---\ncanvas:\n  type: new_quiz\n  title: Q\n---\n"
                      ":::: {.question name=\"A\"}\nText\n\n- [x] Yes\n\n"
                      "::: {.answer correct=true}\nAlso yes\n:::\n::::\n")
        assert "mixes checklist answers" in _errors(validate_file(path, str(tmp_path)))

    def test_numeric_question_on_classic_engine_is_an_error(self, tmp_path):
        path = _write(tmp_path, "01_Q.qmd",
                      "---\ncanvas:\n  type: quiz\n  title: Q\n---\n"
                      ":::: {.question name=\"A\" type=\"numeric_question\"}\nValue?\n\n"
                      "::: {.answer value=\"5\"}\n:::\n::::\n")
        assert "New Quizzes engine" in _errors(validate_file(path, str(tmp_path)))

    def test_multiple_choice_needs_exactly_one_correct(self, tmp_path):
        path = _write(tmp_path, "01_Q.qmd",
                      "---\ncanvas:\n  type: new_quiz\n  title: Q\n---\n"
                      ":::: {.question name=\"A\"}\nText\n\n- [ ] No\n- [ ] Also no\n::::\n")
        assert "exactly one correct answer" in _errors(validate_file(path, str(tmp_path)))

    def test_quiz_with_no_questions_is_an_error(self, tmp_path):
        path = _write(tmp_path, "01_Q.qmd", "---\ncanvas:\n  type: new_quiz\n  title: Q\n---\n")
        assert "no questions" in _errors(validate_file(path, str(tmp_path)))

    def test_formula_divide_by_zero_is_caught(self, tmp_path):
        path = _write(tmp_path, "01_Q.qmd",
                      "---\ncanvas:\n  type: new_quiz\n  title: Q\n---\n"
                      ":::: {.question name=\"D\" type=\"formula_question\"}\n"
                      "Compute [A] over [B].\n\n"
                      "::: {.formula}\nformula: A / B\n:::\n\n"
                      "::: {.variable name=\"A\"}\nmin: 1\nmax: 10\n:::\n\n"
                      "::: {.variable name=\"B\"}\nmin: 0\nmax: 0\n:::\n::::\n")
        assert "does not evaluate" in _errors(validate_file(path, str(tmp_path)))

    def test_valid_formula_question_passes(self, tmp_path):
        path = _write(tmp_path, "01_Q.qmd",
                      "---\ncanvas:\n  type: new_quiz\n  title: Q\n---\n"
                      ":::: {.question name=\"S\" type=\"formula_question\"}\n"
                      "Compute [F] over [A].\n\n"
                      "::: {.formula}\nformula: F / A\nmargin: 2\n:::\n\n"
                      "::: {.variable name=\"F\"}\nmin: 10\nmax: 100\n:::\n\n"
                      "::: {.variable name=\"A\"}\nmin: 5\nmax: 50\n:::\n::::\n")
        assert validate_file(path, str(tmp_path)).errors == []

    def test_undeclared_placeholder_warns(self, tmp_path):
        path = _write(tmp_path, "01_Q.qmd",
                      "---\ncanvas:\n  type: new_quiz\n  title: Q\n---\n"
                      ":::: {.question name=\"S\" type=\"formula_question\"}\n"
                      "Compute [F] over [Z].\n\n"
                      "::: {.formula}\nformula: F / 2\n:::\n\n"
                      "::: {.variable name=\"F\"}\nmin: 10\nmax: 100\n:::\n::::\n")
        assert "[Z]" in _messages(validate_file(path, str(tmp_path)))

    def test_invalid_json_is_reported(self, tmp_path):
        path = _write(tmp_path, "01_Q.json", '{"canvas": {"quiz_engine": "new",}}')
        assert "invalid JSON" in _errors(validate_file(path, str(tmp_path)))

    def test_description_file_with_prefix_warns(self, tmp_path):
        _write(tmp_path, "01_Desc.qmd", "---\ntitle: D\n---\nIntro")
        path = _write(tmp_path, "02_Q.qmd",
                      "---\ncanvas:\n  type: quiz\n  title: Q\n"
                      "  description_file: 01_Desc.qmd\n---\n"
                      ":::: {.question name=\"A\"}\nT\n\n- [x] Yes\n::::\n")
        assert "NN_ prefix" in _messages(validate_file(path, str(tmp_path)))


# --- Links ------------------------------------------------------------------

class TestLinks:

    def test_missing_image_is_an_error(self, tmp_path):
        path = _write(tmp_path, "01_P.qmd",
                      "---\ncanvas:\n  type: page\n---\n![x](nope.png)\n")
        assert "image not found" in _errors(validate_file(path, str(tmp_path)))

    def test_existing_image_passes(self, tmp_path):
        (tmp_path / "pic.png").write_bytes(b"\x89PNG")
        path = _write(tmp_path, "01_P.qmd",
                      "---\ncanvas:\n  type: page\n---\n![x](pic.png)\n")
        assert validate_file(path, str(tmp_path)).errors == []

    def test_external_urls_ignored(self, tmp_path):
        path = _write(tmp_path, "01_P.qmd",
                      "---\ncanvas:\n  type: page\n---\n[x](https://example.com)\n")
        assert validate_file(path, str(tmp_path)).errors == []

    def test_links_inside_code_blocks_ignored(self, tmp_path):
        path = _write(tmp_path, "01_P.qmd",
                      "---\ncanvas:\n  type: page\n---\n```\n![x](nope.png)\n```\n")
        assert validate_file(path, str(tmp_path)).errors == []

    def test_cross_link_without_canvas_metadata_warns(self, tmp_path):
        _write(tmp_path, "01_Target.qmd", "---\ntitle: T\n---\nBody")
        path = _write(tmp_path, "02_P.qmd",
                      "---\ncanvas:\n  type: page\n---\n[go](01_Target.qmd)\n")
        assert "downloadable file" in _messages(validate_file(path, str(tmp_path)))


# --- Walking ----------------------------------------------------------------

class TestValidatePath:

    def test_directory_walk_skips_ignored_dirs(self, tmp_path):
        _write(tmp_path, "01_Mod/01_P.qmd", "---\ncanvas:\n  type: page\n---\n")
        _write(tmp_path, ".claude/skills/canvas-content/SKILL.md", "---\nname: x\n---\n")
        _write(tmp_path, "CLAUDE.md", "# notes")
        reports = validate_path(str(tmp_path))
        assert [os.path.basename(r.path) for r in reports] == ["01_P.qmd"]

    def test_single_file_accepted(self, tmp_path):
        path = _write(tmp_path, "01_Mod/01_P.qmd", "---\ncanvas:\n  type: page\n---\n")
        assert len(validate_path(path)) == 1


# --- Real content -----------------------------------------------------------

def test_shipped_fixture_content_is_clean(fixtures_dir):
    """The E2E fixture syncs correctly, so the validator must not flag it."""
    reports = validate_path(os.path.join(fixtures_dir, "e2e_content"))
    problems = {os.path.basename(r.path): _errors(r) for r in reports if r.errors}
    assert problems == {}


def test_example_content_is_clean():
    """Example/ is what new users copy from - it must validate cleanly."""
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    reports = validate_path(os.path.join(root, "Example"))
    problems = {os.path.basename(r.path): _errors(r) for r in reports if r.errors}
    assert problems == {}
