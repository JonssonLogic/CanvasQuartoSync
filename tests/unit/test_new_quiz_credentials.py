"""Tests for new_quiz_api.resolve_credentials().

Quiz sync used to read CANVAS_API_URL / CANVAS_API_TOKEN only, so a course
that keeps its credentials in config.toml crashed with
``'NoneType' object has no attribute 'rstrip'``.
"""

import pytest

from handlers import config
from handlers.new_quiz_api import NewQuizAPIError, resolve_credentials


class _Requester:
    def __init__(self, url, token):
        self.original_url = url
        self.access_token = token


class _Course:
    def __init__(self, url="https://req.example.com", token="req-token"):
        self._requester = _Requester(url, token)


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    monkeypatch.delenv("CANVAS_API_URL", raising=False)
    monkeypatch.delenv("CANVAS_API_TOKEN", raising=False)
    config._config_cache.clear()
    yield
    config._config_cache.clear()


def _toml(tmp_path, url="https://toml.example.com", token="toml-token"):
    (tmp_path / "token.txt").write_text(token)
    (tmp_path / "config.toml").write_text(
        f'canvas_api_url = "{url}"\ncanvas_token_path = "token.txt"\n')
    return str(tmp_path)


def test_config_toml_used_when_env_missing(tmp_path):
    root = _toml(tmp_path)
    assert resolve_credentials(None, root) == ("https://toml.example.com", "toml-token")


def test_env_wins_over_config(tmp_path, monkeypatch):
    root = _toml(tmp_path)
    monkeypatch.setenv("CANVAS_API_URL", "https://env.example.com")
    monkeypatch.setenv("CANVAS_API_TOKEN", "env-token")
    assert resolve_credentials(_Course(), root) == ("https://env.example.com", "env-token")


def test_falls_back_to_course_connection():
    assert resolve_credentials(_Course()) == ("https://req.example.com", "req-token")


def test_config_wins_over_course_connection(tmp_path):
    root = _toml(tmp_path)
    assert resolve_credentials(_Course(), root) == ("https://toml.example.com", "toml-token")


def test_nothing_available_raises_clear_error():
    with pytest.raises(NewQuizAPIError, match="credentials"):
        resolve_credentials(None, None)
