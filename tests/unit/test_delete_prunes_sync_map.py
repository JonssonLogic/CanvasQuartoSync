"""Deleting content must also make the tool forget it.

Removing a page from the module panel used to delete the Canvas item and the
local file but leave the sync map entry and the snapshot behind. The panel
reads the map, not the disk, so the deleted page kept showing up as synced —
and because two paths can share a Canvas id, the dead entry could win the
reverse lookup and hide the file that was still there.
"""

import json
import os

import pytest

from handlers.content_utils import load_sync_map, save_sync_map
from handlers.drift_detector import (
    SNAPSHOT_DIR,
    forget_synced_dir,
    forget_synced_file,
)
from sync_to_canvas import _delete_items


# --- Canvas stubs -----------------------------------------------------------

class FakeModuleItem:
    def __init__(self):
        self.deleted = False

    def delete(self):
        self.deleted = True


class FakeModule:
    def __init__(self):
        self.deleted = False
        self.item = FakeModuleItem()

    def get_module_item(self, item_id):
        return self.item

    def delete(self):
        self.deleted = True


class FakeCourse:
    def __init__(self):
        self.module = FakeModule()

    def get_module(self, module_id):
        return self.module


# --- fixtures ---------------------------------------------------------------

def _snapshot(root, rel):
    d = os.path.join(root, SNAPSHOT_DIR)
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, rel.replace('/', '__') + '.html')
    with open(p, 'w', encoding='utf-8') as f:
        f.write('<p>old</p>')
    return p


@pytest.fixture
def course_dir(tmp_path):
    """A content root with one module, two tracked pages, and their snapshots."""
    mod = tmp_path / "06_Genomgangar"
    mod.mkdir()
    for name in ("02_Click_alongs.qmd", "03_Files.qmd"):
        (mod / name).write_text("---\ntitle: T\n---\nBody\n", encoding="utf-8")

    save_sync_map(str(tmp_path), {
        "06_Genomgangar/02_Click_alongs.qmd": {"id": 192422, "mtime": 1.0},
        "06_Genomgangar/03_Files.qmd": {"id": 192422, "mtime": 2.0},
    })
    _snapshot(str(tmp_path), "06_Genomgangar/02_Click_alongs.qmd")
    _snapshot(str(tmp_path), "06_Genomgangar/03_Files.qmd")
    return tmp_path


# --- forget_synced_file / forget_synced_dir ---------------------------------

class TestForgetSyncedFile:

    def test_drops_map_entry_and_snapshot(self, course_dir):
        rel = "06_Genomgangar/03_Files.qmd"
        assert forget_synced_file(str(course_dir), rel) is True

        assert rel not in load_sync_map(str(course_dir))
        snap = course_dir / SNAPSHOT_DIR / "06_Genomgangar__03_Files.qmd.html"
        assert not snap.exists()

    def test_leaves_the_sibling_alone(self, course_dir):
        forget_synced_file(str(course_dir), "06_Genomgangar/03_Files.qmd")

        sync_map = load_sync_map(str(course_dir))
        assert "06_Genomgangar/02_Click_alongs.qmd" in sync_map
        assert (course_dir / SNAPSHOT_DIR / "06_Genomgangar__02_Click_alongs.qmd.html").exists()

    def test_untracked_path_is_a_no_op(self, course_dir):
        assert forget_synced_file(str(course_dir), "06_Genomgangar/99_Nope.qmd") is False
        assert len(load_sync_map(str(course_dir))) == 2

    def test_accepts_windows_separators(self, course_dir):
        windows_rel = "06_Genomgangar" + chr(92) + "03_Files.qmd"
        assert forget_synced_file(str(course_dir), windows_rel) is True
        assert "06_Genomgangar/03_Files.qmd" not in load_sync_map(str(course_dir))

    def test_does_not_create_a_snapshot_dir(self, tmp_path):
        save_sync_map(str(tmp_path), {"a/b.qmd": {"id": 1}})
        forget_synced_file(str(tmp_path), "a/b.qmd")
        assert not (tmp_path / SNAPSHOT_DIR).exists()


class TestForgetSyncedDir:

    def test_drops_every_entry_under_the_directory(self, course_dir):
        assert forget_synced_dir(str(course_dir), "06_Genomgangar") == 2
        assert load_sync_map(str(course_dir)) == {}
        assert list((course_dir / SNAPSHOT_DIR).glob("*.html")) == []

    def test_does_not_match_a_prefix_of_another_directory(self, tmp_path):
        save_sync_map(str(tmp_path), {
            "06_Genom/01_A.qmd": {"id": 1},
            "06_Genomgangar/01_B.qmd": {"id": 2},
        })
        assert forget_synced_dir(str(tmp_path), "06_Genom") == 1
        assert "06_Genomgangar/01_B.qmd" in load_sync_map(str(tmp_path))


# --- _delete_items ----------------------------------------------------------

class TestDeleteItemsPrunesMap:

    def test_item_delete_forgets_the_file(self, course_dir):
        course = FakeCourse()
        payload = json.dumps({"items": [{
            "target": "item", "module_id": 1, "item_id": 2,
            "local_path": "06_Genomgangar/03_Files.qmd",
        }]})

        res = _delete_items(course, str(course_dir), payload)

        assert res["success"] and res["deleted"] == 1
        assert course.module.item.deleted
        assert not (course_dir / "06_Genomgangar" / "03_Files.qmd").exists()
        assert "06_Genomgangar/03_Files.qmd" not in load_sync_map(str(course_dir))

    def test_forgets_an_entry_whose_file_is_already_gone(self, course_dir):
        """The ghost row: file deleted by hand, map entry left behind."""
        (course_dir / "06_Genomgangar" / "03_Files.qmd").unlink()
        course = FakeCourse()
        payload = json.dumps({"items": [{
            "target": "item", "module_id": 1, "item_id": 2,
            "local_path": "06_Genomgangar/03_Files.qmd",
        }]})

        res = _delete_items(course, str(course_dir), payload)

        assert res["success"]
        assert "06_Genomgangar/03_Files.qmd" not in load_sync_map(str(course_dir))

    def test_local_file_delete_forgets_the_file(self, course_dir):
        payload = json.dumps({"items": [{
            "target": "local_file", "local_path": "06_Genomgangar/03_Files.qmd",
        }]})

        res = _delete_items(FakeCourse(), str(course_dir), payload)

        assert res["success"] and res["deleted"] == 1
        assert "06_Genomgangar/03_Files.qmd" not in load_sync_map(str(course_dir))

    def test_module_delete_forgets_every_file_in_it(self, course_dir):
        course = FakeCourse()
        payload = json.dumps({"items": [{
            "target": "module", "module_id": 1, "local_dir": "06_Genomgangar",
        }]})

        res = _delete_items(course, str(course_dir), payload)

        assert res["success"]
        assert course.module.deleted
        assert load_sync_map(str(course_dir)) == {}

    def test_canvas_only_item_leaves_the_map_alone(self, course_dir):
        """No local_path means nothing local to forget."""
        payload = json.dumps({"items": [{
            "target": "item", "module_id": 1, "item_id": 2, "local_path": "",
        }]})

        _delete_items(FakeCourse(), str(course_dir), payload)

        assert len(load_sync_map(str(course_dir))) == 2
