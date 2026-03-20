"""
Unified configuration for CanvasQuartoSync.

Resolution priority (highest wins):
  1. CLI arguments (where applicable)
  2. Environment variables (CANVAS_API_URL, CANVAS_API_TOKEN)
  3. config.toml in the content root directory
"""

import os

import tomllib


_config_cache = {}


def load_config(content_root):
    """Load and cache config.toml from content_root, merged with env vars."""
    if content_root in _config_cache:
        return _config_cache[content_root]

    cfg = _read_toml(content_root)

    # Resolve token: env var wins, then read from file path in toml
    token = os.environ.get("CANVAS_API_TOKEN")
    if not token:
        token_path = cfg.get("canvas_token_path", "")
        if token_path:
            token = _read_token_file(token_path, content_root)
    cfg["canvas_api_token"] = token or ""

    # Resolve API URL: env var wins, then toml
    env_url = os.environ.get("CANVAS_API_URL")
    if env_url:
        cfg["canvas_api_url"] = env_url

    _config_cache[content_root] = cfg
    return cfg


def get_api_credentials(content_root):
    """Return (api_url, api_token) tuple."""
    cfg = load_config(content_root)
    return cfg.get("canvas_api_url", ""), cfg.get("canvas_api_token", "")


def get_course_id(content_root, arg_course_id=None):
    """
    Determine course ID.  Priority:
      1. CLI argument
      2. config.toml  course_id
      3. Legacy course_id.txt
    """
    if arg_course_id:
        return str(arg_course_id)

    cfg = load_config(content_root)
    cid = cfg.get("course_id")
    if cid:
        return str(cid)

    # Legacy fallback
    txt = os.path.join(content_root, "course_id.txt")
    if os.path.exists(txt):
        try:
            with open(txt, "r") as f:
                val = f.read().strip()
                if val:
                    return val
        except Exception:
            pass

    return None


def _read_toml(content_root):
    """Read config.toml from content_root. Returns dict (empty if missing)."""
    path = os.path.join(content_root, "config.toml")
    if not os.path.exists(path):
        return {}
    with open(path, "rb") as f:
        return tomllib.load(f)


def _read_token_file(token_path, content_root):
    """Read a token from a file path (absolute or relative to content_root)."""
    if not os.path.isabs(token_path):
        token_path = os.path.join(content_root, token_path)
    try:
        with open(token_path, "r") as f:
            return f.read().strip()
    except FileNotFoundError:
        return None
