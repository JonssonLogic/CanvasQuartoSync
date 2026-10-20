"""What a sync does with each combination of "source changed" and "Canvas changed".

    source   Canvas    solo                      team
    same     same      skip                      skip
    changed  same      publish                   publish
    same     changed   keep Canvas, save copy    keep Canvas, save copy
    changed  changed   overwrite, save copy      refuse, save copy

--accept-drift publishes over a Canvas edit in both modes. Before this, a
Canvas edit went unnoticed until the source changed and was then overwritten.
"""

import json
import os
from unittest.mock import MagicMock, patch

import pytest

from handlers.assignment_handler import AssignmentHandler
from handlers.content_utils import load_sync_map, save_sync_map
from handlers.drift_detector import (
    assignment_settings_hash, check_all_drift, compute_content_hash,
)
from handlers.fingerprint import source_fingerprint
from handlers.page_handler import PageHandler
from handlers.sync_policy import DRIFT_DIR, SOLO, TEAM, SyncPolicy, get_policy, set_policy

PAGE_ID = 777
PUBLISHED = "<p>Published</p>"
EDITED = "<p>Fixed a typo in Canvas</p>"
REL = "01_Mod/01_Page.qmd"


@pytest.fixture(autouse=True)
def fresh_policy():
    set_policy(SyncPolicy())
    yield
    set_policy(SyncPolicy())


def _setup(tmp_path, source_changed, canvas_body):
    root = tmp_path
    mod = root / "01_Mod"
    mod.mkdir()
    page = mod / "01_Page.qmd"
    page.write_text("---\ntitle: Page\ncanvas:\n  type: page\n---\n\nBody.\n", encoding="utf-8")
    fp = source_fingerprint(str(page), str(root))
    save_sync_map(str(root), {REL: {
        "id": PAGE_ID,
        "source_hash": "stale" if source_changed else fp,
        "canvas_hash": compute_content_hash(PUBLISHED),
    }})

    page_obj = MagicMock()
    page_obj.page_id = PAGE_ID
    page_obj.url = "page"
    page_obj.title = "Page"
    page_obj.body = canvas_body
    page_obj.updated_at = "2026-10-14T09:00:00Z"
    course = MagicMock()
    course.get_page.return_value = page_obj
    return str(root), str(page), course, page_obj


def _sync(root, page, course):
    with patch("handlers.page_handler.process_content", side_effect=lambda c, *a, **k: c), \
         patch.object(PageHandler, "render_quarto_document", return_value="<p>New</p>"):
        PageHandler().sync(page, course, module=None, content_root=root)


def _outcomes():
    return [e.outcome for e in get_policy().events]


class TestFourCases:

    def test_nothing_changed_skips(self, tmp_path):
        root, page, course, page_obj = _setup(tmp_path, False, PUBLISHED)
        _sync(root, page, course)
        assert not page_obj.edit.called
        assert _outcomes() == []

    def test_source_changed_publishes(self, tmp_path):
        root, page, course, page_obj = _setup(tmp_path, True, PUBLISHED)
        _sync(root, page, course)
        assert page_obj.edit.called
        assert load_sync_map(root)[REL]["source_hash"] == source_fingerprint(page, root)

    def test_canvas_edit_on_unchanged_source_is_kept_and_saved(self, tmp_path):
        root, page, course, page_obj = _setup(tmp_path, False, EDITED)
        _sync(root, page, course)
        assert not page_obj.edit.called
        assert _outcomes() == ["kept"]
        saved = os.path.join(root, DRIFT_DIR, "01_Mod__01_Page.qmd.canvas.html")
        assert open(saved, encoding="utf-8").read() == EDITED

    def test_conflict_in_solo_overwrites_but_saves_canvas_first(self, tmp_path):
        root, page, course, page_obj = _setup(tmp_path, True, EDITED)
        _sync(root, page, course)
        assert page_obj.edit.called
        assert _outcomes() == ["overwritten"]
        assert os.path.exists(os.path.join(root, DRIFT_DIR, "01_Mod__01_Page.qmd.canvas.html"))

    def test_conflict_in_team_is_refused(self, tmp_path):
        set_policy(SyncPolicy(workflow=TEAM))
        root, page, course, page_obj = _setup(tmp_path, True, EDITED)
        _sync(root, page, course)
        assert not page_obj.edit.called, "team mode overwrote a colleague's Canvas edit"
        assert _outcomes() == ["refused"]
        assert get_policy().report() is True   # exit code 2

    def test_kept_edit_is_not_a_failure(self, tmp_path):
        root, page, course, _ = _setup(tmp_path, False, EDITED)
        _sync(root, page, course)
        assert get_policy().report() is False


class TestAcceptDrift:

    def test_accept_publishes_over_a_conflict_in_team(self, tmp_path):
        set_policy(SyncPolicy(workflow=TEAM, accept={REL}))
        root, page, course, page_obj = _setup(tmp_path, True, EDITED)
        _sync(root, page, course)
        assert page_obj.edit.called
        assert _outcomes() == ["accepted"]

    def test_accept_republishes_unchanged_source_over_canvas_edit(self, tmp_path):
        set_policy(SyncPolicy(accept={"all"}))
        root, page, course, page_obj = _setup(tmp_path, False, EDITED)
        _sync(root, page, course)
        assert page_obj.edit.called

    def test_accept_for_another_file_does_not_apply(self, tmp_path):
        set_policy(SyncPolicy(workflow=TEAM, accept={"01_Mod/99_Other.qmd"}))
        root, page, course, page_obj = _setup(tmp_path, True, EDITED)
        _sync(root, page, course)
        assert not page_obj.edit.called


class TestMtimeMigration:
    """Maps written before fingerprints carry an mtime. They convert themselves."""

    def _legacy(self, tmp_path, mtime_matches):
        root, page, course, page_obj = _setup(tmp_path, False, PUBLISHED)
        m = load_sync_map(root)
        m[REL].pop("source_hash")
        m[REL]["mtime"] = os.path.getmtime(page) if mtime_matches else 1.0
        save_sync_map(root, m)
        return root, page, course, page_obj

    def test_matching_mtime_records_fingerprint_without_rendering(self, tmp_path):
        root, page, course, page_obj = self._legacy(tmp_path, True)
        _sync(root, page, course)
        assert not page_obj.edit.called
        entry = load_sync_map(root)[REL]
        assert entry["source_hash"] == source_fingerprint(page, root)
        assert "mtime" not in entry

    def test_stale_mtime_renders_once(self, tmp_path):
        root, page, course, page_obj = self._legacy(tmp_path, False)
        _sync(root, page, course)
        assert page_obj.edit.called
        assert "mtime" not in load_sync_map(root)[REL]


class TestAssignmentSettings:
    """A due date moved in Canvas is an edit, like a changed description."""

    def test_settings_change_alone_is_drift(self, tmp_path):
        root = tmp_path
        (root / "01_Mod").mkdir()
        f = root / "01_Mod" / "01_Lab.qmd"
        f.write_text("---\ntitle: Lab\ncanvas:\n  type: assignment\n---\n\nDo it.\n",
                     encoding="utf-8")
        a = MagicMock(spec=["id", "name", "description", "points_possible", "due_at",
                            "unlock_at", "lock_at", "grading_type", "submission_types",
                            "allowed_extensions", "allowed_attempts", "omit_from_final_grade",
                            "updated_at", "edit", "hide_in_gradebook"])
        a.id, a.name, a.description = 5, "Lab", PUBLISHED
        a.points_possible, a.due_at, a.unlock_at, a.lock_at = 10.0, "2026-10-01T21:59:00Z", None, None
        a.grading_type, a.submission_types, a.allowed_extensions = "points", ["online_upload"], []
        a.allowed_attempts, a.omit_from_final_grade, a.updated_at = -1, False, ""
        a.hide_in_gradebook = False
        published_settings = assignment_settings_hash(a)
        save_sync_map(str(root), {"01_Mod/01_Lab.qmd": {
            "id": 5, "source_hash": source_fingerprint(str(f), str(root)),
            "canvas_hash": compute_content_hash(PUBLISHED),
            "canvas_settings_hash": published_settings,
        }})
        a.due_at = "2026-10-08T21:59:00Z"   # a colleague gave everyone a week more
        course = MagicMock()
        course.get_assignment.return_value = a
        course.time_zone = "Europe/Stockholm"

        with patch("handlers.assignment_handler.process_content", side_effect=lambda c, *a, **k: c):
            AssignmentHandler().sync(str(f), course, module=None, content_root=str(root))

        assert not a.edit.called
        assert [(e.outcome, e.what) for e in get_policy().events] == [("kept", "settings")]


class TestCheckAllDriftUsesKind:
    """A New Quiz's id is also an assignment id. Guessing would compare the quiz
    against the assignment's description and report drift on every quiz."""

    def test_new_quiz_entry_is_not_checked_as_an_assignment(self, tmp_path):
        save_sync_map(str(tmp_path), {"01_Mod/01_Quiz.qmd": {
            "id": "55", "canvas_hash": compute_content_hash("quiz text"),
            "canvas_kind": "new_quiz",
        }})
        course = MagicMock()
        with patch("handlers.drift_detector._fetch_quiz_text",
                   return_value=("quiz text", "Quiz", None)):
            drifted = check_all_drift(course, str(tmp_path), include_diff=False)
        assert drifted == []
        assert not course.get_assignment.called
        assert not course.get_page.called

    def test_unfetchable_quiz_is_reported_not_silently_clean(self, tmp_path, caplog):
        save_sync_map(str(tmp_path), {"01_Mod/01_Quiz.qmd": {
            "id": "55", "canvas_hash": "x", "canvas_kind": "new_quiz",
        }})
        with patch("handlers.drift_detector._fetch_quiz_text",
                   return_value=(None, "01_Mod/01_Quiz.qmd", None)), \
             patch("handlers.drift_detector.logger") as log:
            check_all_drift(MagicMock(), str(tmp_path), include_diff=False)
        assert any("Could not check" in str(c) for c in log.warning.call_args_list)


class TestMapFormat:

    def test_sorted_utf8_lf(self, tmp_path):
        save_sync_map(str(tmp_path), {"b.qmd": {"id": 2}, "_course_id": 1,
                                      "02_Föreläsningar/a.qmd": {"id": 1}})
        raw = open(tmp_path / ".canvas_sync_map.json", "rb").read()
        assert b"\r\n" not in raw
        text = raw.decode("utf-8")
        assert "Föreläsningar" in text
        keys = list(json.loads(text).keys())
        assert keys == sorted(keys)
