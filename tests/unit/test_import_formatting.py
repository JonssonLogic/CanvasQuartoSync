"""Unit tests for HtmlToMarkdown: the Canvas editor's Format menu on import.

The Rich Content Editor writes strike-through, underline, colours, fonts and
font sizes as <span style="...">, and alignment / direction on the block. Each
must come back as Pandoc markdown that Quarto renders to the same HTML.
"""

from import_from_canvas import HtmlToMarkdown


def conv(html):
    return HtmlToMarkdown().convert(html)


class TestTags:

    def test_bold_italic_code(self):
        assert conv('<p><strong>a</strong> <b>b</b> <em>c</em> <i>d</i> <code>e</code></p>') \
            == '**a** **b** *c* *d* `e`'

    def test_strike_tags(self):
        assert conv('<p><del>a</del> <s>b</s> <strike>c</strike></p>') == '~~a~~ ~~b~~ ~~c~~'

    def test_underline_tag(self):
        assert conv('<p><u>a</u></p>') == '[a]{.underline}'

    def test_superscript_subscript_use_pandoc_syntax(self):
        assert conv('<p>x<sup>2</sup> H<sub>2</sub>O</p>') == 'x^2^ H~2~O'

    def test_superscript_spaces_escaped(self):
        assert conv('<p>x<sup>a b</sup></p>') == r'x^a\ b^'

    def test_whitespace_moved_outside_markers(self):
        assert conv('<p>a<strong> b </strong>c</p>') == 'a **b** c'

    def test_short_tag_names_do_not_swallow_longer_ones(self):
        # <s> must not match <span>/<section>, <b> not <br>, <i> not <img>.
        html = '<section><p>a<br>b <img src="x.png"> <span>c</span></p></section><p><s>d</s></p>'
        out = conv(html)
        assert '~~d~~' in out
        assert '~~a' not in out and '**' not in out
        assert '![](x.png)' in out


class TestStyledSpans:

    def test_strike_through_span(self):
        html = ('<p><span style="text-decoration: line-through;">'
                'Skriv en individuell rapport, lämna in här.</span></p>')
        assert conv(html) == '~~Skriv en individuell rapport, lämna in här.~~'

    def test_underline_span(self):
        assert conv('<p><span style="text-decoration: underline;">a</span></p>') \
            == '[a]{.underline}'

    def test_strike_and_underline_with_colour(self):
        html = '<p><span style="text-decoration: underline line-through; color: #2dc26b;">a</span></p>'
        assert conv(html) == '~~[[a]{style="color: #2dc26b;"}]{.underline}~~'

    def test_text_colour(self):
        assert conv('<p><span style="color: #e03e2d;">röd</span></p>') \
            == '[röd]{style="color: #e03e2d;"}'

    def test_background_colour(self):
        assert conv('<p><span style="background-color: #fbeeb8;">a</span></p>') \
            == '[a]{style="background-color: #fbeeb8;"}'

    def test_font_size(self):
        assert conv('<p><span style="font-size: 18pt;">a</span></p>') \
            == '[a]{style="font-size: 18pt;"}'

    def test_font_family_quotes_stay_valid(self):
        html = '<p><span style="font-family: &quot;comic sans ms&quot;, sans-serif;">a</span></p>'
        assert conv(html) == '''[a]{style="font-family: 'comic sans ms', sans-serif;"}'''

    def test_nested_spans_keep_both_styles(self):
        html = ('<p><span style="color: #e03e2d;">röd <span style="background-color: #fbeeb8;">'
                'gul</span></span></p>')
        assert conv(html) == \
            '[röd [gul]{style="background-color: #fbeeb8;"}]{style="color: #e03e2d;"}'

    def test_strike_through_spanning_a_link(self):
        html = '<p><span style="text-decoration: line-through;">se <a href="https://ex.com">här</a></span></p>'
        assert conv(html) == '~~se [här](https://ex.com)~~'

    def test_bold_and_italic_css(self):
        html = '<p><span style="font-weight: bold;">a</span> <span style="font-style: italic;">b</span></p>'
        assert conv(html) == '**a** *b*'

    def test_plain_span_is_unwrapped(self):
        assert conv('<p><span>a</span> <span class="x">b</span></p>') == 'a b'

    def test_list_item_and_blockquote(self):
        html = ('<ul><li><span style="text-decoration: line-through;">a</span></li></ul>'
                '<blockquote><p><span style="text-decoration: line-through;">b</span></p></blockquote>')
        out = conv(html)
        assert '- ~~a~~' in out
        assert '> ~~b~~' in out


class TestBlockAlignment:

    def test_aligned_paragraph_becomes_fenced_div(self):
        assert conv('<p style="text-align: center;">a</p>') \
            == '::: {style="text-align: center;"}\na\n:::'

    def test_left_alignment_is_default(self):
        assert conv('<p style="text-align: left;">a</p>') == 'a'

    def test_rtl_paragraph(self):
        assert conv('<p dir="rtl">a</p>') == '::: {dir="rtl"}\na\n:::'

    def test_other_paragraph_styles_dropped(self):
        assert conv('<p style="margin: 0 0 8px 0;">a</p>') == 'a'

    def test_aligned_heading_gets_attributes(self):
        assert conv('<h2 style="text-align: right;">Rubrik</h2>') \
            == '## Rubrik {style="text-align: right;"}'

    def test_quarto_heading_attrs_ignored(self):
        assert conv('<h2 id="x" class="anchored" data-anchor-id="x">Rubrik</h2>') == '## Rubrik'
