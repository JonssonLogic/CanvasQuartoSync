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
      3. course_id.txt (deprecated, warns; to be removed)
    """
    if arg_course_id:
        return str(arg_course_id)

    cfg = load_config(content_root)
    cid = cfg.get("course_id")
    if cid:
        _warn_course_id_txt(content_root)
        return str(cid)

    # Legacy fallback
    val = _read_course_id_txt(content_root)
    if val:
        _warn_course_id_txt(content_root)
        return val

    return None


# course_id.txt predates config.toml and is on its way out. Both the sync and
# check_content say so, in the same words, so nobody is surprised when it goes.
_warned_roots = set()


def _read_course_id_txt(content_root):
    txt = os.path.join(content_root, "course_id.txt")
    if not os.path.exists(txt):
        return None
    try:
        with open(txt, "r") as f:
            return f.read().strip() or None
    except Exception:
        return None


def course_id_txt_notice(content_root):
    """The deprecation message for this course's course_id.txt, or None.

    Two cases, because they need different advice: the file is what supplies
    the course id (move it into config.toml), or config.toml already has one
    and the file is silently ignored (just delete it).
    """
    if not os.path.exists(os.path.join(content_root, "course_id.txt")):
        return None
    txt_id = _read_course_id_txt(content_root)
    toml_id = _read_toml(content_root).get("course_id")
    if toml_id:
        differs = f" (it says {txt_id})" if txt_id and str(txt_id) != str(toml_id) else ""
        return (f"course_id.txt is ignored{differs}: config.toml already sets "
                f"course_id = {toml_id}. Support for course_id.txt will be removed "
                f"in a future version; delete the file.")
    return ("course_id.txt is deprecated and support will be removed in a future "
            f"version. Move the id into config.toml as course_id = {txt_id or '<id>'}, "
            "then delete course_id.txt.")


def _warn_course_id_txt(content_root):
    key = os.path.abspath(content_root)
    if key in _warned_roots:
        return
    msg = course_id_txt_notice(content_root)
    if msg:
        _warned_roots.add(key)
        from handlers.log import logger
        logger.warning("[yellow]%s[/yellow]", msg)


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
