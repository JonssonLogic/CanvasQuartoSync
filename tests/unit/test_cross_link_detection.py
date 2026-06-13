"""Unit tests for resolve_cross_link target-type detection.

Regression coverage for two bugs the E2E fixture exposed:
  - A classic quiz (.qmd with `canvas.quiz_type` / question blocks but no
    `canvas.type`) must resolve to a *quiz*, not a stub *page*.
  - A plain page still resolves to a page.
"""

from unittest.mock import MagicMock

from handlers.content_utils import resolve_cross_link


def _course():
    c = MagicMock()
    c.get_pages.return_value = []
    c.get_quizzes.return_value = []
    c.get_assignments.return_value = []
    c.create_page.return_value = MagicMock(html_url="http://canvas/page")
    c.create_quiz.return_value = MagicMock(html_url="http://canvas/quiz")
    c.create_assignment.return_value = MagicMock(html_url="http://canvas/assignment")
    return c


def _write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


class TestClassicQuizDetection:

    def test_quiz_type_metadata_resolves_to_quiz(self, tmp_path):
        _write(tmp_path, "07_Quiz.qmd",
               "---\ncanvas:\n  title: My Quiz\n  quiz_type: assignment\n---\n")
        course = _course()
        url = resolve_cross_link(course, str(tmp_path / "x"), "07_Quiz.qmd", str(tmp_path))
        course.create_quiz.assert_called_once()
        course.create_page.assert_not_called()
        assert url == "http://canvas/quiz"

    def test_question_blocks_resolve_to_quiz(self, tmp_path):
        _write(tmp_path, "08_Quiz.qmd",
               "---\ncanvas:\n  title: Block Quiz\n---\n\n"
               ":::: {.question name=\"Q1\"}\nWhat?\n- [x] A\n- [ ] B\n::::\n")
        course = _course()
        resolve_cross_link(course, str(tmp_path / "x"), "08_Quiz.qmd", str(tmp_path))
        course.create_quiz.assert_called_once()
        course.create_page.assert_not_called()


class TestPageDetection:

    def test_plain_page_resolves_to_page(self, tmp_path):
        _write(tmp_path, "01_Page.qmd",
               "---\ntitle: Welcome\ncanvas:\n  type: page\n---\nbody\n")
        course = _course()
        url = resolve_cross_link(course, str(tmp_path / "x"), "01_Page.qmd", str(tmp_path))
        course.create_page.assert_called_once()
        course.create_quiz.assert_not_called()
        assert url == "http://canvas/page"

    def test_page_without_type_or_quiz_markers_resolves_to_page(self, tmp_path):
        _write(tmp_path, "02_Notes.qmd",
               "---\ncanvas:\n  title: Notes\n---\njust prose, no questions\n")
        course = _course()
        resolve_cross_link(course, str(tmp_path / "x"), "02_Notes.qmd", str(tmp_path))
        course.create_page.assert_called_once()
        course.create_quiz.assert_not_called()
