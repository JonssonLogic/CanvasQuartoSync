"""Tests for handlers/module_structure.py.

Everything here is offline. The Canvas side is a stub, the local side is a
real temp directory, and what is under test is the reconciliation between
them: which Canvas item corresponds to which file, and what exists on only
one side.
"""

import json

from handlers.content_utils import save_sync_map, MAP_COURSE_KEY
from handlers.module_structure import fetch_module_structure


# --- Canvas stubs -----------------------------------------------------------

class FakeItem:
    def __init__(self, title, type="Page", id=1, published=True, indent=0,
                 content_id=None, page_url=None, external_url=None):
        self.title = title
        self.type = type
        self.id = id
        self.published = published
        self.indent = indent
        self.content_id = content_id
        self.page_url = page_url
        self.html_url = f"https://canvas.test/items/{id}"
        if external_url is not None:
            self.external_url = external_url


class FakeModule:
    def __init__(self, name, id=1, published=False, items=()):
        self.name = name
        self.id = id
        self.published = published
        self._items = list(items)

    def get_module_items(self, **kwargs):
        return self._items


class FakeCourse:
    def __init__(self, modules=(), pages=()):
        self.name = "Training_cenmir"
        self.id = 74
        self.course_code = "TEST101"
        self.workflow_state = "unpublished"
        self.time_zone = "Europe/Stockholm"
        self._modules = list(modules)
        self._pages = list(pages)

    def get_modules(self, **kwargs):
        return self._modules

    def get_pages(self, **kwargs):
        return self._pages


def _qmd(tmp_path, rel, title):
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        f'---\ntitle: "{title}"\ncanvas:\n  type: page\n---\n\nBody.\n',
        encoding="utf-8",
    )
    return p


# --- Course-level fields ----------------------------------------------------

def test_reports_the_course_it_read(tmp_path):
    out = fetch_module_structure(FakeCourse(), str(tmp_path))
    assert out["course_id"] == 74
    assert out["course_name"] == "Training_cenmir"
    assert out["time_zone"] == "Europe/Stockholm"


def test_output_is_json_serialisable(tmp_path):
    """The whole point of the flag is that a caller can parse stdout."""
    _qmd(tmp_path, "01_Intro/01_Welcome.qmd", "Welcome")
    course = FakeCourse(modules=[FakeModule("Intro", items=[FakeItem("Welcome")])])
    json.dumps(fetch_module_structure(course, str(tmp_path)))


# --- Matching Canvas items to local files -----------------------------------

def test_matches_by_sync_map_id(tmp_path):
    """The exact signal, when the map has an entry."""
    _qmd(tmp_path, "01_Intro/01_Welcome.qmd", "Something Else Entirely")
    save_sync_map(str(tmp_path), {"01_Intro/01_Welcome.qmd": {"id": 4242}})

    course = FakeCourse(modules=[FakeModule(
        "Intro", items=[FakeItem("Renamed In Canvas", content_id=4242)])])
    out = fetch_module_structure(course, str(tmp_path))

    assert out["modules"][0]["items"][0]["local_path"] == "01_Intro/01_Welcome.qmd"


def test_matches_by_filename_without_a_sync_map(tmp_path):
    """A fresh clone has no map, so the filename has to carry it."""
    _qmd(tmp_path, "01_Intro/01_Welcome.qmd", "Welcome")
    course = FakeCourse(modules=[FakeModule("Intro", items=[FakeItem("Welcome")])])

    out = fetch_module_structure(course, str(tmp_path))
    assert out["modules"][0]["items"][0]["local_path"] == "01_Intro/01_Welcome.qmd"


def test_matches_by_frontmatter_title(tmp_path):
    """Canvas shows the title, which need not resemble the filename."""
    _qmd(tmp_path, "01_Intro/01_Kickoff.qmd", "Welcome to the course")
    course = FakeCourse(modules=[
        FakeModule("Intro", items=[FakeItem("Welcome to the course")])])

    out = fetch_module_structure(course, str(tmp_path))
    assert out["modules"][0]["items"][0]["local_path"] == "01_Intro/01_Kickoff.qmd"


def test_canvas_item_with_no_local_file_is_reported_unmatched(tmp_path):
    (tmp_path / "01_Intro").mkdir()
    course = FakeCourse(modules=[
        FakeModule("Intro", items=[FakeItem("Added In The Browser")])])

    out = fetch_module_structure(course, str(tmp_path))
    assert out["modules"][0]["items"][0]["local_path"] is None


def test_local_module_absent_from_canvas_is_listed_separately(tmp_path):
    _qmd(tmp_path, "02_NotYetSynced/01_Draft.qmd", "Draft")
    out = fetch_module_structure(FakeCourse(), str(tmp_path))

    assert [m["dir_name"] for m in out["local_only_modules"]] == ["02_NotYetSynced"]
    assert out["local_only_modules"][0]["files"] == ["02_NotYetSynced/01_Draft.qmd"]


def test_folders_without_an_NN_prefix_are_ignored(tmp_path):
    """graphics/ and assets/ are not modules."""
    _qmd(tmp_path, "graphics/notes.qmd", "Notes")
    out = fetch_module_structure(FakeCourse(), str(tmp_path))
    assert out["local_only_modules"] == []


# --- Coexistence with the rest of the sync map ------------------------------

def test_reserved_course_key_is_not_mistaken_for_a_file(tmp_path):
    """The map carries _course_id since the wrong-course guard. It is a scalar,
    not an entry, and walking the map must skip it."""
    _qmd(tmp_path, "01_Intro/01_Welcome.qmd", "Welcome")
    save_sync_map(str(tmp_path), {
        MAP_COURSE_KEY: 74,
        "01_Intro/01_Welcome.qmd": {"id": 4242},
    })

    course = FakeCourse(modules=[FakeModule(
        "Intro", items=[FakeItem("Welcome", content_id=4242)])])
    out = fetch_module_structure(course, str(tmp_path))

    assert out["modules"][0]["items"][0]["local_path"] == "01_Intro/01_Welcome.qmd"
    assert out["local_only_modules"] == []


def test_nothing_is_written_to_canvas(tmp_path):
    """Read-only. Any method that would mutate the course fails the test.

    Checked by name rather than with a blanket __getattr__ guard, because
    optional reads such as total_students legitimately go through getattr and
    a blanket guard would flag those too.
    """
    _qmd(tmp_path, "01_Intro/01_Welcome.qmd", "Welcome")

    WRITES = ("create_page", "create_module", "create_assignment", "create_quiz",
              "edit", "update", "delete", "upload")

    class NoWriteCourse(FakeCourse):
        pass

    def _forbid(name):
        def boom(*a, **kw):
            raise AssertionError(f"module structure must not call course.{name}")
        return boom

    course = NoWriteCourse(modules=[FakeModule("Intro", items=[FakeItem("Welcome")])])
    for name in WRITES:
        setattr(course, name, _forbid(name))

    out = fetch_module_structure(course, str(tmp_path))
    assert out["modules"][0]["items"][0]["local_path"] == "01_Intro/01_Welcome.qmd"


# --- Render artefacts -------------------------------------------------------

def test_render_artefacts_are_not_treated_as_content(tmp_path):
    """A sync in flight leaves tmp-pdf-*.qmd next to the file being rendered.

    Refreshing the panel at that moment matched the Canvas item to the
    artefact and reported the real file as local-only: a phantom "not synced"
    row and a local_path pointing at a file that was about to be deleted.
    Every handler and the validator already skip these prefixes.
    """
    _qmd(tmp_path, "01_Intro/01_Welcome.qmd", "Welcome")
    _qmd(tmp_path, "01_Intro/tmp-pdf-01_Welcome.qmd", "Welcome")
    _qmd(tmp_path, "01_Intro/_temp_quiz_render.qmd", "Welcome")

    course = FakeCourse(modules=[FakeModule("Intro", items=[FakeItem("Welcome")])])
    out = fetch_module_structure(course, str(tmp_path))

    item = out["modules"][0]["items"][0]
    assert item["local_path"] == "01_Intro/01_Welcome.qmd"
    # And no artefact is left over as a file with no Canvas counterpart.
    assert out["local_only_modules"] == []
    assert not [i for i in out["modules"][0]["items"] if i.get("local_only")]


# --- Drift status -----------------------------------------------------------

def test_drift_is_not_checked_unless_asked(tmp_path, monkeypatch):
    """Opening the panel must not pay for a Canvas request per item."""
    calls = []

    def fail(*a, **k):
        calls.append(a)
        return []

    monkeypatch.setattr("handlers.module_structure.check_all_drift", fail)
    _qmd(tmp_path, "01_Intro/01_Welcome.qmd", "Welcome")
    course = FakeCourse(modules=[FakeModule("Intro", items=[FakeItem("Welcome")])])

    out = fetch_module_structure(course, str(tmp_path))

    assert calls == []
    # None means "not checked", which is not the same as "no drift".
    assert out["modules"][0]["items"][0]["canvas_drift"] is None


def test_drift_is_reported_per_item_when_asked(tmp_path, monkeypatch):
    """The only reliable Canvas-side signal for an assignment.

    updated_at is fetched for pages alone, because Canvas bumps an
    assignment's timestamp for submissions and grading. A content hash is
    what makes the Canvas-newer dot mean anything on an assignment.
    """
    _qmd(tmp_path, "01_Labs/01_Lab.qmd", "Lab")
    _qmd(tmp_path, "01_Labs/02_Other.qmd", "Other")
    save_sync_map(str(tmp_path), {
        MAP_COURSE_KEY: 74,
        "01_Labs/01_Lab.qmd": {"id": 11},
        "01_Labs/02_Other.qmd": {"id": 12},
    })
    monkeypatch.setattr(
        "handlers.module_structure.check_all_drift",
        lambda course, root, include_diff=True: [{"file": "01_Labs/01_Lab.qmd"}])

    course = FakeCourse(modules=[FakeModule("Labs", items=[
        FakeItem("Lab", type="Assignment", content_id=11),
        FakeItem("Other", type="Assignment", id=2, content_id=12),
    ])])
    out = fetch_module_structure(course, str(tmp_path), with_drift=True)

    by_title = {i["title"]: i for i in out["modules"][0]["items"]}
    assert by_title["Lab"]["canvas_drift"] is True
    assert by_title["Other"]["canvas_drift"] is False


def test_drift_status_writes_no_diff_files(tmp_path, monkeypatch):
    """A status light should not litter .canvas_diff_temp on every refresh."""
    seen = {}

    def spy(course, root, include_diff=True):
        seen["include_diff"] = include_diff
        return []

    monkeypatch.setattr("handlers.module_structure.check_all_drift", spy)
    _qmd(tmp_path, "01_Intro/01_Welcome.qmd", "Welcome")
    course = FakeCourse(modules=[FakeModule("Intro", items=[FakeItem("Welcome")])])

    fetch_module_structure(course, str(tmp_path), with_drift=True)

    assert seen["include_diff"] is False


# --- Pages, and modules renamed in Canvas -----------------------------------

class FakePage:
    """A Canvas page as get_pages() returns it: numeric id plus a slug."""

    def __init__(self, page_id, url, updated_at="2026-08-30T10:00:00Z"):
        self.page_id = page_id
        self.url = url
        self.updated_at = updated_at


def test_page_matches_through_its_slug(tmp_path):
    """A Page module item has no content_id, only page_url.

    The sync map records the numeric page_id, so matching on the item's own
    fields can never succeed. get_pages() already gives the bridge between the
    two, and without it a page falls back to matching on name inside its
    module: fine until somebody renames the module in Canvas, at which point
    the page reads as Canvas-only and its file as an orphan.
    """
    _qmd(tmp_path, "01_Intro/01_Guide.qmd", "Totally Different Title")
    save_sync_map(str(tmp_path), {
        MAP_COURSE_KEY: 74,
        "01_Intro/01_Guide.qmd": {"id": 191818},
    })
    course = FakeCourse(
        modules=[FakeModule("Intro", items=[
            FakeItem("Install the thing", type="Page", content_id=None,
                     page_url="install-the-thing"),
        ])],
        pages=[FakePage(191818, "install-the-thing")],
    )

    out = fetch_module_structure(course, str(tmp_path))

    assert out["modules"][0]["items"][0]["local_path"] == "01_Intro/01_Guide.qmd"


def test_module_renamed_in_canvas_still_finds_its_directory(tmp_path):
    """Renaming a module in Canvas must not orphan the whole directory.

    The directory is identified by name, which is what the sync itself uses,
    but the items already know where they live. Whichever directory holds the
    files they map to is this module's directory, whatever Canvas calls it.
    """
    _qmd(tmp_path, "04_Labs/01_Tensile.qmd", "Lab 1")
    _qmd(tmp_path, "04_Labs/02_Torsion.qmd", "Lab 2")
    save_sync_map(str(tmp_path), {
        MAP_COURSE_KEY: 74,
        "04_Labs/01_Tensile.qmd": {"id": 11},
        "04_Labs/02_Torsion.qmd": {"id": 12},
    })
    # Canvas says "Laboratories"; the directory is still 04_Labs.
    course = FakeCourse(modules=[FakeModule("Laboratories", items=[
        FakeItem("Lab 1", type="Assignment", content_id=11),
        FakeItem("Lab 2", type="Assignment", id=2, content_id=12),
    ])])

    out = fetch_module_structure(course, str(tmp_path))

    assert out["modules"][0]["local_dir"] == "04_Labs"
    assert out["local_only_modules"] == []


def test_renamed_module_still_adopts_its_unsynced_files(tmp_path):
    """An unsynced file belongs to its module, not to a "no module" list.

    The reverse lookup was keyed on the Canvas module name, so a rename sent
    every unmatched file in the directory to the orphan list while the module
    it belongs to sat right there with its other items matched.
    """
    _qmd(tmp_path, "04_Labs/01_Tensile.qmd", "Lab 1")
    _qmd(tmp_path, "04_Labs/09_Setup.qmd", "Setup guide")
    save_sync_map(str(tmp_path), {
        MAP_COURSE_KEY: 74,
        "04_Labs/01_Tensile.qmd": {"id": 11},
    })
    course = FakeCourse(modules=[FakeModule("Laboratories", items=[
        FakeItem("Lab 1", type="Assignment", content_id=11),
    ])])

    out = fetch_module_structure(course, str(tmp_path))

    titles = {i["title"]: i for i in out["modules"][0]["items"]}
    assert "Setup guide" in titles
    assert titles["Setup guide"]["local_only"] is True
    assert out["local_only_modules"] == []


def test_stale_map_entry_does_not_mask_the_file_on_disk(tmp_path):
    """Two paths, one Canvas id: the one still on disk must win.

    Renaming a file leaves the old key in the sync map, and the handlers'
    title-search fallback then re-attaches the new name to the same page, so
    both keys carry the same id. Last-write-wins used to hand the item to
    whichever key came later in the file. When that was the deleted one, the
    panel listed a file that no longer exists as synced and reported the real
    file as local-only.
    """
    _qmd(tmp_path, "06_Genomgangar/02_Click_alongs.qmd", "FEA click-alongs")
    # 03 is only in the map — it was deleted from disk.
    save_sync_map(str(tmp_path), {
        "06_Genomgangar/02_Click_alongs.qmd": {"id": 192422},
        "06_Genomgangar/03_Files.qmd": {"id": 192422},
    })

    page = FakePage(192422, "fea-click-alongs")
    item = FakeItem("FEA click-alongs", type="Page", id=1, page_url="fea-click-alongs")
    course = FakeCourse(modules=[FakeModule("Genomgangar", id=9, items=[item])],
                        pages=[page])

    result = fetch_module_structure(course, str(tmp_path))

    items = result["modules"][0]["items"]
    synced = [i for i in items if i["type"] == "Page"]
    assert len(synced) == 1
    assert synced[0]["local_path"] == "06_Genomgangar/02_Click_alongs.qmd"
    # And the live file is not also reported as local-only.
    assert not [i for i in items if i["type"] == "LocalOnly"]
