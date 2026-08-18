"""Tests for handlers/gradebook.py — the omit/hide rules Canvas enforces.

These are pure functions on purpose: the rules are decided offline, before
anything is sent, because a rejected hide_in_gradebook takes the whole request
with it. That means no API mocks here.
"""

import pytest

from handlers.gradebook import needs_unhide, resolve_gradebook_settings


# --- omit_from_final_grade --------------------------------------------------

class TestOmitFromFinalGrade:

    def test_absent_sends_nothing(self):
        """The caller's own default wins when the file doesn't mention it."""
        assert resolve_gradebook_settings({}, 0) == {}

    def test_true_passes_through(self):
        assert resolve_gradebook_settings({'omit_from_final_grade': True}, 0) == {
            'omit_from_final_grade': True}

    def test_false_passes_through(self):
        """An explicit false is how an author turns it back off."""
        assert resolve_gradebook_settings({'omit_from_final_grade': False}, 0) == {
            'omit_from_final_grade': False}


# --- hide_in_gradebook ------------------------------------------------------

class TestHideInGradebook:

    @pytest.mark.parametrize("points", [0, None, 0.0])
    def test_zero_points_hides_and_forces_omit(self, points):
        """Canvas requires both together, so requesting one implies the other."""
        assert resolve_gradebook_settings({'hide_in_gradebook': True}, points) == {
            'omit_from_final_grade': True,
            'hide_in_gradebook': True,
        }

    def test_overrides_an_explicit_omit_false(self):
        settings = resolve_gradebook_settings(
            {'hide_in_gradebook': True, 'omit_from_final_grade': False}, 0)
        assert settings['omit_from_final_grade'] is True

    def test_with_points_is_dropped_not_sent(self):
        """Canvas would reject the whole request, so the flag is left out."""
        assert resolve_gradebook_settings({'hide_in_gradebook': True, 'points': 10}, 10) == {}

    def test_with_points_keeps_an_explicit_omit(self):
        """Dropping hide must not also drop what the author asked for directly."""
        settings = resolve_gradebook_settings(
            {'hide_in_gradebook': True, 'omit_from_final_grade': True}, 10)
        assert settings == {'omit_from_final_grade': True}

    def test_with_points_warns(self, caplog):
        resolve_gradebook_settings({'hide_in_gradebook': True}, 10, label="02_Reflection.qmd")
        assert "points to be 0" in caplog.text
        assert "02_Reflection.qmd" in caplog.text

    def test_false_sends_nothing(self):
        """Nothing to say when Canvas already has it visible; see needs_unhide."""
        assert resolve_gradebook_settings({'hide_in_gradebook': False}, 0) == {}


# --- un-hiding --------------------------------------------------------------

class TestNeedsUnhide:

    def test_hidden_on_canvas_and_key_removed(self):
        assert needs_unhide({}, current_hidden=True) is True

    def test_hidden_on_canvas_and_key_false(self):
        assert needs_unhide({'hide_in_gradebook': False}, current_hidden=True) is True

    def test_still_requested(self):
        assert needs_unhide({'hide_in_gradebook': True}, current_hidden=True) is False

    def test_not_hidden_on_canvas(self):
        """No point telling Canvas to leave a visible thing visible."""
        assert needs_unhide({}, current_hidden=False) is False

    def test_missing_attribute_defaults_to_visible(self):
        assert needs_unhide({}, current_hidden=None) is False
