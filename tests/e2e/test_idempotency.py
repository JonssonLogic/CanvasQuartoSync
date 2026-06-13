"""End-to-end test: running sync a second time should succeed with no errors.

Depends on the canvas_course fixture which already ran the first sync.
"""

import pytest

from tests.e2e.canvas_helpers import run_sync
from tests.e2e.conftest import E2E_CONTENT_DIR

pytestmark = pytest.mark.canvas


def test_second_sync_succeeds(canvas_course, e2e_credentials):
    """Running sync again (without --force) should complete without errors.
    Smart sync should skip unchanged files.
    """
    result = run_sync(
        E2E_CONTENT_DIR,
        e2e_credentials["course_id"],
        e2e_credentials["api_url"],
        e2e_credentials["api_token"],
    )
    assert result.returncode == 0, f"Second sync failed:\n{result.stderr}"


def test_module_count_unchanged(canvas_course, e2e_credentials):
    """Module count should remain the same after a second sync."""
    count_before = len(list(canvas_course.get_modules()))

    result = run_sync(
        E2E_CONTENT_DIR,
        e2e_credentials["course_id"],
        e2e_credentials["api_url"],
        e2e_credentials["api_token"],
    )
    assert result.returncode == 0

    count_after = len(list(canvas_course.get_modules()))
    assert count_after == count_before, (
        f"Module count changed: {count_before} -> {count_after}"
    )
