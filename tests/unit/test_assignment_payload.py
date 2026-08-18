"""Tests for the assignment payload AssignmentHandler sends to Canvas.

Focused on the gradebook settings, which are the part with server-side rules:
Canvas refuses hide_in_gradebook unless the assignment is also omitted from the
final grade and worth 0 points.

Quarto rendering is patched out — nothing here needs real HTML.
"""

import os
from unittest.mock import MagicMock, patch

import pytest

from handlers.assignment_handler import AssignmentHandler


handler = AssignmentHandler()


def _write(tmp_path, canvas_block):
    path = tmp_path / "01_Module" / "01_Task.qmd"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\ntitle: \"Task\"\ncanvas:\n  type: assignment\n{canvas_block}---\n\nBody\n",
        encoding="utf-8")
    return str(path)


def _sync(file_path, existing=None):
    """Run a sync against mocks and return the payload Canvas was sent.

    ``existing`` is a mock assignment found by title search, i.e. the update
    path; without it the handler takes the create path.
    """
    course = MagicMock()
    course.time_zone = "Europe/Stockholm"   # a Mock here isn't a loadable zone
    course.get_assignments.return_value = [existing] if existing else []
    created = MagicMock()
    created.id = 999
    course.create_assignment.return_value = created

    with patch.object(AssignmentHandler, 'render_quarto_document', return_value="<p>Body</p>"), \
            patch('handlers.assignment_handler.process_content', side_effect=lambda c, *a, **k: c):
        handler.sync(file_path, course, module=None, content_root=None)

    # The first call carries the assignment itself; a later one is the un-hide.
    call = (existing.edit.call_args_list[0] if existing
            else course.create_assignment.call_args_list[0])
    return call[1]['assignment'], (existing or created)


def _existing(name="Task", hidden=False):
    a = MagicMock()
    a.name = name
    a.id = 42
    a.hide_in_gradebook = hidden
    return a


class TestOmitFromFinalGrade:

    def test_absent_sends_false(self, tmp_path):
        """Source of truth: dropping the key turns it back off in Canvas."""
        payload, _ = _sync(_write(tmp_path, ""))
        assert payload['omit_from_final_grade'] is False

    def test_true_is_sent(self, tmp_path):
        payload, _ = _sync(_write(tmp_path, "  omit_from_final_grade: true\n"))
        assert payload['omit_from_final_grade'] is True


class TestHideInGradebook:

    def test_zero_points_hides_and_forces_omit(self, tmp_path):
        payload, _ = _sync(_write(tmp_path, "  points: 0\n  hide_in_gradebook: true\n"))
        assert payload['hide_in_gradebook'] is True
        assert payload['omit_from_final_grade'] is True

    def test_points_unset_counts_as_zero(self, tmp_path):
        payload, _ = _sync(_write(tmp_path, "  hide_in_gradebook: true\n"))
        assert payload['hide_in_gradebook'] is True

    def test_with_points_is_dropped(self, tmp_path):
        """Canvas would reject the create outright, leaving nothing behind."""
        payload, _ = _sync(_write(tmp_path, "  points: 10\n  hide_in_gradebook: true\n"))
        assert 'hide_in_gradebook' not in payload
        assert payload['points_possible'] == 10

    def test_false_is_not_sent(self, tmp_path):
        payload, _ = _sync(_write(tmp_path, "  hide_in_gradebook: false\n"))
        assert 'hide_in_gradebook' not in payload

    def test_not_sent_on_a_plain_assignment(self, tmp_path):
        payload, _ = _sync(_write(tmp_path, "  points: 10\n"))
        assert 'hide_in_gradebook' not in payload


class TestUnhide:
    """Removing the key puts the column back, in the same request."""

    def test_removing_the_key_restores_the_column(self, tmp_path):
        existing = _existing(hidden=True)
        payload, obj = _sync(_write(tmp_path, "  points: 0\n"), existing=existing)

        assert payload['hide_in_gradebook'] is False
        obj.edit.assert_called_once()

    def test_no_unhide_when_still_requested(self, tmp_path):
        existing = _existing(hidden=True)
        payload, obj = _sync(_write(tmp_path, "  points: 0\n  hide_in_gradebook: true\n"),
                             existing=existing)

        assert payload['hide_in_gradebook'] is True
        obj.edit.assert_called_once()

    def test_no_unhide_when_already_visible(self, tmp_path):
        existing = _existing(hidden=False)
        payload, obj = _sync(_write(tmp_path, "  points: 0\n"), existing=existing)

        assert 'hide_in_gradebook' not in payload
        obj.edit.assert_called_once()
