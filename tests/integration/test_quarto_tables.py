"""Real Quarto renders through render_quarto_document, for table styling (#47).

The unit tests feed the pass HTML copied from Quarto. These render the source,
so they also catch Quarto changing the markup the pass relies on. Needs the
Quarto CLI; skipped when it is not installed."""

import re
import shutil

import pytest

import handlers.base_handler as base_handler
from handlers.page_handler import PageHandler

pytestmark = [
    pytest.mark.quarto,
    pytest.mark.slow,
    pytest.mark.skipif(shutil.which("quarto") is None, reason="Quarto CLI not installed"),
]

DOC = """---
title: T
---

| A | B |
|---|---|
| 1 | 2 |
| 3 | 4 |

: {.bordered}

| C | D |
|---|---|
| 5 | 6 |

: Results {#tbl-res .striped .schema}

See @tbl-res.

::: {.cqs-table stripe="#eef3f8" header-bg="var(--brand-heading)"}

| E | F |
|---|---|
| 7 | 8 |

: {.striped}

:::

| G | H |
|---|---|
| 9 | 0 |

: {.plain .striped}
"""

BRANDING = """:root {
  --brand-heading: #003865;
  --table-header-fg: #ffffff;
  --table-border: #999999;
}
table th { background-color: var(--brand-heading) !important; }
.schema { --table-stripe: #e0e8f0; }
"""


def _render(tmp_path, config="", css=None, doc=DOC):
    (tmp_path / "config.toml").write_text('[branding]\ncss = "branding.css"\n' + config,
                                          encoding="utf-8")
    if css is not None:
        (tmp_path / "branding.css").write_text(css, encoding="utf-8")
    return PageHandler().render_quarto_document(
        doc, str(tmp_path), "01_X.qmd", content_root=str(tmp_path))


def _tables(body):
    """Each table's markup, in page order."""
    return re.findall(r"<table\b.*?</table>", body, re.DOTALL)


@pytest.fixture
def seen(monkeypatch):
    """(input, output) of every call the sync makes to the table pass."""
    calls = []
    real = base_handler.inline_table_styles

    def spy(html, settings=None):
        out = real(html, settings)
        calls.append((html, out))
        return out

    monkeypatch.setattr(base_handler, "inline_table_styles", spy)
    return calls


NO_SETTINGS = ":root { --brand-heading: #003865; }\n"


def test_without_settings_or_wrappers_the_html_is_unchanged(tmp_path, seen):
    doc = re.sub(r"::: \{\.cqs-table.*?\n:::\n", "", DOC, flags=re.DOTALL)
    assert _render(tmp_path, css=NO_SETTINGS, doc=doc) is not None
    (before, after), = seen
    assert len(_tables(before)) == 3
    assert after == before


def test_without_settings_only_the_wrapped_table_changes(tmp_path, seen):
    assert _render(tmp_path, css=NO_SETTINGS) is not None
    (before, after), = seen
    b, a = _tables(before), _tables(after)
    assert [a[0], a[1], a[3]] == [b[0], b[1], b[3]]
    assert a[2] != b[2]
    assert "cqs-table" in before and "cqs-table" not in after


def test_every_layer_reaches_its_tables(tmp_path):
    body = _render(tmp_path, '[tables]\nstyle = ["striped"]\n', BRANDING)
    assert body is not None
    bordered, crossref, wrapped, plain = _tables(body)

    # Its own .bordered replaces the course's striped; course values still apply.
    assert 'class="caption-top table"' in bordered
    assert "border: 1px solid #999999" in bordered
    assert "background-color: #" not in bordered.split("<tbody>")[1]
    assert "color: #ffffff" in bordered

    # The named style's stripe, on the first body row only; Canvas's class gone.
    assert 'class="schema caption-top table"' in crossref
    rows = re.findall(r"<tr[^>]*>", crossref.split("<tbody>")[1])
    assert rows[0].endswith('style="background-color: #e0e8f0;">')
    assert "border-top: 1px solid #999999" in crossref
    assert "Table&nbsp;1" in body and "?@tbl-res" not in body

    # The wrapper's values win, var() reads branding.css, and the wrapper is gone.
    assert "background-color: #eef3f8" in wrapped
    assert "background-color: #003865; color: #ffffff" in wrapped
    assert "cqs-table" not in body

    # .plain: exactly as Quarto made it.
    assert "style=" not in plain
    assert "table-striped" in plain


def test_old_branding_table_rules_do_not_switch_styling_on(tmp_path):
    css = ":root { --brand-heading: #003865; }\ntable th { background-color: red !important; }\n"
    body = _render(tmp_path, css=css)
    bordered, crossref, wrapped, plain = _tables(body)
    assert "style=" not in bordered and "style=" not in crossref
    assert "table-bordered" in bordered and "table-striped" in crossref
