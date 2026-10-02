"""Real Quarto renders through render_quarto_document.

Checks that cross-references, equation numbers and citations survive the trip
into the body the sync ships to Canvas, and that an unresolved reference is
reported. Needs the Quarto CLI; skipped when it is not installed."""

import logging
import shutil

import pytest

from handlers.page_handler import PageHandler

pytestmark = [
    pytest.mark.quarto,
    pytest.mark.slow,
    pytest.mark.skipif(shutil.which("quarto") is None, reason="Quarto CLI not installed"),
]

STACKED = """---
title: T
---

See @fig-a and @fig-b.

![Alpha](a.png){#fig-a}
![Beta](b.png){#fig-b}
"""

RESOLVED = """---
title: T
bibliography: refs.bib
---

See @fig-a, @eq-s and @hooke1678.

![Alpha](a.png){#fig-a}

$$
\\sigma = \\frac{F}{A}
$$ {#eq-s}

::: {#refs}
:::
"""

BIB = ("@book{hooke1678, title={De Potentia Restitutiva}, "
       "author={Hooke, Robert}, year={1678}}\n")


def _render(tmp_path, doc):
    return PageHandler().render_quarto_document(
        doc, str(tmp_path), "01_X.qmd", content_root=str(tmp_path))


def test_stacked_images_are_reported(tmp_path, caplog):
    caplog.set_level(logging.WARNING, logger="canvas_sync")
    body = _render(tmp_path, STACKED)
    assert body is not None
    assert "?@fig-a" in body and "?@fig-b" in body
    assert "@fig-a, @fig-b" in caplog.text


def test_resolved_references_reach_the_canvas_body(tmp_path, caplog):
    (tmp_path / "refs.bib").write_text(BIB, encoding="utf-8")
    caplog.set_level(logging.WARNING, logger="canvas_sync")
    body = _render(tmp_path, RESOLVED)
    assert body is not None
    assert "?@" not in body
    assert "Unresolved" not in caplog.text
    assert "Figure&nbsp;1" in body
    assert "Equation&nbsp;1" in body
    # The equation number rides inside the LaTeX handed to Canvas.
    assert "\\tag{1}" in body
    # Citation text and the bibliography live inside <main>, so they ship too.
    assert "Hooke" in body and 'id="refs"' in body
