"""A file imported from Canvas is recorded in the sync map.

Without the entry, the next sync took the imported PDF for a new file,
uploaded a second copy and added it to the module next to the original.
"""

import json
from types import SimpleNamespace

from handlers import content_utils
from handlers.content_utils import record_file


def test_record_file_lets_upload_skip_the_imported_file(tmp_path, monkeypatch):
    pdf = tmp_path / "Mod" / "L1.pdf"
    pdf.parent.mkdir()
    pdf.write_bytes(b"%PDF-1.4 imported")

    record_file(str(tmp_path), str(pdf), 2741843, "https://canvas/files/2741843/download")

    saved = json.loads((tmp_path / ".canvas_sync_map.json").read_text(encoding="utf-8"))
    assert saved["Mod/L1.pdf"]["id"] == 2741843

    monkeypatch.setattr(content_utils, "get_or_create_folder",
                        lambda *a: (_ for _ in ()).throw(AssertionError("uploaded again")))
    url, file_id = content_utils.upload_file(SimpleNamespace(), str(pdf), "synced-files",
                                             content_root=str(tmp_path))
    assert file_id == 2741843
