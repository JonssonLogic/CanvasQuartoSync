"""
Date normalisation: local wall-clock in, UTC out.

The point of handlers/dates.py is that an author writes the time students should
see ("09:00") and gets the right instant on both sides of a daylight-saving
boundary. These tests pin that, plus the back-compatibility rule that any value
already carrying Z or an offset is never reinterpreted.
"""

import datetime

import pytest

from handlers.dates import (
    DateError,
    dst_anomaly,
    get_zone,
    parse_canvas_utc,
    to_canvas_iso,
    to_local_naive,
)

STOCKHOLM = get_zone("Europe/Stockholm")


class TestDaylightSaving:
    """The reason this module exists."""

    def test_same_wall_clock_maps_to_different_instants_across_dst(self):
        summer = to_canvas_iso("2026-08-17T09:00:00", STOCKHOLM)
        winter = to_canvas_iso("2026-11-17T09:00:00", STOCKHOLM)

        assert summer == "2026-08-17T07:00:00Z"   # CEST, +02:00
        assert winter == "2026-11-17T08:00:00Z"   # CET,  +01:00
        assert summer[11:13] != winter[11:13], "DST offset was not applied"

    def test_deadline_stays_at_the_same_local_time_all_semester(self):
        # A weekly 23:59 deadline must read 23:59 to students in every week,
        # which is exactly what a fixed "...Z" literal fails to do.
        for date in ("2026-09-06", "2026-10-18", "2026-10-25", "2026-12-06"):
            utc = to_canvas_iso(f"{date}T23:59:00", STOCKHOLM)
            back = parse_canvas_utc(utc).astimezone(STOCKHOLM)
            assert (back.hour, back.minute) == (23, 59)


class TestPassThrough:
    """Explicit instants are never reinterpreted - existing content is safe."""

    @pytest.mark.parametrize("value", [
        "2026-08-17T09:00:00Z",
        "2026-08-17T09:00:00+02:00",
        "2026-11-17T09:00:00-05:00",
        "2026-08-17T09:00:00+0200",
    ])
    def test_offset_values_are_returned_unchanged(self, value):
        assert to_canvas_iso(value, STOCKHOLM) == value

    def test_pass_through_works_without_any_timezone(self):
        # An all-Z course needs no configuration at all.
        assert to_canvas_iso("2026-08-17T09:00:00Z", None) == "2026-08-17T09:00:00Z"

    def test_aware_datetime_is_converted_not_reinterpreted(self):
        aware = datetime.datetime(2026, 8, 17, 9, 0, tzinfo=datetime.timezone.utc)
        assert to_canvas_iso(aware, STOCKHOLM) == "2026-08-17T09:00:00Z"


class TestClearingDates:
    """Omitting a key clears the date in Canvas; '' is what does that."""

    @pytest.mark.parametrize("value", [None, "", "   "])
    def test_empty_values_become_empty_string(self, value):
        assert to_canvas_iso(value, STOCKHOLM) == ""


class TestAcceptedSpellings:

    @pytest.mark.parametrize("value,expected", [
        ("2026-08-17T09:00:00", "2026-08-17T07:00:00Z"),
        ("2026-08-17 09:00:00", "2026-08-17T07:00:00Z"),
        ("2026-08-17T09:00", "2026-08-17T07:00:00Z"),
        ("2026-08-17", "2026-08-16T22:00:00Z"),          # midnight, course local
    ])
    def test_naive_strings(self, value, expected):
        assert to_canvas_iso(value, STOCKHOLM) == expected

    def test_yaml_datetime_object(self):
        # What PyYAML builds from an unquoted frontmatter timestamp.
        naive = datetime.datetime(2026, 8, 17, 9, 0)
        assert to_canvas_iso(naive, STOCKHOLM) == "2026-08-17T07:00:00Z"

    def test_yaml_date_object(self):
        assert to_canvas_iso(datetime.date(2026, 8, 17), STOCKHOLM) == "2026-08-16T22:00:00Z"

    def test_result_is_always_a_string(self):
        # The New Quizzes payload is JSON-serialised; a datetime would crash it.
        import json
        for value in [datetime.datetime(2026, 8, 17, 9, 0), datetime.date(2026, 8, 17),
                      "2026-08-17T09:00:00", "2026-08-17T09:00:00Z", None]:
            out = to_canvas_iso(value, STOCKHOLM)
            assert isinstance(out, str)
            json.dumps({"due_at": out})


class TestErrors:

    def test_naive_value_without_timezone_is_actionable(self):
        with pytest.raises(DateError) as excinfo:
            to_canvas_iso("2026-08-17T09:00:00", None, field="due_at")
        message = str(excinfo.value)
        assert "due_at" in message
        assert "config.toml" in message

    def test_unparseable_value(self):
        with pytest.raises(DateError):
            to_canvas_iso("next tuesday", STOCKHOLM, field="due_at")

    def test_unknown_zone_name_mentions_tzdata(self):
        with pytest.raises(DateError) as excinfo:
            get_zone("Europe/Atlantis")
        assert "tzdata" in str(excinfo.value)


class TestDstAnomaly:
    """Advisory only - both cases still resolve, the validator just warns."""

    def test_gap(self):
        # 2026-03-29 02:30 does not exist in Stockholm (clocks jump 02:00 -> 03:00).
        assert dst_anomaly(datetime.datetime(2026, 3, 29, 2, 30), STOCKHOLM) == "gap"

    def test_ambiguous(self):
        # 2026-10-25 02:30 happens twice.
        assert dst_anomaly(datetime.datetime(2026, 10, 25, 2, 30), STOCKHOLM) == "ambiguous"

    def test_ordinary_time_is_clean(self):
        assert dst_anomaly(datetime.datetime(2026, 8, 17, 9, 0), STOCKHOLM) is None

    def test_no_timezone_is_clean(self):
        assert dst_anomaly(datetime.datetime(2026, 3, 29, 2, 30), None) is None


class TestImportDirection:
    """Round-trip: what Canvas returns should read as the author would write it."""

    def test_utc_becomes_local_wall_clock(self):
        assert to_local_naive("2026-09-16T21:59:00Z", STOCKHOLM) == "2026-09-16T23:59:00"

    def test_round_trip_preserves_the_instant(self):
        original = "2026-11-17T08:00:00Z"
        local = to_local_naive(original, STOCKHOLM)
        assert to_canvas_iso(local, STOCKHOLM) == original

    def test_falls_back_to_utc_without_a_timezone(self):
        assert to_local_naive("2026-09-16T21:59:00Z", None) == "2026-09-16T21:59:00Z"


class TestParseCanvasUtc:

    def test_parses_z_form(self):
        parsed = parse_canvas_utc("2026-08-17T07:00:00Z")
        assert parsed == datetime.datetime(2026, 8, 17, 7, 0, tzinfo=datetime.timezone.utc)

    def test_returns_none_for_junk(self):
        assert parse_canvas_utc("not a date") is None
        assert parse_canvas_utc(None) is None
