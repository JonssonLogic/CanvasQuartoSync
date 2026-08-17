"""
Timezone-aware date handling for Canvas payloads.

Canvas accepts ISO 8601 with an explicit offset (or ``Z``) and stores everything
in UTC. Authors, however, think in course-local wall-clock time: "the exam opens
at 09:00". Writing a fixed UTC time (``09:00Z``) or a fixed offset (``+02:00``)
is fragile across daylight saving - the same literal means a different local time
either side of the DST boundary, so weekly deadlines silently drift an hour
mid-semester.

Content files may therefore carry a *naive* time (``2026-11-17T09:00:00``), which
this module resolves against the course timezone and converts to UTC before it
reaches the API. Values that already carry ``Z`` or an offset are passed through
untouched, so existing content keeps its exact current meaning.

Every handler sends UTC. That is deliberate: it keeps behaviour identical across
the form-encoded core API and the JSON New Quizzes API, and since
``to_canvas_iso`` always returns a ``str`` it also removes the class of bug where
a ``datetime`` from PyYAML reached ``json.dumps``.

The course timezone is *deployment metadata about the target*, in the same
category as ``canvas_api_url`` and ``course_id``. It tells the tool how to
interpret the times the local files declare - the files remain authoritative.
"""

import datetime
import re

from handlers.config import load_config
from handlers.log import logger

try:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
except ImportError:  # pragma: no cover - Python < 3.9
    ZoneInfo = None
    ZoneInfoNotFoundError = Exception


UTC = datetime.timezone.utc

# A trailing ``Z`` or ``+HH:MM`` / ``-HHMM`` means the author stated the instant
# explicitly and we must not reinterpret it.
_OFFSET_RE = re.compile(r"(Z|z|[+-]\d{2}:?\d{2})$")

# Accepted naive spellings, widest first.
_NAIVE_FORMATS = (
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
)

_tz_cache = {}
_mismatch_warned = set()


class DateError(ValueError):
    """A date value could not be understood, or needed a timezone that isn't set."""


def get_zone(name):
    """Return a ``ZoneInfo`` for an IANA name, or None if the name is empty.

    Raises :class:`DateError` with actionable text when the name is unknown or
    when the tz database is missing (Windows ships no system tzdata, so the
    ``tzdata`` package is required there).
    """
    if not name:
        return None
    if isinstance(name, datetime.tzinfo):
        return name
    if ZoneInfo is None:
        raise DateError("This Python is too old for zoneinfo (needs 3.9+).")
    try:
        return ZoneInfo(str(name))
    except ZoneInfoNotFoundError:
        raise DateError(
            f"Unknown timezone {name!r}. Use an IANA name such as "
            f"'Europe/Stockholm'. On Windows this also needs the 'tzdata' "
            f"package installed (pip install tzdata)."
        )
    except Exception as e:
        raise DateError(f"Could not load timezone {name!r}: {e}")


def resolve_timezone(course=None, content_root=None):
    """Resolve the course timezone, or None when nothing declares one.

    Order: ``config.toml`` ``timezone`` first (the local files are
    authoritative), then the Canvas course's own ``time_zone``. Results are
    cached per (course, content_root).

    When both are present and disagree, warn once. This is not a correctness
    problem - we always send UTC, so the instant is right either way - but Canvas
    renders that instant in the *course* timezone, so students would see a
    different wall-clock time than the author intended.
    """
    key = (getattr(course, "id", None), content_root)
    if key in _tz_cache:
        return _tz_cache[key]

    configured_name = None
    if content_root:
        try:
            configured_name = load_config(content_root).get("timezone") or None
        except Exception:
            configured_name = None

    canvas_name = getattr(course, "time_zone", None) or None

    if configured_name and canvas_name and str(configured_name) != str(canvas_name):
        if key not in _mismatch_warned:
            _mismatch_warned.add(key)
            logger.warning(
                "    [yellow]Timezone mismatch:[/yellow] config.toml declares %s but the "
                "Canvas course is set to %s. Times are still synced as the correct "
                "instant, but Canvas displays them in %s, so students will see a "
                "different clock time than your files state.",
                configured_name, canvas_name, canvas_name,
            )

    chosen = configured_name or canvas_name
    tz = get_zone(chosen)
    if tz is not None and key not in _tz_cache:
        # Logged every run so a changed Canvas course setting is visible rather
        # than quietly altering what unchanged local files mean.
        logger.debug(
            "    Course timezone: %s (from %s)",
            chosen, "config.toml" if configured_name else "Canvas course setting",
        )
    _tz_cache[key] = tz
    return tz


def reset_cache():
    """Clear resolved timezones. For tests, and for long-lived processes."""
    _tz_cache.clear()
    _mismatch_warned.clear()


def dst_anomaly(naive_dt, tz):
    """Classify a naive local time against DST: None, ``"gap"`` or ``"ambiguous"``.

    A *gap* is a wall-clock time that never happens (the spring-forward hour); an
    *ambiguous* time happens twice (the autumn fold). Both still resolve to some
    instant, so this is advisory - the validator warns rather than failing.
    """
    if tz is None or not isinstance(naive_dt, datetime.datetime):
        return None
    if naive_dt.tzinfo is not None:
        return None
    early = naive_dt.replace(tzinfo=tz, fold=0)
    late = naive_dt.replace(tzinfo=tz, fold=1)
    # Test the gap first: PEP 495 gives fold a meaning for imaginary times too,
    # so a differing offset alone cannot tell the two cases apart. Only a gap
    # survives a round trip through UTC on a different wall clock.
    if early.astimezone(UTC).astimezone(tz).replace(tzinfo=None) != naive_dt:
        return "gap"
    if early.utcoffset() != late.utcoffset():
        return "ambiguous"
    return None


def naive_local(value):
    """Return the naive local datetime a value denotes, else None.

    Values carrying ``Z`` or an offset name their instant outright and can't be
    ambiguous, so they yield None. Used to feed :func:`dst_anomaly` without
    duplicating the parsing rules.
    """
    if isinstance(value, datetime.datetime):
        return None if value.tzinfo is not None else value
    if isinstance(value, datetime.date):
        return datetime.datetime(value.year, value.month, value.day)
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or _OFFSET_RE.search(text):
        return None
    for fmt in _NAIVE_FORMATS:
        try:
            return datetime.datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _format(dt):
    """Render an aware datetime as the UTC form Canvas returns and accepts."""
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _needs_zone(field, tz):
    if tz is None:
        where = f"{field}: " if field else ""
        raise DateError(
            f"{where}this is a local time with no timezone, but no course timezone "
            f"is known. Set `timezone = \"Europe/Stockholm\"` in config.toml, or "
            f"write the value with an explicit offset (e.g. '...Z')."
        )


def to_canvas_iso(value, tz=None, field=None):
    """Normalise one date value into the UTC string Canvas expects.

    ``None`` and empty become ``''``, which is how the sync explicitly *clears* a
    date in Canvas (the API ignores None but honours an empty string). Values
    carrying ``Z`` or an offset are returned unchanged. Naive values - strings, or
    the ``datetime``/``date`` objects PyYAML builds from unquoted frontmatter -
    are interpreted in ``tz`` and converted to UTC.

    Args:
        value: the raw value from frontmatter, JSON, or a schedule entry.
        tz: a ``tzinfo`` (typically from :func:`resolve_timezone`), or None.
        field: key name, used only to make error messages actionable.

    Raises:
        DateError: if the value is unparseable, or is naive while ``tz`` is None.
    """
    if value is None:
        return ""

    if isinstance(value, datetime.datetime):
        if value.tzinfo is not None:
            return _format(value)
        _needs_zone(field, tz)
        return _format(value.replace(tzinfo=tz))

    if isinstance(value, datetime.date):
        _needs_zone(field, tz)
        midnight = datetime.datetime(value.year, value.month, value.day, tzinfo=tz)
        return _format(midnight)

    if not isinstance(value, str):
        raise DateError(
            f"{field or 'date'}: expected a date, got {value!r}"
        )

    text = value.strip()
    if not text:
        return ""

    # Already explicit about its instant - leave it exactly as written.
    if _OFFSET_RE.search(text):
        return text

    for fmt in _NAIVE_FORMATS:
        try:
            parsed = datetime.datetime.strptime(text, fmt)
        except ValueError:
            continue
        _needs_zone(field, tz)
        return _format(parsed.replace(tzinfo=tz))

    raise DateError(
        f"{field or 'date'}: could not read {value!r} as a date. Use ISO 8601, "
        f"e.g. '2026-03-15T23:59:00' (course local) or '2026-03-15T23:59:00Z' (UTC)."
    )


def parse_canvas_utc(value):
    """Parse a timestamp Canvas returned into an aware datetime, or None.

    Used to compare against values we computed, instead of matching substrings -
    Canvas always answers in UTC regardless of what was sent.
    """
    if not value:
        return None
    if isinstance(value, datetime.datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    text = str(value).strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def to_local_naive(value, tz):
    """Render a Canvas UTC timestamp as a naive course-local string.

    The inverse of :func:`to_canvas_iso` for the import direction, so a course
    pulled down from Canvas reads the way it would have been authored by hand.
    Falls back to the original text when the value or timezone is unusable.
    """
    parsed = parse_canvas_utc(value)
    if parsed is None:
        return str(value) if value else ""
    if tz is None:
        return _format(parsed)
    return parsed.astimezone(tz).strftime("%Y-%m-%dT%H:%M:%S")
