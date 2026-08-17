"""Calendar events: local wall-clock in, UTC out, and no duplicates.

Events used to be sent with a hardcoded ``Z``, so a 10:15 lecture landed at
12:15 local in summer. Converting them exposed a second problem: the duplicate
check compared the local time against ``start_at``, which Canvas always returns
in UTC. These tests pin both.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from handlers.calendar_handler import CalendarHandler
from handlers.dates import get_zone

STOCKHOLM = get_zone("Europe/Stockholm")


@pytest.fixture
def handler():
    return CalendarHandler()


@pytest.fixture
def course():
    return SimpleNamespace(id=42, time_zone="Europe/Stockholm")


@pytest.fixture
def canvas_obj():
    obj = MagicMock()
    obj.create_calendar_event.return_value = MagicMock()
    return obj


def _payload(canvas_obj):
    return canvas_obj.create_calendar_event.call_args[1]['calendar_event']


def _existing(title, start_at, location=''):
    """An event as Canvas hands it back: UTC, regardless of what was sent."""
    return SimpleNamespace(title=title, start_at=start_at, location_name=location)


class TestLocalTimeConversion:

    def test_summer_event_uses_cest(self, handler, course, canvas_obj):
        handler._create_single_event(
            course, {'title': 'Lecture', 'date': '2026-08-17', 'time': '10:15-12:00'},
            canvas_obj, [], tz=STOCKHOLM)

        payload = _payload(canvas_obj)
        assert payload['start_at'] == '2026-08-17T08:15:00Z'   # +02:00
        assert payload['end_at'] == '2026-08-17T10:00:00Z'

    def test_winter_event_uses_cet(self, handler, course, canvas_obj):
        handler._create_single_event(
            course, {'title': 'Lecture', 'date': '2026-11-17', 'time': '10:15-12:00'},
            canvas_obj, [], tz=STOCKHOLM)

        assert _payload(canvas_obj)['start_at'] == '2026-11-17T09:15:00Z'  # +01:00

    def test_recurring_series_holds_local_time_across_dst(self, handler, course, canvas_obj):
        """The clocks change mid-series; 10:15 must stay 10:15 for students."""
        handler._handle_recurring_series(
            course,
            {'title': 'Seminar', 'start_date': '2026-10-19', 'end_date': '2026-11-02',
             'days': ['Mon'], 'time': '10:15-12:00'},
            canvas_obj, [], STOCKHOLM)

        starts = [c[1]['calendar_event']['start_at']
                  for c in canvas_obj.create_calendar_event.call_args_list]
        # DST ends 2026-10-25, so the offset must change partway through.
        assert starts == [
            '2026-10-19T08:15:00Z',   # CEST
            '2026-10-26T09:15:00Z',   # CET
            '2026-11-02T09:15:00Z',   # CET
        ]


class TestDuplicateDetection:

    def test_skips_event_canvas_already_has(self, handler, course, canvas_obj):
        existing = [_existing('Lecture', '2026-08-17T08:15:00Z')]

        handler._create_single_event(
            course, {'title': 'Lecture', 'date': '2026-08-17', 'time': '10:15-12:00'},
            canvas_obj, existing, tz=STOCKHOLM)

        canvas_obj.create_calendar_event.assert_not_called()

    def test_creates_when_the_time_differs(self, handler, course, canvas_obj):
        existing = [_existing('Lecture', '2026-08-17T08:15:00Z')]

        handler._create_single_event(
            course, {'title': 'Lecture', 'date': '2026-08-17', 'time': '14:00-15:00'},
            canvas_obj, existing, tz=STOCKHOLM)

        canvas_obj.create_calendar_event.assert_called_once()

    def test_creates_when_the_location_differs(self, handler, course, canvas_obj):
        existing = [_existing('Lecture', '2026-08-17T08:15:00Z', location='A1')]

        handler._create_single_event(
            course, {'title': 'Lecture', 'date': '2026-08-17', 'time': '10:15-12:00',
                     'location': 'B2'},
            canvas_obj, existing, tz=STOCKHOLM)

        canvas_obj.create_calendar_event.assert_called_once()

    def test_syncing_twice_does_not_duplicate(self, handler, course, canvas_obj):
        """The regression: a local-time substring never matches a UTC string."""
        event = {'title': 'Lecture', 'date': '2026-08-17', 'time': '10:15-12:00'}
        existing = []

        handler._create_single_event(course, event, canvas_obj, existing, tz=STOCKHOLM)
        # Canvas echoes the stored event back in UTC on the next run.
        second_run = [_existing('Lecture', _payload(canvas_obj)['start_at'])]
        canvas_obj.create_calendar_event.reset_mock()

        handler._create_single_event(course, event, canvas_obj, second_run, tz=STOCKHOLM)
        canvas_obj.create_calendar_event.assert_not_called()

    def test_malformed_existing_event_does_not_crash(self, handler, course, canvas_obj):
        handler._create_single_event(
            course, {'title': 'Lecture', 'date': '2026-08-17', 'time': '10:15-12:00'},
            canvas_obj, [_existing('Lecture', None)], tz=STOCKHOLM)

        canvas_obj.create_calendar_event.assert_called_once()
