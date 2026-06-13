"""
Shared helpers for E2E tests against a real Canvas course.

Centralizes three concerns so the conftest and individual E2E tests stay thin
and the safety logic is unit-testable without touching Canvas:

  1. Credential / course-id / marker resolution — reuses the *regular* config
     system (`handlers.config`) so behavior matches the real sync. A gitignored
     ``./.e2e/`` directory can hold the token, Canvas URL, course id, and safety
     marker; environment variables still take precedence and work alone. (This
     is deliberately separate from ``./.testing/``, which is a manual-sync
     scratch area.)
  2. A safety-guarded, thorough course purge so each run starts fresh.
  3. A wrapper around the sync subprocess that injects the resolved credentials
     into the child environment (so file-based creds work without changing the
     sync code).
"""

import os
import sys
import subprocess

from handlers.config import get_api_credentials, get_course_id, load_config
from handlers.content_utils import (
    FOLDER_IMAGES,
    FOLDER_FILES,
    safe_delete_file,
    safe_delete_dir,
)

# <project_root>/  (tests/e2e/canvas_helpers.py -> up 3)
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Gitignored directory holding local E2E credentials + course id. Kept separate
# from ``.testing/`` (a manual-sync scratch area) to avoid collisions.
E2E_CONFIG_DIR = os.path.join(PROJECT_ROOT, ".e2e")

SYNC_SCRIPT = os.path.join(PROJECT_ROOT, "sync_to_canvas.py")

DEFAULT_COURSE_MARKER = "test"


def resolve_marker(config_dir=None):
    """Resolve the course-name safety marker.

    Priority: CANVAS_TEST_COURSE_MARKER env var -> ``.e2e/config.toml``
    ``test_course_marker`` -> default ``"test"``.
    """
    if config_dir is None:
        config_dir = E2E_CONFIG_DIR

    marker = os.environ.get("CANVAS_TEST_COURSE_MARKER")
    if not marker:
        cfg = load_config(config_dir)
        marker = cfg.get("test_course_marker")
    return marker or DEFAULT_COURSE_MARKER


def resolve_credentials(cli_course_id=None, config_dir=None):
    """Resolve ``(api_url, api_token, course_id, marker)`` for E2E tests.

    Reuses ``handlers.config`` (the same resolution the real sync uses), so
    env vars win and ``.e2e/`` files act as a fallback. The course id also
    honors the ``CANVAS_TEST_COURSE_ID`` env var. Any unresolved value comes
    back falsy so the caller can skip.
    """
    if config_dir is None:
        config_dir = E2E_CONFIG_DIR

    api_url, api_token = get_api_credentials(config_dir)

    course_id = cli_course_id or os.environ.get("CANVAS_TEST_COURSE_ID")
    course_id = get_course_id(config_dir, arg_course_id=course_id)

    marker = resolve_marker(config_dir)
    return api_url, api_token, course_id, marker


def assert_safe_to_purge(course, marker):
    """Raise unless the course name contains the safety marker (case-insensitive).

    Guards against accidentally wiping a production course when file-based creds
    are configured.
    """
    name = getattr(course, "name", "") or ""
    if marker.lower() not in name.lower():
        raise RuntimeError(
            f"Refusing to purge course '{name}': its name does not contain the "
            f"safety marker '{marker}'. Point the tests at a disposable test "
            f"course, or set the marker via CANVAS_TEST_COURSE_MARKER or "
            f"'test_course_marker' in .testing/config.toml."
        )


def purge_course(course, marker):
    """Delete all sync-managed content from the course so a run starts fresh.

    Order matters: modules reference content items, so they go first. Then
    pages (front page reset first), assignments, quizzes, and finally the
    uploaded-asset folders (``synced-images`` / ``synced-files``).
    """
    assert_safe_to_purge(course, marker)

    for module in course.get_modules():
        module.delete()

    for page in course.get_pages():
        try:
            if getattr(page, "front_page", False):
                page.edit(wiki_page={"front_page": False})
            page.delete()
        except Exception:
            pass

    for assignment in course.get_assignments():
        try:
            assignment.delete()
        except Exception:
            pass

    for quiz in course.get_quizzes():
        try:
            quiz.delete()
        except Exception:
            pass

    # Uploaded assets live in dedicated folders; deleting the folder removes
    # its files too.
    managed_folders = {FOLDER_IMAGES.lower(), FOLDER_FILES.lower()}
    try:
        for folder in course.get_folders():
            if (folder.name or "").lower() in managed_folders:
                try:
                    folder.delete()
                except Exception:
                    pass
    except Exception:
        pass


def reset_local_state(content_root):
    """Remove the local sync map and drift snapshots so the next sync is clean."""
    safe_delete_file(os.path.join(content_root, ".canvas_sync_map.json"))
    safe_delete_dir(os.path.join(content_root, ".canvas_snapshots"))


def ensure_group_category(course, name):
    """Return the course group category with ``name``, creating it if missing.

    Group assignments reference a group set by name; if it doesn't exist the
    sync would prompt interactively (and stall the test). Ensuring it exists up
    front keeps the run non-interactive.
    """
    for gc in course.get_group_categories():
        if gc.name == name:
            return gc
    return course.create_group_category(name=name)


def run_sync(content_root, course_id, api_url, api_token, *extra_args, timeout=900):
    """Run sync_to_canvas.py as a subprocess with resolved creds injected.

    Returns the ``subprocess.CompletedProcess``. Credentials are injected into
    the child environment so file-based (``.e2e/``) creds work without the sync
    code having to know about the test setup. A ``timeout`` guards against a
    sync that stalls (e.g. an unexpected interactive prompt) hanging the suite.
    """
    env = {**os.environ}
    if api_url:
        env["CANVAS_API_URL"] = api_url
    if api_token:
        env["CANVAS_API_TOKEN"] = api_token

    cmd = [sys.executable, SYNC_SCRIPT, content_root, "--course-id", str(course_id), *extra_args]
    return subprocess.run(
        cmd, capture_output=True, text=True, env=env, cwd=PROJECT_ROOT, timeout=timeout
    )
