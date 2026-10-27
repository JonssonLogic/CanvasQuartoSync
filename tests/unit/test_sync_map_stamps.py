"""last_synced_at is written by the sync, never by a board refresh.

Team mode commits the sync map at the end of a sync. The Course Board then
refreshes with --module-structure, and its backfill used to stamp entries that
had no last_synced_at and save the map, leaving it changed after every sync
that uploaded a file. Files now get their stamp when they are uploaded, and
the backfill only fills in what the board reads.
"""

from unittest.mock import patch

from handlers import content_utils
from handlers.content_utils import load_sync_map, save_sync_map, upload_file
from handlers.module_structure import fetch_module_structure

from tests.unit.test_module_structure import FakeCourse, FakeItem, FakeModule, _qmd


class FakeFolder:
    def __init__(self, file_id=2725752):
        self.file_id = file_id
        self.uploads = 0

    def upload(self, path, on_duplicate=None):
        self.uploads += 1
        return True, {"url": f"https://canvas.test/files/{self.file_id}", "id": self.file_id}


def _upload(tmp_path, folder):
    pdf = tmp_path / "Docs" / "KursPM.pdf"
    if not pdf.exists():
        pdf.parent.mkdir(parents=True, exist_ok=True)
        pdf.write_bytes(b"%PDF-1.7 test")
    with patch("handlers.content_utils.get_or_create_folder", return_value=folder):
        return upload_file(None, str(pdf), "course_files", content_root=str(tmp_path))


class TestUploadStamps:
    def test_uploaded_file_gets_last_synced_at_and_by(self, tmp_path):
        content_utils.set_sync_identity("Mirza Cenanovic")
        try:
            _upload(tmp_path, FakeFolder())
        finally:
            content_utils.set_sync_identity("")
        entry = load_sync_map(str(tmp_path))["Docs/KursPM.pdf"]
        assert entry["id"] == 2725752
        assert entry["last_synced_at"]
        assert entry["last_synced_by"] == "Mirza Cenanovic"

    def test_cached_file_without_a_stamp_gets_one(self, tmp_path):
        folder = FakeFolder()
        _upload(tmp_path, folder)
        sync_map = load_sync_map(str(tmp_path))
        del sync_map["Docs/KursPM.pdf"]["last_synced_at"]
        save_sync_map(str(tmp_path), sync_map)

        _upload(tmp_path, folder)

        assert folder.uploads == 1  # unchanged, so not uploaded again
        assert load_sync_map(str(tmp_path))["Docs/KursPM.pdf"]["last_synced_at"]


class TestBoardDoesNotWrite:
    def test_backfill_leaves_the_map_file_alone(self, tmp_path):
        _qmd(tmp_path, "01_Intro/01_Welcome.qmd", "Welcome")
        save_sync_map(str(tmp_path), {"01_Intro/01_Welcome.qmd": {"id": 4242}})
        map_file = tmp_path / ".canvas_sync_map.json"
        before = map_file.read_bytes()

        course = FakeCourse(modules=[FakeModule(
            "Intro", items=[FakeItem("Welcome", content_id=4242)])])
        out = fetch_module_structure(course, str(tmp_path))

        assert map_file.read_bytes() == before
        assert out["modules"][0]["items"][0]["last_synced_at"]
