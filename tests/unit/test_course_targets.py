"""One folder, several Canvas courses: the [courses] table and a sync map per course."""

import json
import os

import pytest

from handlers import config as cfgmod
from handlers.config import (CourseConfigError, get_course_id, resolve_course_ref,
                             get_active_course, course_id_error)
from handlers.content_utils import (MAP_COURSE_KEY, SYNC_MAP_NAME, all_sync_map_paths,
                                    get_sync_map_path, is_sync_map_file, load_sync_map,
                                    rekey_all_sync_maps, save_sync_map,
                                    verify_sync_map_course)

TWO_COURSES = """
course_id = "{target}"

[courses]
live     = 17671
training = 74
"""


def _course(tmp_path, target="live", toml=TWO_COURSES):
    (tmp_path / "config.toml").write_text(toml.format(target=target), encoding="utf-8")
    cfgmod._config_cache.pop(str(tmp_path), None)
    return str(tmp_path)


def _read(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# --- resolving course_id ---

def test_a_name_resolves_through_the_courses_table(tmp_path):
    root = _course(tmp_path, "live")
    assert get_course_id(root) == "17671"
    assert get_active_course(root) == "17671"


def test_names_match_without_regard_to_case(tmp_path):
    root = _course(tmp_path, "Training")
    assert get_course_id(root) == "74"


def test_cli_override_takes_a_name_too(tmp_path):
    root = _course(tmp_path, "live")
    assert get_course_id(root, "training") == "74"
    assert get_course_id(root, "12345") == "12345"


def test_a_plain_number_needs_no_table(tmp_path):
    root = _course(tmp_path, toml='course_id = 17671\n')
    assert get_course_id(root) == "17671"


def test_an_unknown_name_is_reported_not_guessed(tmp_path):
    root = _course(tmp_path, "traning")
    assert get_course_id(root) is None
    assert '"traning"' in course_id_error(root)
    assert '"live"' in course_id_error(root)


def test_a_name_without_a_table_says_how_to_add_one(tmp_path):
    root = _course(tmp_path, toml='course_id = "live"\n')
    with pytest.raises(CourseConfigError, match=r"\[courses\]"):
        resolve_course_ref(root, "live")


# --- one map per course ---

def _sandbox_map(root):
    """The state a folder is in after syncing to the sandbox only."""
    save_sync_map(root, {MAP_COURSE_KEY: 74, "a.qmd": {"id": 1}})


def test_a_named_second_course_starts_its_own_map(tmp_path):
    root = _course(tmp_path)
    _sandbox_map(root)
    assert verify_sync_map_course(root, 17671) is True
    own = os.path.join(root, ".canvas_sync_map.17671.json")
    assert get_sync_map_path(root) == own
    save_sync_map(root, dict(load_sync_map(root), **{"a.qmd": {"id": 900}}))
    # The sandbox map is untouched; the live one has live ids.
    assert _read(os.path.join(root, SYNC_MAP_NAME))["a.qmd"] == {"id": 1}
    assert _read(own) == {MAP_COURSE_KEY: 17671, "a.qmd": {"id": 900}}


def test_switching_back_uses_the_first_map_again(tmp_path):
    root = _course(tmp_path)
    _sandbox_map(root)
    verify_sync_map_course(root, 17671)
    save_sync_map(root, dict(load_sync_map(root), **{"a.qmd": {"id": 900}}))
    assert verify_sync_map_course(root, 74) is True
    assert load_sync_map(root)["a.qmd"] == {"id": 1}
    assert verify_sync_map_course(root, 17671) is True
    assert load_sync_map(root)["a.qmd"] == {"id": 900}


def test_a_course_not_in_the_table_is_still_refused(tmp_path):
    """A typo in course_id must not start publishing a whole course elsewhere."""
    root = _course(tmp_path, toml='course_id = 99999\n')
    _sandbox_map(root)
    assert verify_sync_map_course(root, 99999) is False
    assert [os.path.basename(p) for p in all_sync_map_paths(root)] == [SYNC_MAP_NAME]


def test_a_refusal_leaves_readers_on_the_existing_map(tmp_path):
    root = _course(tmp_path, toml='course_id = 99999\n')
    _sandbox_map(root)
    get_course_id(root)                     # makes 99999 active
    verify_sync_map_course(root, 99999)
    assert load_sync_map(root)["a.qmd"] == {"id": 1}


def test_a_single_course_folder_keeps_the_plain_name(tmp_path):
    root = _course(tmp_path, toml='course_id = 17671\n')
    assert verify_sync_map_course(root, 17671) is True
    save_sync_map(root, dict(load_sync_map(root), **{"a.qmd": {"id": 5}}))
    assert [os.path.basename(p) for p in all_sync_map_paths(root)] == [SYNC_MAP_NAME]


def test_a_hand_copied_map_for_the_wrong_course_is_refused(tmp_path):
    root = _course(tmp_path)
    _sandbox_map(root)
    with open(os.path.join(root, ".canvas_sync_map.17671.json"), "w", encoding="utf-8") as f:
        json.dump({MAP_COURSE_KEY: 74, "a.qmd": {"id": 1}}, f)
    assert verify_sync_map_course(root, 17671) is False


# --- renames follow every course ---

def test_a_rename_moves_the_key_in_every_map(tmp_path):
    root = _course(tmp_path)
    _sandbox_map(root)
    verify_sync_map_course(root, 17671)
    save_sync_map(root, dict(load_sync_map(root), **{"a.qmd": {"id": 900}}))
    assert rekey_all_sync_maps(root, {"a.qmd": "b.qmd"}) == 2
    for path in all_sync_map_paths(root):
        m = _read(path)
        assert "a.qmd" not in m and "b.qmd" in m


def test_rekey_can_skip_the_map_already_done(tmp_path):
    root = _course(tmp_path)
    _sandbox_map(root)
    verify_sync_map_course(root, 17671)
    save_sync_map(root, {MAP_COURSE_KEY: 17671, "b.qmd": {"id": 900}})
    assert rekey_all_sync_maps(root, {"a.qmd": "b.qmd"}, skip=get_sync_map_path(root)) == 1
    assert _read(os.path.join(root, SYNC_MAP_NAME))["b.qmd"] == {"id": 1}


def test_sync_map_file_names():
    assert is_sync_map_file(".canvas_sync_map.json")
    assert is_sync_map_file(".canvas_sync_map.17671.json")
    assert not is_sync_map_file(".canvas_sync_map.backup.json")
    assert not is_sync_map_file("canvas_sync_map.json")
