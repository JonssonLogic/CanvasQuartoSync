"""Unit tests for how render_quarto_document reports unresolved cross-references.

Quarto is replaced by a fake subprocess.run that writes the HTML Quarto would
have produced and returns the stderr it would have printed."""

import logging
import os
from types import SimpleNamespace
from unittest.mock import patch

from handlers.base_handler import BaseHandler
from handlers.page_handler import PageHandler

QUARTO_WARNING = (
    "WARNING (C:/Quarto/share/filters/main.lua:13876) "
    "Unable to resolve crossref @fig-geometry\n"
)
DOC = "---\ntitle: T\n---\n\nSee @fig-geometry.\n"


class TestUnresolvedCrossrefs:

    def test_reads_labels_from_stderr(self):
        assert BaseHandler._unresolved_crossrefs(QUARTO_WARNING, "") == ["fig-geometry"]

    def test_falls_back_to_the_rendered_body(self):
        assert BaseHandler._unresolved_crossrefs("", "<p>See ?@fig-load.</p>") == ["fig-load"]

    def test_merges_both_sources_without_duplicates(self):
        out = BaseHandler._unresolved_crossrefs(QUARTO_WARNING, "?@fig-geometry and ?@eq-x")
        assert out == ["fig-geometry", "eq-x"]

    def test_clean_render_reports_nothing(self):
        assert BaseHandler._unresolved_crossrefs("", "<p>Figure 1</p>") == []


def _fake_quarto(body, stderr=""):
    def run(cmd, **kwargs):
        html = cmd[2].replace(".qmd", ".html")
        with open(html, "w", encoding="utf-8") as f:
            f.write(f'<html><body><main id="quarto-document-content">{body}</main></body></html>')
        return SimpleNamespace(returncode=0, stdout=b"", stderr=stderr.encode("utf-8"))
    return run


def _render(tmp_path, body, stderr=""):
    with patch("handlers.base_handler.subprocess.run", side_effect=_fake_quarto(body, stderr)):
        return PageHandler().render_quarto_document(
            DOC, str(tmp_path), "01_X.qmd", content_root=str(tmp_path))


class TestRenderReporting:

    def test_warns_but_uploads_by_default(self, tmp_path, caplog):
        caplog.set_level(logging.WARNING, logger="canvas_sync")
        body = _render(tmp_path, "<p>See ?@fig-geometry.</p>", QUARTO_WARNING)
        assert body is not None and "?@fig-geometry" in body
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1
        assert "@fig-geometry" in warnings[0].getMessage()
        assert "strict_crossrefs" in warnings[0].getMessage()

    def test_strict_mode_skips_the_upload(self, tmp_path, caplog):
        caplog.set_level(logging.ERROR, logger="canvas_sync")
        (tmp_path / "config.toml").write_text("strict_crossrefs = true\n", encoding="utf-8")
        body = _render(tmp_path, "<p>See ?@fig-geometry.</p>", QUARTO_WARNING)
        assert body is None
        assert "@fig-geometry" in caplog.text and "not uploaded" in caplog.text
        # Temp render files are cleaned up on this path too.
        assert os.listdir(tmp_path) == ["config.toml"]

    def test_strict_mode_off_in_config_still_uploads(self, tmp_path):
        (tmp_path / "config.toml").write_text("strict_crossrefs = false\n", encoding="utf-8")
        assert _render(tmp_path, "<p>?@fig-geometry</p>", QUARTO_WARNING) is not None

    def test_clean_render_is_silent(self, tmp_path, caplog):
        caplog.set_level(logging.WARNING, logger="canvas_sync")
        body = _render(tmp_path, "<p>See Figure&nbsp;1.</p>")
        assert body is not None
        assert caplog.records == []
