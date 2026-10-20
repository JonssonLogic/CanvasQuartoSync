"""Tests for handlers/content_utils.py — pure utility functions."""

import os
import json
from handlers.content_utils import (
    parse_module_name,
    clean_title,
    load_sync_map,
    save_sync_map,
    verify_sync_map_course,
    get_map_course_id,
    sync_map_has_entries,
    MAP_COURSE_KEY,
    get_mapped_id,
    save_mapped_id,
    safe_delete_file,
    safe_delete_dir,
)


# --- parse_module_name ---

class TestParseModuleName:

    def test_standard_prefix(self):
        assert parse_module_name("01_Introduction") == "Introduction"

    def test_high_number(self):
        assert parse_module_name("99_Last") == "Last"

    def test_no_prefix(self):
        assert parse_module_name("Introduction") == "Introduction"

    def test_single_digit_not_stripped(self):
        """Only exactly 2-digit prefix is matched."""
        assert parse_module_name("1_Intro") == "1_Intro"

    def test_three_digits_no_match(self):
        """'001_X' does NOT match — regex anchors to NN_ (exactly 2 digits before _)."""
        assert parse_module_name("001_Intro") == "001_Intro"

    def test_empty_string(self):
        assert parse_module_name("") == ""

    def test_none(self):
        assert parse_module_name(None) is None

    def test_with_spaces(self):
        assert parse_module_name("02_Python Basics") == "Python Basics"

    def test_underscores_in_name(self):
        assert parse_module_name("03_My_File_Name") == "My_File_Name"

    def test_only_prefix(self):
        """Edge case: '01_' with empty name part."""
        assert parse_module_name("01_") == ""


# --- clean_title ---

class TestCleanTitle:

    def test_strips_prefix(self):
        assert clean_title("05_Syllabus.pdf") == "Syllabus.pdf"

    def test_no_prefix(self):
        assert clean_title("Readme.md") == "Readme.md"

    def test_qmd_file(self):
        assert clean_title("02_Intro.qmd") == "Intro.qmd"


# --- Sync map I/O ---

class TestSyncMap:

    def test_load_empty(self, tmp_path):
        result = load_sync_map(str(tmp_path))
        assert result == {}

    def test_save_and_load_roundtrip(self, tmp_path):
        data = {"file.qmd": {"id": 123, "mtime": 1.0}}
        save_sync_map(str(tmp_path), data)
        loaded = load_sync_map(str(tmp_path))
        assert loaded == data

    def test_sync_map_file_created(self, tmp_path):
        save_sync_map(str(tmp_path), {"a": 1})
        assert os.path.exists(os.path.join(str(tmp_path), ".canvas_sync_map.json"))

    def test_load_corrupted_file(self, tmp_path):
        """Corrupted JSON should return empty dict, not crash."""
        path = os.path.join(str(tmp_path), ".canvas_sync_map.json")
        with open(path, "w") as f:
            f.write("{invalid json")
        result = load_sync_map(str(tmp_path))
        assert result == {}

    def test_overwrite_existing(self, tmp_path):
        save_sync_map(str(tmp_path), {"old": 1})
        save_sync_map(str(tmp_path), {"new": 2})
        loaded = load_sync_map(str(tmp_path))
        assert loaded == {"new": 2}


# --- get_mapped_id / save_mapped_id ---

class TestMappedId:

    def test_dict_entry(self, tmp_path):
        save_sync_map(str(tmp_path), {"sub/file.qmd": {"id": 42, "mtime": 1.0}})
        # Create the file path that will resolve to 'sub/file.qmd'
        sub_dir = os.path.join(str(tmp_path), "sub")
        os.makedirs(sub_dir, exist_ok=True)
        file_path = os.path.join(sub_dir, "file.qmd")
        canvas_id, meta = get_mapped_id(str(tmp_path), file_path)
        assert canvas_id == 42
        assert meta["mtime"] == 1.0

    def test_legacy_int_entry(self, tmp_path):
        save_sync_map(str(tmp_path), {"file.qmd": 99})
        file_path = os.path.join(str(tmp_path), "file.qmd")
        canvas_id, meta = get_mapped_id(str(tmp_path), file_path)
        assert canvas_id == 99
        assert meta is None

    def test_missing_entry(self, tmp_path):
        save_sync_map(str(tmp_path), {})
        file_path = os.path.join(str(tmp_path), "nonexistent.qmd")
        canvas_id, meta = get_mapped_id(str(tmp_path), file_path)
        assert canvas_id is None
        assert meta is None

    def test_save_with_source_hash(self, tmp_path):
        file_path = os.path.join(str(tmp_path), "file.qmd")
        save_mapped_id(str(tmp_path), file_path, 42, source_hash="abc123")
        sync_map = load_sync_map(str(tmp_path))
        assert sync_map["file.qmd"]["id"] == 42
        assert sync_map["file.qmd"]["source_hash"] == "abc123"
        assert "mtime" not in sync_map["file.qmd"]

    def test_save_without_mtime_legacy(self, tmp_path):
        file_path = os.path.join(str(tmp_path), "file.qmd")
        save_mapped_id(str(tmp_path), file_path, 99)
        sync_map = load_sync_map(str(tmp_path))
        assert sync_map["file.qmd"] == 99


# --- safe_delete_file / safe_delete_dir ---

class TestSafeDelete:

    def test_delete_existing_file(self, tmp_path):
        f = tmp_path / "deleteme.txt"
        f.write_text("hello")
        safe_delete_file(str(f))
        assert not f.exists()

    def test_delete_nonexistent_file_no_error(self, tmp_path):
        safe_delete_file(str(tmp_path / "nope.txt"))

    def test_delete_existing_dir(self, tmp_path):
        d = tmp_path / "subdir"
        d.mkdir()
        (d / "file.txt").write_text("content")
        safe_delete_dir(str(d))
        assert not d.exists()

    def test_delete_nonexistent_dir_no_error(self, tmp_path):
        safe_delete_dir(str(tmp_path / "nope"))


# --- sync map course identity ---
#
# Canvas ids are only meaningful inside their own course, so a map carried to a
# different course would silently edit the wrong objects. See the comment above
# MAP_COURSE_KEY in content_utils.py.

class TestSyncMapCourseIdentity:

    def test_stamps_a_fresh_folder(self, tmp_path):
        assert verify_sync_map_course(str(tmp_path), 74) is True
        assert get_map_course_id(load_sync_map(str(tmp_path))) == 74

    def test_matching_course_passes(self, tmp_path):
        save_sync_map(str(tmp_path), {MAP_COURSE_KEY: 74, "a.qmd": {"id": 1}})
        assert verify_sync_map_course(str(tmp_path), 74) is True

    def test_mismatched_course_is_refused(self, tmp_path):
        save_sync_map(str(tmp_path), {MAP_COURSE_KEY: 74, "a.qmd": {"id": 1}})
        assert verify_sync_map_course(str(tmp_path), 15406) is False

    def test_refusal_does_not_rewrite_the_map(self, tmp_path):
        """A refused sync must leave the map describing its real course."""
        save_sync_map(str(tmp_path), {MAP_COURSE_KEY: 74, "a.qmd": {"id": 1}})
        verify_sync_map_course(str(tmp_path), 15406)
        assert get_map_course_id(load_sync_map(str(tmp_path))) == 74

    def test_unstamped_map_with_entries_is_adopted(self, tmp_path):
        """Every pre-existing map looks like this; refusing would wall off upgraders."""
        save_sync_map(str(tmp_path), {"a.qmd": {"id": 1}})
        assert verify_sync_map_course(str(tmp_path), 74) is True
        assert get_map_course_id(load_sync_map(str(tmp_path))) == 74

    def test_adoption_keeps_existing_entries(self, tmp_path):
        save_sync_map(str(tmp_path), {"a.qmd": {"id": 1, "mtime": 2.0}})
        verify_sync_map_course(str(tmp_path), 74)
        assert load_sync_map(str(tmp_path))["a.qmd"] == {"id": 1, "mtime": 2.0}

    def test_string_course_id_compares_equal(self, tmp_path):
        """--course-id arrives as a string; config.toml gives an int."""
        save_sync_map(str(tmp_path), {MAP_COURSE_KEY: 74, "a.qmd": {"id": 1}})
        assert verify_sync_map_course(str(tmp_path), "74") is True
        assert verify_sync_map_course(str(tmp_path), "15406") is False

    def test_unusable_course_id_does_not_block(self, tmp_path):
        """Missing-id handling belongs to the caller, not to this guard."""
        assert verify_sync_map_course(str(tmp_path), None) is True

    def test_corrupt_stamp_is_treated_as_unmarked(self, tmp_path):
        save_sync_map(str(tmp_path), {MAP_COURSE_KEY: "not a number", "a.qmd": {"id": 1}})
        assert verify_sync_map_course(str(tmp_path), 74) is True
        assert get_map_course_id(load_sync_map(str(tmp_path))) == 74

    def test_reserved_key_is_not_counted_as_an_entry(self, tmp_path):
        assert sync_map_has_entries({MAP_COURSE_KEY: 74}) is False
        assert sync_map_has_entries({MAP_COURSE_KEY: 74, "a.qmd": {"id": 1}}) is True

    def test_reserved_key_is_inert_to_map_walkers(self, tmp_path):
        """check_all_drift is the only code that iterates the map. Its existing
        isinstance(entry, dict) guard must skip the stamp rather than treat it
        as a file entry."""
        verify_sync_map_course(str(tmp_path), 74)
        sync_map = load_sync_map(str(tmp_path))
        for rel_path, entry in sync_map.items():
            if rel_path == MAP_COURSE_KEY:
                assert not isinstance(entry, dict)
