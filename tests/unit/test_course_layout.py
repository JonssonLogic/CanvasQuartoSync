"""Tests for handlers/course_layout.py: which modules, in what order, holding what.

Everything is a real temp directory. ``is_content`` is stubbed where the test
is about the order rules rather than about what the handlers claim.
"""

from handlers import course_layout as cl


def _page(path, title="T"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'---\ntitle: "{title}"\ncanvas:\n  type: page\n---\n\nBody.\n', encoding="utf-8")
    return path


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


class TestNNPrefix:

    def test_nn_folders_and_files_in_prefix_order(self, tmp_path):
        _page(tmp_path / "02_Labs" / "02_Two.qmd")
        _page(tmp_path / "02_Labs" / "01_One.qmd")
        _page(tmp_path / "01_Intro" / "01_Welcome.qmd")
        _page(tmp_path / "graphics" / "notes.qmd")
        _page(tmp_path / "02_Labs" / "draft.qmd")

        layout = cl.load_layout(str(tmp_path))

        assert layout.mode == cl.NN_PREFIX
        assert [(m.dir, m.name) for m in layout.modules] == [("01_Intro", "Intro"), ("02_Labs", "Labs")]
        assert layout.module("02_Labs").items == ["01_One.qmd", "02_Two.qmd"]

    def test_why_excluded_names_the_prefix(self, tmp_path):
        _page(tmp_path / "01_Labs" / "draft.qmd")
        layout = cl.load_layout(str(tmp_path))
        assert "NN_ prefix" in layout.why_excluded("01_Labs/draft.qmd")


class TestOrderFiles:

    def test_modules_in_listed_order_with_display_names(self, tmp_path):
        _page(tmp_path / "Labs" / "One.qmd")
        _page(tmp_path / "Intro" / "Welcome.qmd")
        _write(tmp_path / "_modules.yml", "- Intro: Welcome to the course\n- Labs\n")

        layout = cl.load_layout(str(tmp_path))

        assert layout.mode == cl.ORDER_FILES
        assert [(m.dir, m.name) for m in layout.modules] == [
            ("Intro", "Welcome to the course"), ("Labs", "Labs")]

    def test_listed_items_first_then_content_appended(self, tmp_path):
        """A new page needs no bookkeeping: it joins at the end."""
        for n in ("A.qmd", "B.qmd", "C.qmd"):
            _page(tmp_path / "Labs" / n)
        _write(tmp_path / "_modules.yml", "- Labs\n")
        _write(tmp_path / "Labs" / "_order.yml", "- C.qmd\n- A.qmd\n")

        mod = cl.load_layout(str(tmp_path)).module("Labs")

        assert mod.items == ["C.qmd", "A.qmd", "B.qmd"]
        assert mod.auto_added == ["B.qmd"]

    def test_attachments_only_when_listed(self, tmp_path):
        """A PDF or a plain note declares nothing, so it must be listed."""
        _page(tmp_path / "Labs" / "A.qmd")
        _write(tmp_path / "Labs" / "notes.md", "just notes\n")
        (tmp_path / "Labs" / "handout.pdf").write_bytes(b"%PDF")
        (tmp_path / "Labs" / "slides.pdf").write_bytes(b"%PDF")
        _write(tmp_path / "_modules.yml", "- Labs\n")
        _write(tmp_path / "Labs" / "_order.yml", "- handout.pdf\n- A.qmd\n")

        mod = cl.load_layout(str(tmp_path)).module("Labs")

        assert mod.items == ["handout.pdf", "A.qmd"]
        assert mod.unlisted == ["notes.md", "slides.pdf"]

    def test_underscore_files_stay_out_unless_listed(self, tmp_path):
        _page(tmp_path / "Labs" / "_draft.qmd")
        _page(tmp_path / "Labs" / "_kept.qmd")
        _write(tmp_path / "_modules.yml", "- Labs\n")
        _write(tmp_path / "Labs" / "_order.yml", "- _kept.qmd\n")

        layout = cl.load_layout(str(tmp_path))

        assert layout.module("Labs").items == ["_kept.qmd"]
        assert "_" in layout.why_excluded("Labs/_draft.qmd")

    def test_unlisted_folders_are_not_modules(self, tmp_path):
        _page(tmp_path / "Labs" / "A.qmd")
        _page(tmp_path / "Extra" / "B.qmd")
        (tmp_path / "graphics").mkdir()
        (tmp_path / "graphics" / "x.png").write_bytes(b"png")
        _write(tmp_path / "_modules.yml", "- Labs\n")

        layout = cl.load_layout(str(tmp_path))

        assert [m.dir for m in layout.modules] == ["Labs"]
        # Offered on the board, because it holds content. graphics/ does not.
        assert layout.unlisted_dirs == ["Extra"]
        assert "not listed" in layout.why_excluded("Extra/B.qmd")

    def test_root_files_listed_in_modules_sync_outside_any_module(self, tmp_path):
        _page(tmp_path / "Start.qmd")
        _page(tmp_path / "CLAUDE.qmd")
        _write(tmp_path / "_modules.yml", "- Start.qmd\n")

        layout = cl.load_layout(str(tmp_path))

        assert layout.root_items == ["Start.qmd"]
        assert layout.locate("Start.qmd") == (None, True)
        assert layout.locate("CLAUDE.qmd") == (None, False)

    def test_names_match_case_insensitively(self, tmp_path):
        _page(tmp_path / "Labs" / "One.qmd")
        _write(tmp_path / "_modules.yml", "- labs\n")
        _write(tmp_path / "Labs" / "_order.yml", "- one.QMD\n")

        mod = cl.load_layout(str(tmp_path)).modules[0]

        assert (mod.dir, mod.items, mod.auto_added) == ("Labs", ["One.qmd"], [])


class TestTolerantReading:
    """A merge must never break a sync. See the union-merge note in the module."""

    def test_duplicates_keep_their_first_place(self, tmp_path):
        for n in ("A.qmd", "B.qmd"):
            _page(tmp_path / "Labs" / n)
        _write(tmp_path / "_modules.yml", "- Labs\n- Labs\n")
        _write(tmp_path / "Labs" / "_order.yml", "- B.qmd\n- A.qmd\n- B.qmd\n")

        layout = cl.load_layout(str(tmp_path))

        assert [m.dir for m in layout.modules] == ["Labs"]
        assert layout.modules[0].items == ["B.qmd", "A.qmd"]
        assert layout.problems == []

    def test_missing_entries_are_skipped_with_a_warning(self, tmp_path):
        _page(tmp_path / "Labs" / "A.qmd")
        _write(tmp_path / "_modules.yml", "- Gone\n- Labs\n")
        _write(tmp_path / "Labs" / "_order.yml", "- Deleted.qmd\n- A.qmd\n")

        layout = cl.load_layout(str(tmp_path))

        assert layout.modules[0].items == ["A.qmd"]
        assert sorted(p.path for p in layout.problems) == ["Labs/_order.yml", "_modules.yml"]
        assert all(p.level == "warning" for p in layout.problems)

    def test_conflict_markers_still_yield_an_order(self, tmp_path):
        for n in ("A.qmd", "B.qmd", "C.qmd"):
            _page(tmp_path / "Labs" / n)
        _write(tmp_path / "_modules.yml", "- Labs\n")
        _write(tmp_path / "Labs" / "_order.yml",
               "- A.qmd\n<<<<<<< HEAD\n- B.qmd\n=======\n- C.qmd\n>>>>>>> theirs\n")

        layout = cl.load_layout(str(tmp_path))

        assert layout.modules[0].items == ["A.qmd", "B.qmd", "C.qmd"]
        assert any("conflict" in p.message for p in layout.problems)

    def test_not_a_list_is_an_error_not_a_crash(self, tmp_path):
        _page(tmp_path / "Labs" / "A.qmd")
        _write(tmp_path / "_modules.yml", "Labs: yes\n")

        layout = cl.load_layout(str(tmp_path))

        assert layout.modules == []
        assert layout.problems[0].level == "error"


class TestWriting:

    def test_round_trip_with_awkward_names(self, tmp_path):
        names = ["Kurs PM.qmd", "yes", "01: intro.qmd", "Fjädrar.qmd", "#hash.qmd"]
        for n in names:
            (tmp_path / "M").mkdir(exist_ok=True)
            (tmp_path / "M" / n).write_text("x", encoding="utf-8")
        cl.write_order(str(tmp_path), "M", names)
        assert [n for n, _ in cl.read_list(str(tmp_path / "M" / cl.ORDER_FILE))] == names

    def test_module_order_keeps_display_names(self, tmp_path):
        for d in ("A", "B", "C"):
            (tmp_path / d).mkdir()
        cl.write_modules(str(tmp_path), [("A", None), ("B", "Bee"), ("C", None)])

        cl.set_module_order(str(tmp_path), ["C", "B"])

        assert cl.read_list(str(tmp_path / cl.MODULES_FILE)) == [("C", None), ("B", "Bee"), ("A", None)]

    def test_gitattributes_marks_union_merge_once(self, tmp_path):
        (tmp_path / ".gitattributes").write_text("*.bat text eol=crlf", encoding="utf-8")

        assert cl.ensure_gitattributes(str(tmp_path)) is True
        assert cl.ensure_gitattributes(str(tmp_path)) is False

        lines = (tmp_path / ".gitattributes").read_text(encoding="utf-8").splitlines()
        assert lines[0] == "*.bat text eol=crlf"
        assert "_modules.yml merge=union" in lines and "_order.yml merge=union" in lines

    def test_free_name_never_overwrites(self, tmp_path):
        (tmp_path / "A.qmd").write_text("x")
        (tmp_path / "A_2.qmd").write_text("x")
        assert cl.free_name(str(tmp_path), "A.qmd") == "A_3.qmd"
        assert cl.free_name(str(tmp_path), "B.qmd") == "B.qmd"


class TestReconcileOrders:
    """The order file follows the folder: new files in, dead lines out, renames in place."""

    def _course(self, tmp_path, files, order):
        for n in files:
            _page(tmp_path / "Labs" / n)
        _write(tmp_path / "_modules.yml", "- Labs\n")
        if order is not None:
            _write(tmp_path / "Labs" / "_order.yml", order)
        return str(tmp_path)

    def _order(self, tmp_path):
        return [n for n, _ in cl.read_list(str(tmp_path / "Labs" / "_order.yml"))]

    def test_new_file_is_listed_last_and_manual_order_survives(self, tmp_path):
        root = self._course(tmp_path, ["A.qmd", "B.qmd", "C.qmd"], "- C.qmd\n- A.qmd\n")
        changes = cl.reconcile_orders(root)
        assert self._order(tmp_path) == ["C.qmd", "A.qmd", "B.qmd"]
        assert [(c.kind, c.name) for c in changes] == [("added", "B.qmd")]

    def test_dead_line_is_removed(self, tmp_path):
        root = self._course(tmp_path, ["A.qmd"], "- Gone.qmd\n- A.qmd\n")
        changes = cl.reconcile_orders(root)
        assert self._order(tmp_path) == ["A.qmd"]
        assert [(c.kind, c.name) for c in changes] == [("removed", "Gone.qmd")]
        assert not cl.load_layout(root).problems

    def test_rename_keeps_the_place_and_moves_the_canvas_id(self, tmp_path):
        """The case that started this: New_External_URL.qmd became Kuggar_URL.qmd."""
        root = self._course(tmp_path, ["First.qmd", "Kuggar_URL.qmd", "Last.qmd"],
                            "- First.qmd\n- New_External_URL.qmd\n- Last.qmd\n")
        smap = {"Labs/New_External_URL.qmd": {"id": 7, "mtime": 1.0}}
        changes = cl.reconcile_orders(root, smap)
        assert self._order(tmp_path) == ["First.qmd", "Kuggar_URL.qmd", "Last.qmd"]
        assert [(c.kind, c.old, c.name) for c in changes] == \
            [("renamed", "New_External_URL.qmd", "Kuggar_URL.qmd")]
        assert smap == {"Labs/Kuggar_URL.qmd": {"id": 7, "mtime": 1.0}}

    def test_ambiguous_rename_is_not_guessed(self, tmp_path):
        root = self._course(tmp_path, ["N1.qmd", "N2.qmd"], "- Old1.qmd\n- Old2.qmd\n")
        smap = {"Labs/Old1.qmd": {"id": 1}, "Labs/Old2.qmd": {"id": 2}}
        cl.reconcile_orders(root, smap)
        assert self._order(tmp_path) == ["N1.qmd", "N2.qmd"]
        assert set(smap) == {"Labs/Old1.qmd", "Labs/Old2.qmd"}   # no id moved

    def test_different_extension_is_not_a_rename(self, tmp_path):
        root = self._course(tmp_path, ["A.qmd"], "- Old.pdf\n- A.qmd\n")
        smap = {"Labs/Old.pdf": {"id": 9}}
        cl.reconcile_orders(root, smap)
        assert "Labs/Old.pdf" in smap

    def test_a_file_that_already_has_an_id_never_receives_another(self, tmp_path):
        root = self._course(tmp_path, ["New.qmd"], "- Old.qmd\n")
        smap = {"Labs/Old.qmd": {"id": 1}, "Labs/New.qmd": {"id": 2}}
        cl.reconcile_orders(root, smap)
        assert smap["Labs/New.qmd"] == {"id": 2}
        assert self._order(tmp_path) == ["New.qmd"]

    def test_dry_run_writes_nothing(self, tmp_path):
        root = self._course(tmp_path, ["A.qmd", "B.qmd"], "- A.qmd\n- Gone.qmd\n")
        before = (tmp_path / "Labs" / "_order.yml").read_text(encoding="utf-8")
        changes = cl.reconcile_orders(root, write=False)
        assert changes and (tmp_path / "Labs" / "_order.yml").read_text(encoding="utf-8") == before

    def test_attachments_are_never_added_and_listed_ones_stay(self, tmp_path):
        root = self._course(tmp_path, ["A.qmd"], "- A.qmd\n- Kursplan.pdf\n")
        (tmp_path / "Labs" / "Kursplan.pdf").write_bytes(b"%PDF")
        (tmp_path / "Labs" / "notes.txt").write_text("x", encoding="utf-8")
        assert cl.reconcile_orders(root) == []
        assert self._order(tmp_path) == ["A.qmd", "Kursplan.pdf"]

    def test_in_step_course_is_left_alone(self, tmp_path):
        root = self._course(tmp_path, ["A.qmd"], "- A.qmd\n")
        p = tmp_path / "Labs" / "_order.yml"
        p.write_text("# my own comment\n- A.qmd\n", encoding="utf-8")
        assert cl.reconcile_orders(root) == []
        assert "my own comment" in p.read_text(encoding="utf-8")

    def test_module_without_order_file_gets_one(self, tmp_path):
        root = self._course(tmp_path, ["B.qmd", "A.qmd"], None)
        cl.reconcile_orders(root)
        assert self._order(tmp_path) == ["A.qmd", "B.qmd"]

    def test_nn_course_is_ignored(self, tmp_path):
        _page(tmp_path / "01_Mod" / "01_A.qmd")
        assert cl.reconcile_orders(str(tmp_path)) == []
