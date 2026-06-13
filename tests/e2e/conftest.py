"""
E2E conftest.py — fixtures for real Canvas API testing.

Credentials, the test course id, and the safety marker are resolved through the
*regular* config system (`handlers.config`). They may be supplied via:
  - environment variables (CANVAS_API_URL, CANVAS_API_TOKEN, CANVAS_TEST_COURSE_ID),
  - the `--course-id` CLI flag, and/or
  - a gitignored `./.e2e/` directory (config.toml + token file).
See tests/e2e/e2e.config.example.toml for the file format.

Run with:
  python -m pytest tests/e2e/ -v -m canvas              # creds from .testing/
  python -m pytest tests/e2e/ -v -m canvas --course-id 12345

All tests in this directory are marked with @pytest.mark.canvas.
"""

import os
import pytest

from tests.e2e.canvas_helpers import (
    resolve_credentials,
    purge_course,
    reset_local_state,
    ensure_group_category,
    run_sync,
)

# Dedicated test content (independent of Example/)
E2E_CONTENT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "fixtures", "e2e_content",
)

# Group set referenced by 02_Statics/03_Group_Project.qmd. Must exist on Canvas
# before the sync, or the group assignment would prompt interactively.
GROUP_CATEGORY_NAME = "MECH201 Project Groups"

# Set when the live suite actually syncs, so the manual-verification prompt is
# only printed when tests ran against Canvas (not when skipped).
_course_web_url = None

# The few things automated assertions can't see — printed at the end of a run.
_HUMAN_CHECKS = [
    "Course PM (PDF) opens and looks correct (LaTeX, math, tables)",
    "New Quizzes render in the New Quizzes UI (Beam Bending Concepts / "
    "Calculations / Section Properties); numeric & formula questions show values",
    "Overall branding / layout looks right",
]


@pytest.fixture(scope="session")
def e2e_credentials(request):
    """Resolve (api_url, api_token, course_id, marker) or skip the E2E suite."""
    api_url, api_token, course_id, marker = resolve_credentials(
        cli_course_id=request.config.getoption("--course-id")
    )
    if not api_url or not api_token:
        pytest.skip(
            "Canvas credentials not found. Set CANVAS_API_URL / CANVAS_API_TOKEN "
            "or create .e2e/config.toml (see tests/e2e/e2e.config.example.toml)."
        )
    if not course_id:
        pytest.skip(
            "No test course id. Use --course-id, set CANVAS_TEST_COURSE_ID, or add "
            "course_id to .e2e/config.toml."
        )
    return {
        "api_url": api_url,
        "api_token": api_token,
        "course_id": course_id,
        "marker": marker,
    }


@pytest.fixture(scope="session")
def canvas_course(e2e_credentials):
    """Connect to the test course, purge it, reset local state, then sync content.

    Returns the canvasapi Course object for verification.
    """
    from canvasapi import Canvas

    canvas = Canvas(e2e_credentials["api_url"], e2e_credentials["api_token"])
    course = canvas.get_course(e2e_credentials["course_id"])

    # Record the course URL so the manual-verification prompt can show it.
    global _course_web_url
    _course_web_url = f"{e2e_credentials['api_url'].rstrip('/')}/courses/{e2e_credentials['course_id']}"

    # Fresh every run: wipe the course (guarded by the safety marker) and clear
    # local sync state so the forced sync starts clean.
    purge_course(course, e2e_credentials["marker"])
    reset_local_state(E2E_CONTENT_DIR)

    # The group-set assignment needs its group category to exist up front.
    ensure_group_category(course, GROUP_CATEGORY_NAME)

    result = run_sync(
        E2E_CONTENT_DIR,
        e2e_credentials["course_id"],
        e2e_credentials["api_url"],
        e2e_credentials["api_token"],
        "--force",
    )
    if result.returncode != 0:
        pytest.fail(f"Sync failed (exit code {result.returncode}):\n{result.stderr}")

    return course


@pytest.fixture(scope="session")
def synced_modules(canvas_course):
    """Return dict of module_name -> module object."""
    modules = {}
    for m in canvas_course.get_modules():
        modules[m.name] = m
    return modules


@pytest.fixture(scope="session")
def synced_pages(canvas_course):
    """Return dict of page_title -> page object (with body loaded)."""
    pages = {}
    for p in canvas_course.get_pages():
        full = canvas_course.get_page(p.url)
        pages[full.title] = full
    return pages


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    """After a live E2E run, prompt the developer for the manual-only checks.

    Automated assertions cover everything that can be read back from the API
    (math rendered to equation images, callouts/code styled, tables, dates,
    grading, indentation, etc.). This prints the short list of things that
    still need human eyes, with the course URL. Only shown when the suite
    actually synced to Canvas (not when skipped).
    """
    if not _course_web_url:
        return
    tr = terminalreporter
    tr.write_sep("=", "MANUAL VERIFICATION (things automation can't see)", yellow=True, bold=True)
    tr.write_line(f"Course:    {_course_web_url}")
    tr.write_line(f"Modules:   {_course_web_url}/modules")
    tr.write_line("Full list: tests/e2e/MANUAL_CHECKLIST.md")
    tr.write_line("")
    for item in _HUMAN_CHECKS:
        tr.write_line(f"  [ ] {item}")
    tr.write_sep("=", "", yellow=True)
