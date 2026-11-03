"""Unit tests for rendering outside the course folder (handlers/render_workspace.py).

A render runs in a mirror of the course under the temp folder, so no tmp-*
file ever lands next to the source, where Dropbox or OneDrive would lock it.
Quarto is replaced by a fake subprocess.run that writes what Quarto would."""

import logging
import os
import subprocess
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from handlers import render_workspace as rw
from handlers.base_handler import BaseHandler
from handlers.content_utils import ACTIVE_ASSET_IDS
from handlers.page_handler import PageHandler
from handlers.study_guide_handler import StudyGuideHandler


@pytest.fixture(autouse=True)
def no_open_runs():
    rw._runs.clear()
    yield
    rw._runs.clear()


def _course(root):
    """A small course: one module with a page and an image, plus clutter."""
    (root / "_quarto.yml").write_text("project:\n  type: default\n", encoding="utf-8")
    mod = root / "Labs"
    (mod / "graphics").mkdir(parents=True)
    (mod / "Lab1.qmd").write_text("---\ntitle: Lab 1\n---\n\n![](graphics/a.png)\n", encoding="utf-8")
    (mod / "graphics" / "a.png").write_bytes(b"png")
    (mod / "tmp-pdf-Old.qmd").write_text("leftover", encoding="utf-8")
    (root / ".git").mkdir()
    (root / ".git" / "HEAD").write_text("ref", encoding="utf-8")
    (root / "_site").mkdir()
    (root / "_site" / "index.html").write_text("old", encoding="utf-8")
    (root / "_freeze").mkdir()
    (root / "_freeze" / "x.json").write_text("{}", encoding="utf-8")
    return mod


def _tmp_files(root):
    return [os.path.join(d, f) for d, _, fs in os.walk(root) for f in fs
            if f.startswith(('tmp-html-', 'tmp-pdf-New', '_temp_quiz_render'))]


class TestMirror:

    def test_mirrors_the_tree_and_skips_clutter(self, tmp_path):
        _course(tmp_path)
        m = rw.mirror_course(str(tmp_path))
        try:
            assert os.path.isfile(os.path.join(m, "_quarto.yml"))
            assert os.path.isfile(os.path.join(m, "Labs", "graphics", "a.png"))
            assert not os.path.exists(os.path.join(m, ".git"))
            assert not os.path.exists(os.path.join(m, "_site"))
            assert not os.path.exists(os.path.join(m, "Labs", "tmp-pdf-Old.qmd"))
            assert not os.path.normcase(m).startswith(os.path.normcase(str(tmp_path)))
        finally:
            rw._remove(m)

    def test_files_are_hard_links(self, tmp_path):
        _course(tmp_path)
        m = rw.mirror_course(str(tmp_path))
        try:
            src = tmp_path / "Labs" / "graphics" / "a.png"
            assert os.path.samefile(src, os.path.join(m, "Labs", "graphics", "a.png"))
        finally:
            rw._remove(m)

    def test_freeze_is_copied_not_linked(self, tmp_path):
        # Quarto rewrites _freeze files; a link would carry that into the course.
        _course(tmp_path)
        m = rw.mirror_course(str(tmp_path))
        try:
            copy = os.path.join(m, "_freeze", "x.json")
            assert os.path.isfile(copy)
            assert not os.path.samefile(tmp_path / "_freeze" / "x.json", copy)
        finally:
            rw._remove(m)

    def test_copies_when_links_fail(self, tmp_path):
        _course(tmp_path)
        with patch("handlers.render_workspace.os.link", side_effect=OSError("cross-device")) as link:
            m = rw.mirror_course(str(tmp_path))
        try:
            src = tmp_path / "Labs" / "Lab1.qmd"
            dst = os.path.join(m, "Labs", "Lab1.qmd")
            assert os.path.isfile(dst) and not os.path.samefile(src, dst)
            assert link.call_count == 1      # gave up on links after the first failure
        finally:
            rw._remove(m)

    def test_removed_when_mirroring_fails(self, tmp_path):
        _course(tmp_path)
        made = []
        real_mkdtemp = rw.tempfile.mkdtemp

        def mkdtemp(**kw):
            made.append(real_mkdtemp(**kw))
            return made[-1]

        with patch("handlers.render_workspace.tempfile.mkdtemp", side_effect=mkdtemp), \
             patch("handlers.render_workspace.os.makedirs", side_effect=RuntimeError("boom")):
            with pytest.raises(RuntimeError):
                rw.mirror_course(str(tmp_path))
        assert made and not os.path.exists(made[0])


class TestWorkspace:

    def test_one_mirror_per_run_removed_at_the_end(self, tmp_path):
        mod = _course(tmp_path)
        with rw.render_workspace(str(tmp_path)):
            with rw.render_dir(str(mod), str(tmp_path)) as a:
                pass
            with rw.render_dir(str(mod), str(tmp_path)) as b:
                pass
            assert a == b and os.path.isdir(a)
            assert not os.path.normcase(a).startswith(os.path.normcase(str(tmp_path)))
        assert not os.path.exists(a)

    def test_removed_when_the_run_raises(self, tmp_path):
        mod = _course(tmp_path)
        with pytest.raises(ValueError):
            with rw.render_workspace(str(tmp_path)):
                with rw.render_dir(str(mod), str(tmp_path)) as work:
                    pass
                raise ValueError
        assert not os.path.exists(work)
        assert rw._runs == {}

    def test_no_mirror_when_nothing_renders(self, tmp_path):
        _course(tmp_path)
        with patch("handlers.render_workspace.mirror_course") as mirror:
            with rw.render_workspace(str(tmp_path)):
                pass
        mirror.assert_not_called()

    def test_nested_runs_share_the_outer_mirror(self, tmp_path):
        mod = _course(tmp_path)
        with rw.render_workspace(str(tmp_path)):
            with rw.render_dir(str(mod), str(tmp_path)) as outer:
                pass
            with rw.render_workspace(str(tmp_path)):
                with rw.render_dir(str(mod), str(tmp_path)) as inner:
                    pass
            assert inner == outer and os.path.isdir(outer)

    def test_outside_a_run_the_mirror_is_per_render(self, tmp_path):
        mod = _course(tmp_path)
        with rw.render_dir(str(mod), str(tmp_path)) as work:
            assert os.path.isdir(work)
            assert os.path.isfile(os.path.join(work, "graphics", "a.png"))
        assert not os.path.exists(work)

    def test_in_place_when_the_mirror_cannot_be_made(self, tmp_path, caplog):
        caplog.set_level(logging.WARNING, logger="canvas_sync")
        mod = _course(tmp_path)
        with patch("handlers.render_workspace.mirror_course", side_effect=OSError("disk full")):
            with rw.render_dir(str(mod), str(tmp_path)) as work:
                assert work == str(mod)
        assert any("Rendering in place" in r.getMessage() for r in caplog.records)

    def test_in_place_without_a_course_root(self, tmp_path):
        with rw.render_dir(str(tmp_path), None) as work:
            assert work == str(tmp_path)


class TestPathMapping:

    def test_real_path_maps_mirror_paths_back(self, tmp_path):
        mod = _course(tmp_path)
        with rw.render_workspace(str(tmp_path)):
            with rw.render_dir(str(mod), str(tmp_path)) as work:
                pdf = os.path.join(work, "KursPM.pdf")
                assert os.path.normcase(rw.real_path(pdf)) == \
                    os.path.normcase(str(mod / "KursPM.pdf"))

    def test_real_path_leaves_other_paths_alone(self, tmp_path):
        assert rw.real_path(str(tmp_path / "x.qmd")) == str(tmp_path / "x.qmd")

    def test_real_text_rewrites_both_slash_styles(self, tmp_path):
        work = os.path.join(str(tmp_path), "mirror", "Labs")
        src = os.path.join(str(tmp_path), "course", "Labs")
        text = f"error in {work}{os.sep}tmp.qmd and {work.replace(os.sep, '/')}/tmp.qmd"
        out = rw.real_text(text, work, src)
        assert work not in out and work.replace(os.sep, '/') not in out
        assert src in out and src.replace(os.sep, '/') in out


def _fake_quarto(write_html=True, write_pdf=False, fail=None, seen=None):
    """subprocess.run stand-in: writes the output Quarto would, then fails if asked."""
    def run(cmd, **kwargs):
        qmd = cmd[2]
        if seen is not None:
            seen.append(qmd)
        stem = os.path.splitext(qmd)[0]
        if write_html:
            with open(stem + ".html", "w", encoding="utf-8") as f:
                f.write('<main id="quarto-document-content"><p>Hi</p></main>')
        if write_pdf:
            with open(stem + ".pdf", "wb") as f:
                f.write(b"%PDF-1.7")
        os.makedirs(stem + "_files", exist_ok=True)
        if fail:
            raise subprocess.CalledProcessError(1, cmd, output=b"", stderr=fail.encode())
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")
    return run


class TestRendersOutsideTheCourse:

    def test_html_render_leaves_nothing_in_the_course(self, tmp_path):
        mod = _course(tmp_path)
        seen = []
        with patch("handlers.base_handler.subprocess.run", side_effect=_fake_quarto(seen=seen)):
            body = PageHandler().render_quarto_document(
                "---\ntitle: T\n---\n\nHi\n", str(mod), "Lab1.qmd", content_root=str(tmp_path))
        assert "Hi" in body
        assert not os.path.normcase(seen[0]).startswith(os.path.normcase(str(tmp_path)))
        assert _tmp_files(tmp_path) == []

    def test_pdf_render_leaves_nothing_in_the_course(self, tmp_path):
        mod = _course(tmp_path)
        with rw.render_workspace(str(tmp_path)):
            with patch("handlers.base_handler.subprocess.run",
                       side_effect=_fake_quarto(write_html=False, write_pdf=True)):
                pdf = BaseHandler.render_quarto_pdf(
                    PageHandler(), "x", str(mod), "New.qmd", content_root=str(tmp_path))
            assert pdf and os.path.isfile(pdf)
            assert _tmp_files(tmp_path) == []
        assert not os.path.exists(pdf)


class TestCleanupOnlyFailure:
    """Quarto exits 1 when only its cleanup fails (os error 32), after the PDF is written."""

    LOCKED = ("ERROR: The process cannot access the file because it is being used by "
              "another process. (os error 32): remove 'tmp-pdf-New_files'")

    def test_fresh_pdf_is_used(self, tmp_path, caplog):
        caplog.set_level(logging.WARNING, logger="canvas_sync")
        with patch("handlers.base_handler.subprocess.run",
                   side_effect=_fake_quarto(write_html=False, write_pdf=True, fail=self.LOCKED)):
            pdf = PageHandler()._render_pdf_in("x", str(tmp_path), str(tmp_path), "New.qmd")
        assert pdf and os.path.isfile(pdf)
        assert any("after writing the PDF" in r.getMessage() for r in caplog.records)

    def test_no_pdf_is_still_a_failure(self, tmp_path):
        with patch("handlers.base_handler.subprocess.run",
                   side_effect=_fake_quarto(write_html=False, fail="LaTeX Error")):
            assert PageHandler()._render_pdf_in("x", str(tmp_path), str(tmp_path), "New.qmd") is None

    def test_stale_pdf_from_an_earlier_run_is_not_used(self, tmp_path):
        old = tmp_path / "tmp-pdf-New.pdf"
        old.write_bytes(b"%PDF old")
        os.utime(old, (0, 0))
        with patch("handlers.base_handler.subprocess.run",
                   side_effect=_fake_quarto(write_html=False, fail="LaTeX Error")):
            assert PageHandler()._render_pdf_in("x", str(tmp_path), str(tmp_path), "New.qmd") is None


GUIDE = """---
title: KursPM
canvas:
  type: study_guide
  pdf:
    filename: KursPM.pdf
---

Text.
"""


class TestStudyGuideKeepsTheOldPdf:
    """A failed PDF render must not let the orphan pruner delete the PDF in Canvas."""

    def _sync(self, tmp_path, pdf_path):
        mod = tmp_path / "Docs"
        mod.mkdir()
        guide = mod / "KursPM.qmd"
        guide.write_text(GUIDE, encoding="utf-8")
        entry = {"id": 11, "source_hash": "changed", "pdf_file_id": 2725570}
        course = MagicMock()
        page = MagicMock(page_id=11, url="kurspm")
        course.get_page.return_value = page
        h = StudyGuideHandler()
        with patch("handlers.study_guide_handler.get_mapped_id", return_value=(11, entry)), \
             patch("handlers.study_guide_handler.process_content", side_effect=lambda c, *a, **k: c), \
             patch("handlers.study_guide_handler.check_drift", return_value={"drifted": False}), \
             patch("handlers.study_guide_handler.save_mapped_id"), \
             patch("handlers.study_guide_handler.store_canvas_hash"), \
             patch("handlers.study_guide_handler.resolve_stored_html", return_value=""), \
             patch("handlers.study_guide_handler.get_policy") as policy, \
             patch.object(h, "render_quarto_document", return_value="<p>Text</p>"), \
             patch.object(h, "render_quarto_pdf", return_value=pdf_path), \
             patch.object(h, "add_to_module"):
            policy.return_value.review.return_value = True
            h.sync(str(guide), course, module=None, content_root=str(tmp_path))

    def test_failed_render_keeps_the_old_pdf_active(self, tmp_path):
        self._sync(tmp_path, None)
        assert 2725570 in ACTIVE_ASSET_IDS

    def test_failed_upload_keeps_the_old_pdf_active(self, tmp_path):
        pdf = tmp_path / "tmp-pdf-KursPM.pdf"
        pdf.write_bytes(b"%PDF")
        with patch("handlers.study_guide_handler.upload_file", return_value=(None, None)):
            self._sync(tmp_path, str(pdf))
        assert 2725570 in ACTIVE_ASSET_IDS

    def test_new_pdf_replaces_the_old_one(self, tmp_path):
        pdf = tmp_path / "tmp-pdf-KursPM.pdf"
        pdf.write_bytes(b"%PDF")

        def upload(course, path, folder, content_root=None):
            ACTIVE_ASSET_IDS.add(99)
            return "url", 99

        with patch("handlers.study_guide_handler.upload_file", side_effect=upload), \
             patch("handlers.study_guide_handler.load_sync_map",
                   return_value={"Docs/KursPM.qmd": {}}), \
             patch("handlers.study_guide_handler.save_sync_map"):
            self._sync(tmp_path, str(pdf))
        assert 99 in ACTIVE_ASSET_IDS and 2725570 not in ACTIVE_ASSET_IDS
