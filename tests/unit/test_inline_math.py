"""Unit tests for BaseHandler._inline_math (Quarto math -> Canvas equation image)."""

import re
from urllib.parse import unquote

from handlers.base_handler import BaseHandler

inline_math = BaseHandler._inline_math


def _src(html):
    """Extract the /equation_images/<encoded> path from the first img."""
    m = re.search(r'src="/equation_images/([^"]+)"', html)
    assert m, f"no equation_image src in: {html}"
    return m.group(1)


class TestInlineMath:

    def test_inline_becomes_equation_image(self):
        out = inline_math('<span class="math inline">\\(E = mc^2\\)</span>')
        assert 'class="equation_image"' in out
        assert 'data-equation-content="E = mc^2"' in out
        # Inline math is not wrapped in a centered paragraph.
        assert "text-align: center" not in out

    def test_display_is_centered(self):
        out = inline_math('<span class="math display">\\[\\sigma = \\frac{F}{A}\\]</span>')
        assert "text-align: center" in out
        assert 'class="equation_image"' in out

    def test_src_is_double_url_encoded_and_roundtrips(self):
        """unquote(unquote(src)) must recover the raw LaTeX (matches the importer)."""
        latex = "\\sigma = \\frac{F}{A}"
        out = inline_math(f'<span class="math display">\\[{latex}\\]</span>')
        recovered = unquote(unquote(_src(out)))
        assert recovered == latex

    def test_html_entities_unescaped(self):
        # Pandoc escapes & as &amp; inside the span; the equation content is raw.
        out = inline_math('<span class="math display">\\[a &amp;= b\\]</span>')
        assert unquote(unquote(_src(out))) == "a &= b"
        assert 'data-equation-content="a &amp;= b"' in out  # re-escaped for the attr

    def test_whitespace_collapsed(self):
        out = inline_math('<span class="math display">\\[\\sigma\n  =\n  E\\varepsilon\\]</span>')
        assert unquote(unquote(_src(out))) == "\\sigma = E\\varepsilon"

    def test_multiple_and_mixed(self):
        html = (
            'Inline <span class="math inline">\\(x^2\\)</span> and display '
            '<span class="math display">\\[y = x\\]</span> done.'
        )
        out = inline_math(html)
        assert out.count('class="equation_image"') == 2
        assert "math inline" not in out and "math display" not in out

    def test_non_math_untouched(self):
        html = '<p>No math here, just <code>x = 1</code>.</p>'
        assert inline_math(html) == html
