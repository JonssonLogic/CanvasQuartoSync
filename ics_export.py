"""cqs ics: the course schedule as a calendar that Outlook can subscribe to.

schedule.yaml is turned into one iCalendar (.ics) file, written next to
config.toml. With --publish the file also goes to a secret GitHub gist, whose
raw address never changes. Outlook (or Google Calendar, or Apple Calendar)
subscribes to that address once and then fetches the file again by itself, so
editing schedule.yaml and running `cqs ics --publish` again is all an update
takes: moved events move, removed events disappear.

Canvas is not involved. The calendar sync (`cqs sync --sync-calendar`) and
this export read the same schedule.yaml, independently.

Settings, all optional, in config.toml:

    [ics]
    name = "Konstruktion och Mekatronik 1"   # calendar name; default course_name
    file = "T1H2UO.ics"                      # default <course_code>.ics
    gist = "..."                             # written by the first --publish

The gist belongs to whoever publishes first. A secret gist is not listed
anywhere, but anyone with the address can read it, so the schedule should hold
nothing that is not meant for the students anyway.
"""

import argparse
import datetime
import hashlib
import os
import re
import sys

import yaml

from handlers.config import load_config
from handlers.dates import DateError, get_zone, to_canvas_iso

DAY_MAP = {"Mon": 0, "Tue": 1, "Wed": 2, "Thu": 3, "Fri": 4, "Sat": 5, "Sun": 6}
DEFAULT_TIME = "12:00-13:00"  # the same default as the calendar sync
REFRESH = "PT1H"              # how often a calendar app should fetch the file again
TIME_RE = re.compile(r'^\s*(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})\s*$')

EPILOG = """\
examples:
  cqs ics
      Write the course schedule to <course_code>.ics. Nothing leaves the computer.

  cqs ics --publish
      The same, and put the file on a secret GitHub gist. The first time this
      creates the gist and stores its id in config.toml; after that it updates
      the same gist, so the address you subscribed to keeps working.
"""


class IcsError(Exception):
    """A user-facing problem: print it, exit 2, no traceback."""


# ---------------------------------------------------------------------------
# schedule.yaml -> occurrences
# ---------------------------------------------------------------------------

def _parse_day(value, what):
    """A schedule date, quoted or not (PyYAML turns an unquoted one into a date)."""
    if isinstance(value, datetime.date):
        return value
    try:
        return datetime.datetime.strptime(str(value).strip(), '%Y-%m-%d').date()
    except ValueError:
        raise IcsError(f"{what}: could not read {value!r} as a date. Write it as YYYY-MM-DD.")


def occurrences(events):
    """Every event in schedule.yaml as (date, definition), series expanded.

    A series is one event per matching weekday from start_date to end_date,
    both inclusive, exactly as the calendar sync expands it.
    """
    for i, ev in enumerate(events or [], 1):
        title = ev.get('title') or f"event {i}"
        if 'days' in ev:
            start = _parse_day(ev.get('start_date'), f"{title}: start_date")
            end = _parse_day(ev.get('end_date'), f"{title}: end_date")
            unknown = [d for d in ev['days'] if d not in DAY_MAP]
            if unknown:
                raise IcsError(f"{title}: unknown day {unknown[0]!r}. Use Mon, Tue, Wed, Thu, Fri, Sat or Sun.")
            weekdays = {DAY_MAP[d] for d in ev['days']}
            day = start
            while day <= end:
                if day.weekday() in weekdays:
                    yield day, ev
                day += datetime.timedelta(days=1)
        else:
            yield _parse_day(ev.get('date'), f"{title}: date"), ev


def _times(ev):
    text = str(ev.get('time') or DEFAULT_TIME)
    m = TIME_RE.match(text)
    if not m:
        raise IcsError(f"{ev.get('title', 'event')}: time {text!r} should look like 09:00-12:00.")
    return m.group(1).zfill(5), m.group(2).zfill(5)


# ---------------------------------------------------------------------------
# iCalendar text (RFC 5545)
# ---------------------------------------------------------------------------

def _escape(text):
    return (str(text).replace('\\', '\\\\').replace(';', '\\;').replace(',', '\\,')
            .replace('\r\n', '\n').replace('\n', '\\n'))


def _fold(line):
    """Fold a content line at 75 octets, never inside a UTF-8 character."""
    out, current, size = [], '', 0
    for ch in line:
        n = len(ch.encode('utf-8'))
        if size + n > 75:
            out.append(current)
            current, size = ' ', 1
        current += ch
        size += n
    out.append(current)
    return '\r\n'.join(out)


def _utc(date, hhmm, tz):
    """Course-local date and time as an iCalendar UTC stamp, DST-aware."""
    iso = to_canvas_iso(f"{date.isoformat()}T{hhmm}:00", tz, 'time')  # 2026-10-29T08:00:00Z
    return iso.replace('-', '').replace(':', '')


def _uid(course_key, date, start, end, title):
    """Stable across runs, so an unchanged event stays the same event."""
    digest = hashlib.sha1(f"{date}|{start}|{end}|{title}".encode('utf-8')).hexdigest()[:20]
    return f"{digest}-{course_key}@canvasquartosync"


def build_ics(events, name, tz, tz_name='', course_key='course', now=None):
    """The whole calendar as text, CRLF line endings."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    stamp = now.strftime('%Y%m%dT%H%M%SZ')
    lines = [
        'BEGIN:VCALENDAR',
        'VERSION:2.0',
        'PRODID:-//CanvasQuartoSync//cqs ics//EN',
        'CALSCALE:GREGORIAN',
        'METHOD:PUBLISH',
        f'X-WR-CALNAME:{_escape(name)}',
        f'NAME:{_escape(name)}',
        f'REFRESH-INTERVAL;VALUE=DURATION:{REFRESH}',
        f'X-PUBLISHED-TTL:{REFRESH}',
    ]
    if tz_name:
        lines.append(f'X-WR-TIMEZONE:{tz_name}')
    for date, ev in sorted(occurrences(events), key=lambda o: (o[0], _times(o[1])[0])):
        title = ev.get('title') or 'Untitled Event'
        start, end = _times(ev)
        try:
            dtstart, dtend = _utc(date, start, tz), _utc(date, end, tz)
        except DateError as e:
            raise IcsError(str(e))
        lines += [
            'BEGIN:VEVENT',
            f'UID:{_uid(course_key, date, start, end, title)}',
            f'DTSTAMP:{stamp}',
            f'DTSTART:{dtstart}',
            f'DTEND:{dtend}',
            f'SUMMARY:{_escape(title)}',
        ]
        if ev.get('location'):
            lines.append(f"LOCATION:{_escape(ev['location'])}")
        if ev.get('description'):
            lines.append(f"DESCRIPTION:{_escape(ev['description'])}")
        lines.append('END:VEVENT')
    lines.append('END:VCALENDAR')
    return '\r\n'.join(_fold(l) for l in lines) + '\r\n'


# ---------------------------------------------------------------------------
# Publishing to a gist
# ---------------------------------------------------------------------------

def raw_url(owner, gist_id, filename):
    """The address that always serves the newest version of the file."""
    return f"https://gist.githubusercontent.com/{owner}/{gist_id}/raw/{filename}"


def publish(text, filename, gist_id, description, gh=None):
    """Create or update the gist. Returns (gist_id, raw url, created)."""
    from github_course import GitHub, GithubError, NO_TOKEN, find_token
    try:
        if gh is None:
            token = find_token()
            if not token:
                raise IcsError(NO_TOKEN)
            gh = GitHub(token)
        files = {filename: {'content': text}}
        if gist_id:
            r = gh._call('PATCH', f'/gists/{gist_id}', json={'files': files})
            if r.status_code == 404:
                raise IcsError(f"gist {gist_id} was not found, or it belongs to someone else. "
                               f"Only the person who published first can update it. Remove "
                               f"'gist' from [ics] in config.toml to start a new one.")
            data = gh._ok(r, 'Updating the gist')
            created = False
        else:
            data = gh._ok(gh._call('POST', '/gists', json={
                'description': description, 'public': False, 'files': files}),
                'Creating the gist')
            created = True
    except GithubError as e:
        raise IcsError(f"{e}\n  Gists need the 'gist' permission: gh auth refresh -h github.com -s gist")
    return data['id'], raw_url(data['owner']['login'], data['id'], filename), created


def remember_gist(root, gist_id):
    """Write gist = "<id>" under [ics] in config.toml, keeping the rest as it is."""
    path = os.path.join(root, 'config.toml')
    with open(path, 'r', encoding='utf-8') as f:
        text = f.read()
    line = f'gist = "{gist_id}"   # written by cqs ics --publish\n'
    m = re.search(r'^\[ics\][ \t]*\r?\n', text, re.MULTILINE)
    if m:
        text = text[:m.end()] + line + text[m.end():]
    else:
        text = text.rstrip('\n') + '\n\n[ics]\n' + line
    with open(path, 'w', encoding='utf-8', newline='') as f:
        f.write(text)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def export(root, do_publish=False, gh=None, now=None):
    cfg = load_config(root)
    ics_cfg = cfg.get('ics') or {}
    schedule = os.path.join(root, 'schedule.yaml')
    if not os.path.exists(schedule):
        raise IcsError(f"no schedule.yaml in {root}. The calendar is made from it.")
    with open(schedule, 'r', encoding='utf-8') as f:
        data = yaml.safe_load(f) or {}
    events = data.get('events') or []
    if not events:
        raise IcsError("schedule.yaml has no events.")

    tz_name = cfg.get('timezone') or ''
    try:
        tz = get_zone(tz_name)
    except DateError as e:
        raise IcsError(str(e))
    if tz is None:
        raise IcsError('config.toml has no timezone, so the schedule\'s times mean nothing yet. '
                       'Add timezone = "Europe/Stockholm".')

    code = str(cfg.get('course_code') or 'schedule')
    name = ics_cfg.get('name') or cfg.get('course_name') or code
    filename = ics_cfg.get('file') or f"{code}.ics"
    course_key = re.sub(r'[^A-Za-z0-9]+', '-', str(cfg.get('course_id') or code)).strip('-')

    text = build_ics(events, name, tz, tz_name, course_key, now=now)
    out = os.path.join(root, filename)
    with open(out, 'w', encoding='utf-8', newline='') as f:
        f.write(text)
    count = text.count('BEGIN:VEVENT')
    print(f"Wrote {count} events to {filename}.")

    if not do_publish:
        print("Run 'cqs ics --publish' to put it where Outlook can subscribe to it.")
        return 0

    gist_id = str(ics_cfg.get('gist') or '')
    gist_id, url, created = publish(text, os.path.basename(filename), gist_id,
                                    f"{name}: course schedule (CanvasQuartoSync)", gh=gh)
    if created:
        remember_gist(root, gist_id)
        print("Created a secret gist and stored its id in config.toml.")
    else:
        print("Updated the gist. Subscribed calendars pick it up the next time they refresh.")
    print(f"\nSubscribe to this address (it stays the same on every publish):\n  {url}\n")
    if created:
        print("Outlook: Add calendar > Subscribe from web, paste the address, give it a name.")
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    root, rest = (argv[0], argv[1:]) if argv and not argv[0].startswith('-') else ('.', argv)
    p = argparse.ArgumentParser(
        prog='cqs ics', epilog=EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter,
        description='Write the course schedule (schedule.yaml) as an .ics calendar file.')
    p.add_argument('--publish', action='store_true',
                   help='Also put the file on a secret GitHub gist that calendars can subscribe to.')
    a = p.parse_args(rest)
    try:
        return export(os.path.abspath(root), a.publish)
    except IcsError as e:
        print(f"cqs ics: {e}", file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
