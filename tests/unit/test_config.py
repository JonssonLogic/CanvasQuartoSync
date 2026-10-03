"""Tests for handlers/config.py — configuration resolution."""

import logging
import os
import pytest
from handlers.config import (load_config, get_course_id, get_api_credentials, _config_cache,
                             course_id_txt_notice)
from validate_content import validate_path


class TestLoadConfig:

    def test_no_config_file(self, tmp_path):
        cfg = load_config(str(tmp_path))
        assert isinstance(cfg, dict)

    def test_with_toml(self, tmp_path):
        (tmp_path / "config.toml").write_text('course_id = 12345\n')
        cfg = load_config(str(tmp_path))
        assert cfg["course_id"] == 12345

    def test_caching(self, tmp_path):
        """Second call returns cached result."""
        (tmp_path / "config.toml").write_text('course_id = 42\n')
        cfg1 = load_config(str(tmp_path))
        cfg2 = load_config(str(tmp_path))
        assert cfg1 is cfg2


class TestGetCourseId:

    def test_from_cli_arg(self, tmp_path):
        assert get_course_id(str(tmp_path), "999") == "999"

    def test_from_toml(self, tmp_path):
        (tmp_path / "config.toml").write_text('course_id = 42\n')
        assert get_course_id(str(tmp_path)) == "42"

    def test_from_legacy_txt(self, tmp_path):
        (tmp_path / "course_id.txt").write_text("1434")
        assert get_course_id(str(tmp_path)) == "1434"

    def test_cli_wins_over_toml(self, tmp_path):
        (tmp_path / "config.toml").write_text('course_id = 42\n')
        assert get_course_id(str(tmp_path), "999") == "999"

    def test_toml_wins_over_txt(self, tmp_path):
        (tmp_path / "config.toml").write_text('course_id = 42\n')
        (tmp_path / "course_id.txt").write_text("1434")
        assert get_course_id(str(tmp_path)) == "42"

    def test_nothing_configured(self, tmp_path):
        assert get_course_id(str(tmp_path)) is None


class TestCourseIdTxtDeprecation:
    """course_id.txt still works, but says it is going away."""

    def test_no_file_no_notice(self, tmp_path):
        (tmp_path / "config.toml").write_text('course_id = 42\n')
        assert course_id_txt_notice(str(tmp_path)) is None

    def test_used_file_says_move_it(self, tmp_path):
        (tmp_path / "course_id.txt").write_text("1434")
        msg = course_id_txt_notice(str(tmp_path))
        assert "deprecated" in msg
        assert "course_id = 1434" in msg

    def test_ignored_file_says_delete_it(self, tmp_path):
        (tmp_path / "config.toml").write_text('course_id = 42\n')
        (tmp_path / "course_id.txt").write_text("42")
        msg = course_id_txt_notice(str(tmp_path))
        assert "ignored" in msg and "delete" in msg
        assert "it says" not in msg

    def test_ignored_file_with_a_different_id_says_so(self, tmp_path):
        (tmp_path / "config.toml").write_text('course_id = 42\n')
        (tmp_path / "course_id.txt").write_text("1434")
        assert "(it says 1434)" in course_id_txt_notice(str(tmp_path))

    def test_sync_warns_once(self, tmp_path, caplog):
        (tmp_path / "course_id.txt").write_text("1434")
        with caplog.at_level(logging.WARNING, logger="canvas_sync"):
            get_course_id(str(tmp_path))
            get_course_id(str(tmp_path))
        assert len([r for r in caplog.records if "course_id.txt" in r.getMessage()]) == 1

    def test_check_content_flags_it(self, tmp_path):
        (tmp_path / "course_id.txt").write_text("1434")
        reports = validate_path(str(tmp_path))
        flagged = [r for r in reports if r.path.endswith("course_id.txt")]
        assert len(flagged) == 1
        assert flagged[0].issues[0].level == "WARN"


class TestGetApiCredentials:

    def test_from_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CANVAS_API_URL", "https://canvas.test.com")
        monkeypatch.setenv("CANVAS_API_TOKEN", "test-token-123")
        url, token = get_api_credentials(str(tmp_path))
        assert url == "https://canvas.test.com"
        assert token == "test-token-123"

    def test_url_from_toml(self, tmp_path, monkeypatch):
        monkeypatch.delenv("CANVAS_API_URL", raising=False)
        monkeypatch.delenv("CANVAS_API_TOKEN", raising=False)
        (tmp_path / "config.toml").write_text('canvas_api_url = "https://toml.canvas.com"\n')
        url, token = get_api_credentials(str(tmp_path))
        assert url == "https://toml.canvas.com"

    def test_env_url_wins_over_toml(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CANVAS_API_URL", "https://env.canvas.com")
        monkeypatch.delenv("CANVAS_API_TOKEN", raising=False)
        (tmp_path / "config.toml").write_text('canvas_api_url = "https://toml.canvas.com"\n')
        url, _ = get_api_credentials(str(tmp_path))
        assert url == "https://env.canvas.com"
