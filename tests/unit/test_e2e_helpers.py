"""Unit tests for tests/e2e/canvas_helpers.py — resolution + safety guard.

These exercise the credential/marker resolution and the purge safety guard
without any Canvas access.
"""

import pytest

from tests.e2e.canvas_helpers import (
    resolve_credentials,
    resolve_marker,
    assert_safe_to_purge,
    DEFAULT_COURSE_MARKER,
)

CRED_ENV = [
    "CANVAS_API_URL",
    "CANVAS_API_TOKEN",
    "CANVAS_TEST_COURSE_ID",
    "CANVAS_TEST_COURSE_MARKER",
]


@pytest.fixture
def clean_env(monkeypatch):
    """Ensure no ambient Canvas env vars leak into resolution tests."""
    for name in CRED_ENV:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def _write_config_dir(tmp_path, *, url="https://file.example.com",
                      token="file-token", course_id=12345,
                      marker="Training", with_token_file=True):
    cfg_lines = [
        f'canvas_api_url = "{url}"',
        'canvas_token_path = "token.txt"',
        f'course_id = {course_id}',
        f'test_course_marker = "{marker}"',
    ]
    (tmp_path / "config.toml").write_text("\n".join(cfg_lines) + "\n", encoding="utf-8")
    if with_token_file:
        (tmp_path / "token.txt").write_text(token, encoding="utf-8")
    return str(tmp_path)


class TestResolveCredentials:

    def test_reads_from_testing_dir(self, clean_env, tmp_path):
        testing_dir = _write_config_dir(tmp_path)
        url, token, course_id, marker = resolve_credentials(config_dir=testing_dir)
        assert url == "https://file.example.com"
        assert token == "file-token"
        assert course_id == "12345"
        assert marker == "Training"

    def test_env_vars_win_over_files(self, clean_env, tmp_path):
        testing_dir = _write_config_dir(tmp_path)
        clean_env.setenv("CANVAS_API_URL", "https://env.example.com")
        clean_env.setenv("CANVAS_API_TOKEN", "env-token")
        clean_env.setenv("CANVAS_TEST_COURSE_ID", "999")
        url, token, course_id, _ = resolve_credentials(config_dir=testing_dir)
        assert url == "https://env.example.com"
        assert token == "env-token"
        assert course_id == "999"

    def test_env_token_used_when_token_file_absent(self, clean_env, tmp_path):
        # config.toml present (url + token_path) but no token.txt on disk.
        testing_dir = _write_config_dir(tmp_path, with_token_file=False)
        clean_env.setenv("CANVAS_API_TOKEN", "env-token")
        url, token, course_id, _ = resolve_credentials(config_dir=testing_dir)
        assert url == "https://file.example.com"   # url still from file
        assert token == "env-token"                 # token from env fallback

    def test_cli_course_id_takes_priority(self, clean_env, tmp_path):
        testing_dir = _write_config_dir(tmp_path, course_id=12345)
        _, _, course_id, _ = resolve_credentials(cli_course_id="777", config_dir=testing_dir)
        assert course_id == "777"

    def test_missing_everything_returns_falsy(self, clean_env, tmp_path):
        # Empty testing dir, no env -> nothing resolves.
        url, token, course_id, marker = resolve_credentials(config_dir=str(tmp_path))
        assert not url
        assert not token
        assert not course_id
        assert marker == DEFAULT_COURSE_MARKER


class TestResolveMarker:

    def test_default(self, clean_env, tmp_path):
        assert resolve_marker(config_dir=str(tmp_path)) == DEFAULT_COURSE_MARKER

    def test_from_config(self, clean_env, tmp_path):
        _write_config_dir(tmp_path, marker="Sandbox")
        assert resolve_marker(config_dir=str(tmp_path)) == "Sandbox"

    def test_env_wins(self, clean_env, tmp_path):
        _write_config_dir(tmp_path, marker="Sandbox")
        clean_env.setenv("CANVAS_TEST_COURSE_MARKER", "EnvMarker")
        assert resolve_marker(config_dir=str(tmp_path)) == "EnvMarker"


class _FakeCourse:
    def __init__(self, name):
        self.name = name


class TestAssertSafeToPurge:

    def test_passes_when_marker_present(self):
        assert_safe_to_purge(_FakeCourse("CS101 Training Sandbox"), "Training")  # no raise

    def test_case_insensitive(self):
        assert_safe_to_purge(_FakeCourse("my TRAINING course"), "training")  # no raise

    def test_raises_when_marker_absent(self):
        with pytest.raises(RuntimeError, match="Refusing to purge"):
            assert_safe_to_purge(_FakeCourse("Production Biology 200"), "Training")

    def test_raises_on_empty_name(self):
        with pytest.raises(RuntimeError):
            assert_safe_to_purge(_FakeCourse(""), "test")
