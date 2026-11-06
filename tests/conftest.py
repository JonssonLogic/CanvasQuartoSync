"""
Root conftest.py — shared fixtures for all test tiers.

Fixtures defined here are automatically available to every test file
without needing an import.
"""

import os
import sys
import pytest

# Ensure the project root is on sys.path so 'handlers' and 'sync_to_canvas' are importable
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


def pytest_addoption(parser):
    """Add custom CLI options for E2E tests."""
    parser.addoption(
        "--course-id",
        action="store",
        default=None,
        help="Canvas course ID for E2E tests. Overrides CANVAS_TEST_COURSE_ID env var.",
    )


@pytest.fixture(autouse=True)
def reset_globals():
    """Clear module-level caches between tests to prevent cross-contamination."""
    from handlers.content_utils import FOLDER_CACHE, ACTIVE_ASSET_IDS
    from handlers.config import _config_cache
    from handlers.table_styles import _settings_cache as _table_settings_cache

    FOLDER_CACHE.clear()
    ACTIVE_ASSET_IDS.clear()
    _config_cache.clear()
    _table_settings_cache.clear()
    yield


@pytest.fixture
def fixtures_dir():
    """Return the path to the tests/fixtures/ directory."""
    return os.path.join(os.path.dirname(__file__), "fixtures")
