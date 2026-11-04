"""A --only sync brings the order files in step too.

The Course Board syncs one file at a time (--only). Reconciling only in a full
sync left a renamed file's old name in _order.yml.
"""

import json
from types import SimpleNamespace

import sync_to_canvas
from handlers import course_layout as cl


def _course(tmp_path):
    labs = tmp_path / "Labs"
    labs.mkdir()
    (tmp_path / "_modules.yml").write_text("- Labs\n", encoding="utf-8")
    for n in ("test.qmd", "testarNamnByte.qmd"):
        (labs / n).write_text('---\ntitle: "T"\ncanvas:\n  type: page\n---\n\nBody.\n',
                              encoding="utf-8")
    (labs / "_order.yml").write_text("- test.qmd\n- test2.qmd\n", encoding="utf-8")
    (tmp_path / ".canvas_sync_map.json").write_text(
        json.dumps({"Labs/test2.qmd": {"id": 42}}), encoding="utf-8")
    return tmp_path


def test_only_sync_renames_in_order_file_and_map(tmp_path, monkeypatch):
    root = _course(tmp_path)
    seen = {}

    def fake_single(course, content_root, path, canvas=None, handlers=None):
        # The rename must be done before the file is synced, so it finds its id.
        seen["map"] = json.loads((root / ".canvas_sync_map.json").read_text(encoding="utf-8"))
        return SimpleNamespace(success=True, message="ok")

    monkeypatch.setattr(sync_to_canvas, "sync_single_file", fake_single)
    monkeypatch.setattr(sync_to_canvas, "build_handlers", lambda: [])
    args = SimpleNamespace(only="Labs/testarNamnByte.qmd", sync_calendar=False)

    assert sync_to_canvas._sync_content(args, None, None, str(root)) == 0

    order = [n for n, _ in cl.read_list(str(root / "Labs" / "_order.yml"))]
    assert order == ["test.qmd", "testarNamnByte.qmd"]
    assert seen["map"] == {"Labs/testarNamnByte.qmd": {"id": 42}}
