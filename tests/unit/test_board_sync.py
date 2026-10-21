"""Sync-side behaviour the Course Board relies on, against fake Canvas objects.

- modules end up in local order without disturbing Canvas-only ones
- an item moved to another module folder leaves its old module
- single-file sync follows the order files and explains a skipped file
- structure mode reports what is wrong instead of failing
- the validator and cqs know about order files
"""

import json
import os
from types import SimpleNamespace
from unittest.mock import MagicMock

import cqs
import sync_to_canvas
import validate_content
from handlers import course_layout as cl
from handlers.single_sync import arrange_modules, remove_stale_placements, sync_single_file


class FakeModule:
    def __init__(self, name, id, position, items=()):
        self.name, self.id, self.position = name, id, position
        self._items = list(items)
        self.edits = []

    def edit(self, module):
        self.edits.append(module)
        self.position = module['position']

    def get_module_items(self):
        return self._items


class FakeItem:
    def __init__(self, type, title, content_id=None, page_url=None):
        self.type, self.title, self.content_id, self.page_url = type, title, content_id, page_url
        self.deleted = False

    def delete(self):
        self.deleted = True


def _course(modules):
    return SimpleNamespace(get_modules=lambda **kw: list(modules))


class TestArrangeModules:

    def test_local_modules_take_their_slots_in_local_order(self):
        info = FakeModule("Info", 1, 1)
        b = FakeModule("B", 2, 2)
        a = FakeModule("A", 3, 3)

        moved = arrange_modules(_course([info, b, a]), ["A", "B"])

        assert (info.position, a.position, b.position) == (1, 2, 3)
        assert info.edits == [] and moved == 2

    def test_nothing_moves_when_already_in_order(self):
        a, b = FakeModule("A", 1, 1), FakeModule("B", 2, 2)
        assert arrange_modules(_course([a, b]), ["A", "B"]) == 0


class TestRemoveStalePlacements:

    def test_moved_item_leaves_its_old_module(self):
        old_item = FakeItem("Assignment", "Lab", content_id=7)
        new_item = FakeItem("Assignment", "Lab", content_id=7)
        old = FakeModule("Old", 1, 1, [old_item])
        new = FakeModule("New", 2, 2, [new_item])

        removed = remove_stale_placements(_course([old, new]), [(2, new_item)], {"Old", "New"})

        assert removed == 1 and old_item.deleted and not new_item.deleted

    def test_canvas_only_modules_are_left_alone(self):
        theirs = FakeItem("Page", "Guide", page_url="guide")
        mine = FakeItem("Page", "Guide", page_url="guide")
        hand = FakeModule("Picked by hand", 1, 1, [theirs])
        own = FakeModule("Own", 2, 2, [mine])

        assert remove_stale_placements(_course([hand, own]), [(2, mine)], {"Own"}) == 0
        assert not theirs.deleted

    def test_subheaders_are_never_treated_as_moved(self):
        a = FakeItem("SubHeader", "Week 1")
        b = FakeItem("SubHeader", "Week 1")
        m1, m2 = FakeModule("A", 1, 1, [a]), FakeModule("B", 2, 2, [b])
        assert remove_stale_placements(_course([m1, m2]), [(2, b)], {"A", "B"}) == 0


def _page(path, title="T"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'---\ntitle: "{title}"\ncanvas:\n  type: page\n---\n\nBody.\n', encoding="utf-8")
    return path


class TestSingleSyncWithOrderFiles:

    def test_skipped_file_says_why(self, tmp_path):
        _page(tmp_path / "Labs" / "A.qmd")
        _page(tmp_path / "Extra" / "B.qmd")
        cl.write_modules(str(tmp_path), [("Labs", None)])

        result = sync_single_file(MagicMock(), str(tmp_path), str(tmp_path / "Extra" / "B.qmd"))

        assert not result.success
        assert "not listed in _modules.yml" in result.message

    def test_position_follows_the_order_file(self, tmp_path):
        for n in ("A.qmd", "B.qmd", "C.qmd"):
            _page(tmp_path / "Labs" / n, n[0])
        cl.write_modules(str(tmp_path), [("Labs", "Laborationer")])
        cl.write_order(str(tmp_path), "Labs", ["C.qmd", "A.qmd", "B.qmd"])

        module = MagicMock()
        module.name = "Laborationer"
        module.id = 1
        module.get_module_items.return_value = [SimpleNamespace(title="C")]
        course = MagicMock()
        course.get_modules.return_value = [module]
        mod_item = MagicMock(title="A", position=2, type="Page", page_url="a")

        class H:
            def can_handle(self, p):
                return p.endswith("A.qmd")

            def sync(self, *a, **k):
                return mod_item

        result = sync_single_file(course, str(tmp_path), str(tmp_path / "Labs" / "A.qmd"), handlers=[H()])

        assert result.success
        # C is before A in the order file and already in Canvas.
        assert result.position == 2


class TestStructureProblems:

    def _run(self, tmp_path, capsys, **kw):
        args = dict(api_url=None, api_token=None, course_id=None, with_drift=False)
        args.update(kw)
        assert sync_to_canvas._print_module_structure(str(tmp_path), **args) == 0
        return json.loads(capsys.readouterr().out)

    def test_no_config_still_shows_the_local_course(self, tmp_path, capsys):
        _page(tmp_path / "01_Intro" / "01_Welcome.qmd", "Welcome")

        out = self._run(tmp_path, capsys)

        codes = [p["code"] for p in out["problems"]]
        assert codes == ["no_config", "nn_prefix"]
        assert out["connected"] is False
        assert out["modules"][0]["items"][0]["local_path"] == "01_Intro/01_Welcome.qmd"

    def test_missing_course_id_names_config_toml(self, tmp_path, capsys):
        (tmp_path / "config.toml").write_text('canvas_api_url = "https://x"\n', encoding="utf-8")
        out = self._run(tmp_path, capsys, api_url="https://x", api_token="t")
        assert out["problems"][0]["code"] == "no_course_id"
        assert "config.toml" in out["problems"][0]["action"]

    def test_missing_token(self, tmp_path, capsys):
        (tmp_path / "config.toml").write_text("course_id = 5\n", encoding="utf-8")
        out = self._run(tmp_path, capsys, api_url="https://x", course_id=5)
        assert [p["code"] for p in out["problems"]] == ["no_credentials"]


class TestValidatorWithOrderFiles:

    def test_content_in_an_unlisted_folder_is_an_error(self, tmp_path):
        _page(tmp_path / "Labs" / "A.qmd")
        _page(tmp_path / "Extra" / "B.qmd")
        cl.write_modules(str(tmp_path), [("Labs", None)])
        validate_content._LAYOUTS.clear()

        reports = validate_content.validate_path(str(tmp_path), str(tmp_path))

        by_name = {os.path.basename(r.path): r for r in reports}
        assert by_name["A.qmd"].errors == []
        assert "not listed in _modules.yml" in by_name["B.qmd"].issues[0].message

    def test_order_file_problems_are_reported(self, tmp_path):
        _page(tmp_path / "Labs" / "A.qmd")
        cl.write_modules(str(tmp_path), [("Labs", None), ("Gone", None)])
        validate_content._LAYOUTS.clear()

        reports = validate_content.validate_path(str(tmp_path), str(tmp_path))

        layout_reports = [r for r in reports if r.kind == "course layout"]
        assert layout_reports and "Gone" in layout_reports[0].issues[0].message

    def test_nn_course_gets_a_note_not_a_warning(self, tmp_path):
        _page(tmp_path / "01_Labs" / "01_A.qmd")
        validate_content._LAYOUTS.clear()

        reports = validate_content.validate_path(str(tmp_path), str(tmp_path))

        assert all(not r.issues for r in reports)
        assert "cqs migrate" in validate_content.layout_note(str(tmp_path))


def test_cqs_migrate_runs_course_board(tmp_path):
    (tmp_path / "config.toml").write_text("course_id = 1\n", encoding="utf-8")
    module, argv = cqs.VERBS['migrate'](['--dry-run'], str(tmp_path))
    assert module == 'course_board'
    assert argv == [str(tmp_path), 'migrate', '--dry-run']
