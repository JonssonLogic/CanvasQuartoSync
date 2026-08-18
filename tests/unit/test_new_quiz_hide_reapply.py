"""The backing assignment is settled twice when a New Quiz asks to be hidden.

Canvas validates hide_in_gradebook against the assignment's *current* points,
not the ones the file declares. For a New Quiz those only settle after the quiz
and its items have synced, so a quiz moving from graded to 0-point-and-hidden in
a single edit is refused on the first pass. Because an unchanged mtime skips the
whole sync next time, a missed re-apply would never be retried.
"""

from unittest.mock import MagicMock, patch

import pytest

from handlers.new_quiz_handler import NewQuizHandler


handler = NewQuizHandler()

_QUIZ = (
    "---\ncanvas:\n  type: new_quiz\n  title: \"Self Check\"\n"
    "  points: 0\n{extra}---\n\n"
    ":::: {{.question name=\"Q1\" points_possible=\"0\"}}\n"
    "Hidden?\n\n- [x] Yes\n::::\n"
)


def _write(tmp_path, extra=""):
    path = tmp_path / "01_Module" / "01_Check.qmd"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_QUIZ.format(extra=extra), encoding="utf-8")
    return str(path)


def _run(file_path):
    """Sync against mocks and return how often the backing assignment was settled."""
    course = MagicMock()
    course.id = 1
    course.time_zone = "Europe/Stockholm"

    client = MagicMock()
    client.create_quiz.return_value = {'id': 555}

    with patch('handlers.new_quiz_handler.NewQuizAPIClient', return_value=client), \
            patch('handlers.new_quiz_handler.process_content', side_effect=lambda c, *a, **k: c), \
            patch.object(NewQuizHandler, '_render_qmd_questions', side_effect=lambda q, *a: q), \
            patch.object(NewQuizHandler, '_sync_questions'), \
            patch.object(NewQuizHandler, '_update_backing_assignment') as settle:
        handler.sync(file_path, course, module=None, content_root=None)

    return settle


def test_hidden_quiz_is_settled_again_after_items_sync(tmp_path):
    settle = _run(_write(tmp_path, "  hide_in_gradebook: true\n"))
    assert settle.call_count == 2, (
        "a hidden New Quiz must be re-settled once its items exist, or the hide "
        "is lost whenever Canvas still holds the old points"
    )


def test_ordinary_quiz_is_settled_once(tmp_path):
    """No constraint to re-check, so no extra API call."""
    settle = _run(_write(tmp_path, "  omit_from_final_grade: true\n"))
    assert settle.call_count == 1


def test_reapply_happens_after_the_questions(tmp_path):
    """Ordering is the whole point: before the items, the points are still stale."""
    calls = []
    course = MagicMock()
    course.id = 1
    course.time_zone = "Europe/Stockholm"
    client = MagicMock()
    client.create_quiz.return_value = {'id': 555}

    with patch('handlers.new_quiz_handler.NewQuizAPIClient', return_value=client), \
            patch('handlers.new_quiz_handler.process_content', side_effect=lambda c, *a, **k: c), \
            patch.object(NewQuizHandler, '_render_qmd_questions', side_effect=lambda q, *a: q), \
            patch.object(NewQuizHandler, '_sync_questions',
                         side_effect=lambda *a, **k: calls.append('questions')), \
            patch.object(NewQuizHandler, '_update_backing_assignment',
                         side_effect=lambda *a, **k: calls.append('settle')):
        handler.sync(_write(tmp_path, "  hide_in_gradebook: true\n"),
                     course, module=None, content_root=None)

    assert calls == ['settle', 'questions', 'settle'], calls
