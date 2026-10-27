"""cqs ics: schedule.yaml as a calendar file that Outlook subscribes to.

What matters is that a calendar app sees the times the teacher wrote, on both
sides of a daylight-saving change, and that running it again updates the same
calendar instead of making a new one: stable event ids, and the same gist.
"""

import datetime
import types

import pytest

import cqs
import ics_export
from handlers.config import _config_cache
from handlers.dates import get_zone

STOCKHOLM = get_zone("Europe/Stockholm")
NOW = datetime.datetime(2026, 10, 27, 12, 0, tzinfo=datetime.timezone.utc)


@pytest.fixture(autouse=True)
def fresh_config():
    _config_cache.clear()
    yield
    _config_cache.clear()


@pytest.fixture
def course(tmp_path):
    root = tmp_path / "Course"
    root.mkdir()
    (root / "config.toml").write_text(
        'course_id = 19155\ncourse_name = "Konstruktion och Mekatronik 1"\n'
        'course_code = "T1H2UO"\ntimezone = "Europe/Stockholm"\n', encoding="utf-8")
    (root / "schedule.yaml").write_text(
        'events:\n'
        '  - title: "Kursstart"\n'
        '    date: "2026-10-29"\n'
        '    time: "09:00-12:00"\n'
        '    description: "Sal: se anslagstavlan."\n'
        '  - title: "Labb"\n'
        '    start_date: "2026-10-22"\n'
        '    end_date: "2026-11-05"\n'
        '    days: ["Thu"]\n'
        '    time: "13:00-15:30"\n', encoding="utf-8")
    return root


def events_of(text):
    return [block for block in text.split("BEGIN:VEVENT")[1:]]


class TestOccurrences:

    def test_a_series_is_expanded_inclusive(self):
        evs = [{"title": "L", "start_date": "2026-10-22", "end_date": "2026-11-05", "days": ["Thu"]}]
        dates = [d.isoformat() for d, _ in ics_export.occurrences(evs)]
        assert dates == ["2026-10-22", "2026-10-29", "2026-11-05"]

    def test_unquoted_yaml_dates_work(self):
        evs = [{"title": "K", "date": datetime.date(2026, 10, 29)}]
        assert [d for d, _ in ics_export.occurrences(evs)] == [datetime.date(2026, 10, 29)]

    def test_an_unknown_day_is_reported(self):
        evs = [{"title": "L", "start_date": "2026-10-22", "end_date": "2026-11-05", "days": ["Tor"]}]
        with pytest.raises(ics_export.IcsError, match="Tor"):
            list(ics_export.occurrences(evs))

    def test_a_bad_date_is_reported(self):
        with pytest.raises(ics_export.IcsError, match="YYYY-MM-DD"):
            list(ics_export.occurrences([{"title": "K", "date": "29/10"}]))


class TestBuild:

    def test_local_times_become_utc_on_both_sides_of_dst(self):
        evs = [{"title": "Summer", "date": "2026-10-22", "time": "13:00-15:30"},
               {"title": "Winter", "date": "2026-10-29", "time": "13:00-15:30"}]
        text = ics_export.build_ics(evs, "C", STOCKHOLM, now=NOW)
        assert "DTSTART:20261022T110000Z" in text   # CEST, UTC+2
        assert "DTSTART:20261029T120000Z" in text   # CET, UTC+1
        assert "DTEND:20261029T143000Z" in text

    def test_missing_time_uses_the_calendar_sync_default(self):
        text = ics_export.build_ics([{"title": "K", "date": "2026-11-05"}], "C", STOCKHOLM, now=NOW)
        assert "DTSTART:20261105T110000Z" in text

    def test_a_bad_time_is_reported(self):
        with pytest.raises(ics_export.IcsError, match="09:00-12:00"):
            ics_export.build_ics([{"title": "K", "date": "2026-11-05", "time": "9-12"}],
                                 "C", STOCKHOLM, now=NOW)

    def test_text_is_escaped(self):
        evs = [{"title": "A, B; C", "date": "2026-11-05", "description": "rad 1\nrad 2"}]
        text = ics_export.build_ics(evs, "C", STOCKHOLM, now=NOW)
        assert "SUMMARY:A\\, B\\; C" in text
        assert "DESCRIPTION:rad 1\\nrad 2" in text

    def test_long_lines_are_folded_within_75_octets(self):
        evs = [{"title": "Ä" * 100, "date": "2026-11-05"}]
        text = ics_export.build_ics(evs, "C", STOCKHOLM, now=NOW)
        for line in text.split("\r\n"):
            assert len(line.encode("utf-8")) <= 75
        unfolded = text.replace("\r\n ", "")
        assert "SUMMARY:" + "Ä" * 100 in unfolded

    def test_crlf_and_calendar_header(self):
        text = ics_export.build_ics([{"title": "K", "date": "2026-11-05"}], "Kurs, HT", STOCKHOLM,
                                    "Europe/Stockholm", now=NOW)
        assert text.startswith("BEGIN:VCALENDAR\r\nVERSION:2.0\r\n")
        assert text.endswith("END:VCALENDAR\r\n")
        assert "X-WR-CALNAME:Kurs\\, HT" in text
        assert "REFRESH-INTERVAL;VALUE=DURATION:PT1H" in text
        assert "X-WR-TIMEZONE:Europe/Stockholm" in text

    def test_uids_are_stable_and_follow_the_event(self):
        evs = [{"title": "K", "date": "2026-11-05", "time": "09:00-12:00"}]
        first = ics_export.build_ics(evs, "C", STOCKHOLM, now=NOW)
        later = ics_export.build_ics(evs, "C", STOCKHOLM, now=NOW + datetime.timedelta(days=3))
        uid = [l for l in first.split("\r\n") if l.startswith("UID:")]
        assert uid == [l for l in later.split("\r\n") if l.startswith("UID:")]
        moved = ics_export.build_ics([{**evs[0], "time": "10:00-12:00"}], "C", STOCKHOLM, now=NOW)
        assert uid != [l for l in moved.split("\r\n") if l.startswith("UID:")]

    def test_events_are_in_time_order(self):
        evs = [{"title": "B", "date": "2026-11-05", "time": "13:00-15:00"},
               {"title": "A", "date": "2026-11-05", "time": "09:00-12:00"}]
        text = ics_export.build_ics(evs, "C", STOCKHOLM, now=NOW)
        assert text.index("SUMMARY:A") < text.index("SUMMARY:B")


class FakeGitHub:
    """Stands in for github_course.GitHub: records calls, answers like the API."""

    def __init__(self, missing=False):
        self.calls, self.missing = [], missing

    def _call(self, method, path, **kw):
        self.calls.append((method, path, kw.get("json")))
        status = 404 if (self.missing and method == "PATCH") else 200
        gid = path.rsplit("/", 1)[-1] if method == "PATCH" else "abc123"
        return types.SimpleNamespace(status_code=status,
                                     data={"id": gid, "owner": {"login": "cenmir"}})

    def _ok(self, r, what):
        return r.data


class TestExport:

    def test_writes_the_file_and_publishes_nothing(self, course, capsys):
        assert ics_export.export(str(course), now=NOW) == 0
        text = (course / "T1H2UO.ics").read_text(encoding="utf-8")
        assert len(events_of(text)) == 4
        assert "X-WR-CALNAME:Konstruktion och Mekatronik 1" in text
        assert "--publish" in capsys.readouterr().out

    def test_name_and_file_from_the_ics_table(self, course):
        with open(course / "config.toml", "a", encoding="utf-8") as f:
            f.write('\n[ics]\nname = "KoM1"\nfile = "kom1.ics"\n')
        ics_export.export(str(course), now=NOW)
        assert "X-WR-CALNAME:KoM1" in (course / "kom1.ics").read_text(encoding="utf-8")

    def test_first_publish_creates_a_secret_gist_and_remembers_it(self, course, capsys):
        gh = FakeGitHub()
        ics_export.export(str(course), do_publish=True, gh=gh, now=NOW)
        method, path, body = gh.calls[0]
        assert (method, path) == ("POST", "/gists")
        assert body["public"] is False
        assert "T1H2UO.ics" in body["files"]
        assert 'gist = "abc123"' in (course / "config.toml").read_text(encoding="utf-8")
        assert ("https://gist.githubusercontent.com/cenmir/abc123/raw/T1H2UO.ics"
                in capsys.readouterr().out)

    def test_the_next_publish_updates_the_same_gist(self, course):
        with open(course / "config.toml", "a", encoding="utf-8") as f:
            f.write('\n[ics]\ngist = "abc123"\n')
        gh = FakeGitHub()
        ics_export.export(str(course), do_publish=True, gh=gh, now=NOW)
        assert gh.calls[0][:2] == ("PATCH", "/gists/abc123")
        assert (course / "config.toml").read_text(encoding="utf-8").count("gist =") == 1

    def test_a_gist_someone_else_owns_is_explained(self, course):
        with open(course / "config.toml", "a", encoding="utf-8") as f:
            f.write('\n[ics]\ngist = "gone"\n')
        with pytest.raises(ics_export.IcsError, match="published first"):
            ics_export.export(str(course), do_publish=True, gh=FakeGitHub(missing=True), now=NOW)

    def test_gist_is_added_under_an_existing_ics_table(self, course):
        with open(course / "config.toml", "a", encoding="utf-8") as f:
            f.write('\n[ics]\nname = "KoM1"\n')
        ics_export.export(str(course), do_publish=True, gh=FakeGitHub(), now=NOW)
        _config_cache.clear()
        from handlers.config import load_config
        assert load_config(str(course))["ics"] == {"name": "KoM1", "gist": "abc123"}

    def test_no_schedule(self, course):
        (course / "schedule.yaml").unlink()
        with pytest.raises(ics_export.IcsError, match="schedule.yaml"):
            ics_export.export(str(course))

    def test_no_timezone(self, course):
        (course / "config.toml").write_text('course_code = "X"\n', encoding="utf-8")
        with pytest.raises(ics_export.IcsError, match="timezone"):
            ics_export.export(str(course))

    def test_main_reports_problems_without_a_traceback(self, course, capsys):
        (course / "schedule.yaml").unlink()
        assert ics_export.main([str(course)]) == 2
        assert "cqs ics:" in capsys.readouterr().err


def test_cqs_ics_gets_the_root(tmp_path, monkeypatch):
    root = tmp_path / "Course"
    (root / "Mod").mkdir(parents=True)
    (root / "config.toml").write_text('course_id = 1\n', encoding="utf-8")
    seen = []

    def fake_import(name):
        return types.SimpleNamespace(main=lambda: seen.append((name, list(__import__("sys").argv))) or 0)

    monkeypatch.setattr(cqs.importlib, "import_module", fake_import)
    monkeypatch.chdir(root / "Mod")
    cqs.main(["ics", "--publish"])
    assert seen == [("ics_export", ["cqs ics", str(root), "--publish"])]
