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

    The CLI argument and course_id may be a number or the name of a course in
    the [courses] table (see resolve_course_ref). The resolved id becomes this
    folder's active course, which picks the sync map every later read uses.
    Returns None, after logging why, when a name is not in [courses].
    """
    cid = _course_id_or_none(content_root, arg_course_id)
    if cid is not None:
        set_active_course(content_root, cid)
    return cid


def _course_id_or_none(content_root, arg_course_id):
    if arg_course_id:
        return _resolve_or_log(content_root, arg_course_id)

    cfg = load_config(content_root)
    cid = cfg.get("course_id")
    if cid:
        _warn_course_id_txt(content_root)
        return _resolve_or_log(content_root, cid)

    # Legacy fallback
    val = _read_course_id_txt(content_root)
    if val:
        _warn_course_id_txt(content_root)
        return val

    return None


# ---------------------------------------------------------------------------
# Named courses: one folder, several Canvas courses
# ---------------------------------------------------------------------------
# A teacher keeps a sandbox next to the course they teach, and syncs the same
# folder to either. config.toml names them:
#
#     course_id = "live"
#
#     [courses]
#     live     = 17671
#     training = 74
#
# Switching is one edit (or --course-id training). Each course keeps its own
# sync map, because a map's Canvas ids only mean anything in the course they
# came from (see content_utils.get_sync_map_path).

class CourseConfigError(ValueError):
    """course_id names a course that the [courses] table does not define."""


def get_course_targets(content_root):
    """The [courses] table as {name: int id}. Entries that are not a number are skipped."""
    raw = load_config(content_root).get("courses") or {}
    if not isinstance(raw, dict):
        return {}
    out = {}
    for name, val in raw.items():
        try:
            out[str(name)] = int(val)
        except (TypeError, ValueError):
            continue
    return out


def resolve_course_ref(content_root, ref):
    """A course id as a string, from a number or a name in [courses].

    Names match without regard to case. Raises CourseConfigError for a name
    that is not defined, naming the ones that are.
    """
    text = str(ref).strip()
    if text.isdigit():
        return text
    targets = get_course_targets(content_root)
    for name, cid in targets.items():
        if name.lower() == text.lower():
            return str(cid)
    if targets:
        known = ", ".join(f'"{n}"' for n in targets)
        raise CourseConfigError(
            f'course_id = "{text}" is not a course in the [courses] table of config.toml '
            f'(it has {known}). Use one of those names, or a course number.')
    raise CourseConfigError(
        f'course_id = "{text}" is a name, but config.toml has no [courses] table to look it up in. '
        f'Add one, e.g.\n    [courses]\n    {text} = 12345\nor set course_id to the number '
        f'in the Canvas course address (.../courses/12345).')


def course_name_for(content_root, course_id):
    """The [courses] name of a course id, or None if it has none."""
    try:
        course_id = int(course_id)
    except (TypeError, ValueError):
        return None
    for name, cid in get_course_targets(content_root).items():
        if cid == course_id:
            return name
    return None


_course_errors = {}


def course_id_error(content_root):
    """Why the last get_course_id for this folder found no course, or None."""
    return _course_errors.get(os.path.abspath(content_root))


def _resolve_or_log(content_root, ref):
    key = os.path.abspath(content_root)
    try:
        cid = resolve_course_ref(content_root, ref)
    except CourseConfigError as e:
        _course_errors[key] = str(e)
        from handlers.log import logger
        from rich.markup import escape
        logger.error("[red]%s[/red]", escape(str(e)))
        return None
    _course_errors.pop(key, None)
    return cid


# The course this process is talking to, per content folder. Set when the
# course id is resolved, read by the sync map to pick its file. Nothing set
# means the folder's one default map, which is all a single-course folder has.
_active_courses = {}


def set_active_course(content_root, course_id):
    _active_courses[os.path.abspath(content_root)] = str(course_id)


def clear_active_course(content_root):
    _active_courses.pop(os.path.abspath(content_root), None)


def get_active_course(content_root):
    return _active_courses.get(os.path.abspath(content_root))


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
