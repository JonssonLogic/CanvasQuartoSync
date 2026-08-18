"""Tests for QuizHandler._update_backing_assignment().

Canvas keeps "do not count towards the final grade" on the assignment behind a
graded classic quiz, and the classic Quizzes API has no field for it — so the
setting goes through the Assignments API. Practice quizzes and ungraded surveys
have no backing assignment at all.
"""

from unittest.mock import MagicMock

import pytest

from handlers.quiz_handler import QuizHandler


handler = QuizHandler()


def _course_and_quiz(assignment_id=77):
    course = MagicMock()
    quiz = MagicMock()
    quiz.id = 5
    quiz.assignment_id = assignment_id
    assignment = MagicMock()
    course.get_assignment.return_value = assignment
    return course, quiz, assignment


class TestGradedQuiz:

    def test_omit_true_is_sent(self):
        course, quiz, assignment = _course_and_quiz()

        handler._update_backing_assignment(course, quiz, {'omit_from_final_grade': True})

        course.get_assignment.assert_called_once_with(77)
        assignment.edit.assert_called_once_with(
            assignment={'omit_from_final_grade': True})

    def test_absent_key_sends_false(self):
        """Source of truth: dropping the key turns it back off."""
        course, quiz, assignment = _course_and_quiz()

        handler._update_backing_assignment(course, quiz, {})

        assignment.edit.assert_called_once_with(
            assignment={'omit_from_final_grade': False})

    def test_api_failure_is_swallowed(self, caplog):
        """The quiz itself already synced; this must not raise."""
        course, quiz, assignment = _course_and_quiz()
        assignment.edit.side_effect = Exception("boom")

        handler._update_backing_assignment(course, quiz, {'omit_from_final_grade': True})

        assert "Failed to update backing assignment" in caplog.text


class TestFirstSync:
    """On a first sync the local quiz object predates Canvas creating the assignment."""

    def test_rereads_the_quiz_for_its_assignment_id(self):
        course, quiz, assignment = _course_and_quiz(assignment_id=None)
        refreshed = MagicMock()
        refreshed.assignment_id = 88
        course.get_quiz.return_value = refreshed

        handler._update_backing_assignment(course, quiz, {'omit_from_final_grade': True})

        course.get_quiz.assert_called_once_with(5)
        course.get_assignment.assert_called_once_with(88)

    def test_reread_failure_is_swallowed(self):
        course, quiz, _ = _course_and_quiz(assignment_id=None)
        course.get_quiz.side_effect = Exception("gone")

        handler._update_backing_assignment(course, quiz, {})

        course.get_assignment.assert_not_called()


class TestUngradedQuiz:
    """No backing assignment: practice quizzes and surveys never reach the gradebook."""

    def test_no_api_call(self):
        course, quiz, _ = _course_and_quiz(assignment_id=None)
        course.get_quiz.return_value = MagicMock(assignment_id=None)

        handler._update_backing_assignment(course, quiz, {})

        course.get_assignment.assert_not_called()

    def test_warns_only_when_the_setting_was_requested(self, caplog):
        course, quiz, _ = _course_and_quiz(assignment_id=None)
        course.get_quiz.return_value = MagicMock(assignment_id=None)

        handler._update_backing_assignment(course, quiz, {'omit_from_final_grade': True})

        assert "omit_from_final_grade needs a graded quiz" in caplog.text

    def test_silent_when_not_requested(self, caplog):
        course, quiz, _ = _course_and_quiz(assignment_id=None)
        course.get_quiz.return_value = MagicMock(assignment_id=None)

        handler._update_backing_assignment(course, quiz, {})

        assert "omit_from_final_grade" not in caplog.text
