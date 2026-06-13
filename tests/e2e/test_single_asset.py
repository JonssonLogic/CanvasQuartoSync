"""End-to-end test for single-asset sync (the --only flag / sync_single_file).

Runs after the full sync (canvas_course fixture). Verifies that syncing one file
succeeds and that the item lands in its correct slot within the module, matching
the order a full sync produces.
"""

import pytest

from tests.e2e.canvas_helpers import run_sync
from tests.e2e.conftest import E2E_CONTENT_DIR

pytestmark = pytest.mark.canvas


def _module(course, name):
    return next((m for m in course.get_modules() if m.name == name), None)


def _only(creds, relpath):
    return run_sync(
        E2E_CONTENT_DIR, creds["course_id"], creds["api_url"], creds["api_token"],
        "--only", relpath,
    )


class TestSingleAssetSync:

    def test_only_first_file_stays_at_position_one(self, canvas_course, e2e_credentials):
        result = _only(e2e_credentials, "01_Introduction/01_Welcome.qmd")
        assert result.returncode == 0, f"--only sync failed:\n{result.stderr}"

        items = list(_module(canvas_course, "Introduction").get_module_items())
        welcome = next((i for i in items if "Welcome" in (i.title or "")), None)
        assert welcome is not None, [i.title for i in items]
        assert welcome.position == 1

    def test_only_places_middle_file_in_correct_slot(self, canvas_course, e2e_credentials):
        # 06_Formula_Sheet.qmd is the 6th syncable file in Introduction.
        result = _only(e2e_credentials, "01_Introduction/06_Formula_Sheet.qmd")
        assert result.returncode == 0, f"--only sync failed:\n{result.stderr}"

        items = list(_module(canvas_course, "Introduction").get_module_items())
        formula = next((i for i in items if "Formula" in (i.title or "")), None)
        assert formula is not None, [i.title for i in items]
        assert formula.position == 6, [(i.position, i.title) for i in items]

    def test_only_solo_file_succeeds(self, canvas_course, e2e_credentials):
        result = _only(e2e_credentials, "04_Course Documents/02_Material_Properties.csv")
        assert result.returncode == 0, f"--only sync failed:\n{result.stderr}"

        items = list(_module(canvas_course, "Course Documents").get_module_items())
        csv = next((i for i in items if i.type == "File" and "Material_Properties" in (i.title or "")), None)
        assert csv is not None, [(i.type, i.title) for i in items]
