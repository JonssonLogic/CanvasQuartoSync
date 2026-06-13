"""Integration tests for sync_single_file() with mocked Canvas + handlers.

These avoid Quarto/Canvas by injecting fake handlers, so they exercise the
orchestration: module resolution, handler dispatch, positioning, and the
solo-asset fallback.
"""

import os
from types import SimpleNamespace
from unittest.mock import MagicMock

import handlers.single_sync as single_sync
from handlers.single_sync import sync_single_file


def _make_course(module):
    """Course whose get_modules() finds nothing, so find_or_create_module creates one."""
    course = MagicMock()
    course.get_modules.return_value = []
    course.create_module.return_value = module
    return course


def _make_module(present_titles):
    module = MagicMock()
    module.name = "Mod"
    module.get_module_items.return_value = [SimpleNamespace(title=t) for t in present_titles]
    return module


def _make_module_dir(tmp_path, names):
    mod_dir = tmp_path / "01_Mod"
    mod_dir.mkdir()
    for name in names:
        (mod_dir / name).write_text("---\ncanvas:\n  type: page\n---\n", encoding="utf-8")
    return mod_dir


class _FakeHandler:
    """Handler that claims the target file and returns a given module item."""
    def __init__(self, target_name, mod_item):
        self.target_name = target_name
        self.mod_item = mod_item
        self.synced_with = None

    def can_handle(self, file_path):
        return os.path.basename(file_path) == self.target_name

    def sync(self, file_path, course, module=None, canvas_obj=None, content_root=None):
        self.synced_with = SimpleNamespace(file_path=file_path, module=module)
        return self.mod_item


class TestSyncSingleFilePositioning:

    def test_positions_item_in_correct_slot(self, tmp_path):
        mod_dir = _make_module_dir(tmp_path, ["01_A.qmd", "02_B.qmd", "03_C.qmd"])
        module = _make_module(present_titles=["A"])  # A already in module
        course = _make_course(module)

        # The synced item starts appended at the end (position 3).
        mod_item = MagicMock()
        mod_item.title = "B"
        mod_item.position = 3
        handler = _FakeHandler("02_B.qmd", mod_item)

        result = sync_single_file(
            course, str(tmp_path), str(mod_dir / "02_B.qmd"),
            canvas=MagicMock(), handlers=[handler],
        )

        assert result.success
        assert result.position == 2
        mod_item.edit.assert_called_once_with(module_item={"position": 2})
        # Handler received the resolved module object.
        assert handler.synced_with.module is module

    def test_no_reposition_when_already_correct(self, tmp_path):
        mod_dir = _make_module_dir(tmp_path, ["01_A.qmd", "02_B.qmd"])
        module = _make_module(present_titles=["A"])
        course = _make_course(module)

        mod_item = MagicMock()
        mod_item.title = "B"
        mod_item.position = 2  # already where it belongs
        handler = _FakeHandler("02_B.qmd", mod_item)

        result = sync_single_file(
            course, str(tmp_path), str(mod_dir / "02_B.qmd"),
            handlers=[handler],
        )
        assert result.success
        mod_item.edit.assert_not_called()


class TestSyncSingleFileSoloAsset:

    def test_uploads_and_adds_solo_asset(self, tmp_path, monkeypatch):
        mod_dir = _make_module_dir(tmp_path, ["01_A.qmd"])
        pdf = mod_dir / "02_Syllabus.pdf"
        pdf.write_bytes(b"%PDF-1.4")

        module = _make_module(present_titles=["A"])
        course = _make_course(module)

        # No handler claims the PDF.
        no_match = _FakeHandler("__none__", MagicMock())

        # add_to_module lives on the (first) handler; stub it on our fake.
        file_item = MagicMock()
        file_item.title = "Syllabus.pdf"
        file_item.position = 2
        no_match.add_to_module = MagicMock(return_value=file_item)

        monkeypatch.setattr(single_sync, "upload_file", lambda *a, **k: ("http://x/file", 999))

        result = sync_single_file(
            course, str(tmp_path), str(pdf), handlers=[no_match],
        )

        assert result.success
        no_match.add_to_module.assert_called_once()
        payload = no_match.add_to_module.call_args[0][1]
        assert payload["type"] == "File"
        assert payload["content_id"] == 999
        assert payload["title"] == "Syllabus.pdf"


class TestSyncSingleFileValidation:

    def test_rejects_missing_file(self, tmp_path):
        course = MagicMock()
        result = sync_single_file(course, str(tmp_path), str(tmp_path / "nope.qmd"))
        assert not result.success
        assert "not found" in result.message.lower()

    def test_rejects_file_outside_content_root(self, tmp_path):
        outside = tmp_path / "outside"
        outside.mkdir()
        f = outside / "01_X.qmd"
        f.write_text("---\ncanvas:\n  type: page\n---\n", encoding="utf-8")
        root = tmp_path / "root"
        root.mkdir()
        course = MagicMock()
        result = sync_single_file(course, str(root), str(f))
        assert not result.success
        assert "inside" in result.message.lower()

    def test_rejects_unprefixed_file(self, tmp_path):
        f = tmp_path / "welcome.qmd"  # no NN_ prefix
        f.write_text("---\ncanvas:\n  type: page\n---\n", encoding="utf-8")
        course = MagicMock()
        result = sync_single_file(course, str(tmp_path), str(f))
        assert not result.success
        assert "prefix" in result.message.lower()

    def test_root_file_syncs_without_module(self, tmp_path):
        f = tmp_path / "01_Home.qmd"
        f.write_text("---\ncanvas:\n  type: page\n---\n", encoding="utf-8")
        course = MagicMock()
        # Root page handler returns None (no module) but still succeeds.
        handler = _FakeHandler("01_Home.qmd", None)
        result = sync_single_file(course, str(tmp_path), str(f), handlers=[handler])
        assert result.success
        assert result.position is None
        course.create_module.assert_not_called()
