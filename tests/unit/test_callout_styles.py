"""Tests for BaseHandler HTML transformation static methods."""

from handlers.base_handler import BaseHandler, _DEFAULT_CALLOUT_STYLES


# --- _inline_callout_styles ---

class TestInlineCalloutStyles:

    SAMPLE_CALLOUT = (
        '<div class="callout callout-style-default callout-tip callout-titled">\n'
        '<div class="callout-header d-flex align-content-center">\n'
        '<div class="callout-icon-container">\n<i class="callout-icon"></i>\n</div>\n'
        '<div class="callout-title-container flex-fill">\nTip Title\n</div>\n'
        '</div>\n'
        '<div class="callout-body-container callout-body">\n<p>Body text</p>\n</div>\n'
        '</div>'
    )

    def test_tip_callout_transformed(self):
        result = BaseHandler._inline_callout_styles(self.SAMPLE_CALLOUT, _DEFAULT_CALLOUT_STYLES)
        assert "border-left: 4px solid #198754" in result
        assert "background-color: #d1e7dd" in result
        assert "Tip Title" in result
        assert "Body text" in result

    def test_icon_included(self):
        result = BaseHandler._inline_callout_styles(self.SAMPLE_CALLOUT, _DEFAULT_CALLOUT_STYLES)
        assert "\U0001f4a1" in result  # lightbulb emoji

    def test_no_match_passthrough(self):
        html = "<p>No callouts here</p>"
        result = BaseHandler._inline_callout_styles(html, _DEFAULT_CALLOUT_STYLES)
        assert result == html

    def test_screen_reader_span_stripped_from_title(self):
        # Quarto injects <span class="screen-reader-only">Note</span> before the
        # custom title. Without Quarto's CSS, Canvas renders it as visible text,
        # causing "Note Syftet med peer-review" to appear on the same line.
        callout_with_sr_span = (
            '<div class="callout callout-style-default callout-note callout-titled">\n'
            '<div class="callout-header d-flex align-content-center">\n'
            '<div class="callout-icon-container">\n<i class="callout-icon"></i>\n</div>\n'
            '<div class="callout-title-container flex-fill">\n'
            '<span class="screen-reader-only">Note</span>Syftet med peer-review\n'
            '</div>\n'
            '</div>\n'
            '<div class="callout-body-container callout-body">\n<p>Body text</p>\n</div>\n'
            '</div>'
        )
        result = BaseHandler._inline_callout_styles(callout_with_sr_span, _DEFAULT_CALLOUT_STYLES)
        assert "Syftet med peer-review" in result
        assert "screen-reader-only" not in result
        assert ">Note<" not in result  # the sr-only span text must not appear as visible text

    def test_custom_styles_applied(self):
        custom = {
            "callout-tip": {"border": "#FF0000", "bg": "#FFCCCC", "icon": "!"},
        }
        result = BaseHandler._inline_callout_styles(self.SAMPLE_CALLOUT, custom)
        assert "border-left: 4px solid #FF0000" in result
        assert "background-color: #FFCCCC" in result


# --- _inline_syntax_highlighting ---

class TestInlineSyntaxHighlighting:

    def test_keyword_styled(self):
        html = '<span class="kw">def</span>'
        result = BaseHandler._inline_syntax_highlighting(html)
        assert 'style="color:#003B4F;font-weight:bold"' in result
        assert 'class="kw"' not in result

    def test_function_styled(self):
        html = '<span class="fu">main</span>()'
        result = BaseHandler._inline_syntax_highlighting(html)
        assert 'style="color:#4758AB"' in result

    def test_string_styled(self):
        html = '<span class="st">"hello"</span>'
        result = BaseHandler._inline_syntax_highlighting(html)
        assert 'style="color:#20794D"' in result

    def test_code_block_styled(self):
        html = '<div class="sourceCode" id="cb1"><pre class="sourceCode python"><code>x = 1</code></pre></div>'
        result = BaseHandler._inline_syntax_highlighting(html)
        assert "background-color:#f7f7f7" in result
        assert "padding:12px 16px" in result

    def test_copy_button_removed(self):
        html = '<button title="Copy to Clipboard" class="code-copy-button"><i class="bi"></i></button>'
        result = BaseHandler._inline_syntax_highlighting(html)
        assert "<button" not in result

    def test_line_number_anchors_removed(self):
        html = '<a href="#cb1-1" aria-hidden="true" tabindex="-1"></a>'
        result = BaseHandler._inline_syntax_highlighting(html)
        assert '<a href="#cb1-1"' not in result

    def test_plain_html_passthrough(self):
        html = "<p>No code here</p>"
        result = BaseHandler._inline_syntax_highlighting(html)
        assert result == html

    def test_multiple_tokens(self):
        html = '<span class="kw">def</span> <span class="fu">foo</span>(<span class="va">x</span>):'
        result = BaseHandler._inline_syntax_highlighting(html)
        assert 'color:#003B4F;font-weight:bold' in result  # kw
        assert 'color:#4758AB' in result                    # fu
        assert 'color:#111111' in result                    # va
