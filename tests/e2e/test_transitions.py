"""End-to-end: changing gradebook visibility on an *already synced* New Quiz.

The main suite purges the course and syncs with --force, so everything it checks
was created from scratch. Gradebook visibility is the one setting where the
update path can fail while the create path succeeds: Canvas validates
hide_in_gradebook against the assignment's **current** points_possible, and for a
New Quiz those only settle after the quiz and its items have synced. A quiz going
from graded to 0-point-and-hidden in a single edit is therefore refused on the
first attempt — and since an unchanged mtime skips the file entirely next run, a
missed retry would never heal.

This walks the Self-Check quiz out of the gradebook and back in, re-syncing
between each step. The fixture file is restored on the way out whatever happens.

Runs after test_full_sync.py (alphabetical), and leaves the course in the state
that suite expects.
"""

import os
import re

import pytest

from tests.e2e.canvas_helpers import run_sync
from tests.e2e.conftest import E2E_CONTENT_DIR

pytestmark = pytest.mark.canvas

FIXTURE = os.path.join(E2E_CONTENT_DIR, "03_Beam Bending", "04_Self_Check.qmd")


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _write(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def _self_check(course):
    return next((a for a in course.get_assignments()
                 if "Self-Check" in (a.name or "")), None)


@pytest.fixture(scope="module")
def restore_fixture():
    """Put the fixture file back however the test ends."""
    original = _read(FIXTURE)
    yield original
    _write(FIXTURE, original)


@pytest.fixture(scope="module")
def graded_then_hidden(canvas_course, e2e_credentials, restore_fixture):
    """Make the quiz graded and visible, then hidden again, syncing each time.

    Returns the assignment as Canvas held it after each step.
    """
    original = restore_fixture

    def sync():
        result = run_sync(
            E2E_CONTENT_DIR,
            e2e_credentials["course_id"],
            e2e_credentials["api_url"],
            e2e_credentials["api_token"],
        )
        assert result.returncode == 0, f"Sync failed:\n{result.stderr}"

    # Step 1 — graded and visible: drop the hide, give the quiz and its items points.
    graded = original.replace("  hide_in_gradebook: true\n", "")
    graded = graded.replace("  points: 0\n", "  points: 10\n")
    graded = graded.replace('points_possible="0"', 'points_possible="5"')
    _write(FIXTURE, graded)
    sync()
    after_graded = _self_check(canvas_course)

    # Step 2 — back to 0 points and hidden, in a single edit. This is the case
    # that fails if the backing assignment is only settled before the items sync.
    _write(FIXTURE, original)
    sync()
    after_hidden = _self_check(canvas_course)

    return after_graded, after_hidden


class TestUnhideOnUpdate:
    """true -> false on an object that already exists in Canvas."""

    def test_becomes_visible(self, graded_then_hidden):
        after_graded, _ = graded_then_hidden
        assert after_graded is not None, "Self-Check quiz disappeared"
        assert getattr(after_graded, "hide_in_gradebook", False) is False

    def test_points_applied(self, graded_then_hidden):
        after_graded, _ = graded_then_hidden
        assert (after_graded.points_possible or 0) > 0


class TestHideOnUpdate:
    """false -> true, together with the points drop that makes it legal."""

    def test_becomes_hidden(self, graded_then_hidden):
        _, after_hidden = graded_then_hidden
        assert after_hidden is not None
        assert getattr(after_hidden, "hide_in_gradebook", False) is True, (
            "hide_in_gradebook was not applied on the update path — Canvas most "
            "likely still held the old points when the request was made"
        )

    def test_points_back_to_zero(self, graded_then_hidden):
        _, after_hidden = graded_then_hidden
        assert (after_hidden.points_possible or 0) == 0

    def test_omit_still_enabled(self, graded_then_hidden):
        _, after_hidden = graded_then_hidden
        assert getattr(after_hidden, "omit_from_final_grade", False) is True
