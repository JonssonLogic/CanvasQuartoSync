from abc import ABC, abstractmethod
import os
import subprocess
import re
from handlers.content_utils import safe_delete_file, safe_delete_dir
from handlers.config import load_config
from handlers.log import logger

# Default callout styles (used when no branding.css defines them)
_DEFAULT_CALLOUT_STYLES = {
    'callout-tip':       {'border': '#198754', 'bg': '#d1e7dd', 'icon': '\U0001f4a1'},
    'callout-important': {'border': '#dc3545', 'bg': '#f8d7da', 'icon': '\u2757'},
    'callout-warning':   {'border': '#ffc107', 'bg': '#fff3cd', 'icon': '\u26a0\ufe0f'},
    'callout-note':      {'border': '#0d6efd', 'bg': '#cfe2ff', 'icon': '\U0001f4dd'},
    'callout-caution':   {'border': '#fd7e14', 'bg': '#ffe5d0', 'icon': '\U0001f536'},
}

_callout_cache = {}

def _load_callout_styles(content_root):
    """Parse callout styles from branding.css, with defaults as fallback."""
    if content_root in _callout_cache:
        return _callout_cache[content_root]

    styles = dict(_DEFAULT_CALLOUT_STYLES)

    cfg = load_config(content_root)
    css_path = cfg.get('branding', {}).get('css', '')
    if css_path:
        if not os.path.isabs(css_path):
            css_path = os.path.join(content_root, css_path)
        if os.path.exists(css_path):
            with open(css_path, 'r', encoding='utf-8') as f:
                css = f.read()
            for cls in list(styles.keys()):
                m = re.search(
                    r'\.' + cls + r'\s*\{([^}]+)\}', css
                )
                if m:
                    block = m.group(1)
                    border = re.search(r'border-color:\s*(#[0-9a-fA-F]{3,8})', block)
                    bg = re.search(r'background-color:\s*(#[0-9a-fA-F]{3,8})', block)
                    icon = re.search(r'--callout-icon:\s*"([^"]+)"', block)
                    if border:
                        styles[cls]['border'] = border.group(1)
                    if bg:
                        styles[cls]['bg'] = bg.group(1)
                    if icon:
                        # Decode Unicode escapes like \U0001f4a1 or \2757
                        styles[cls]['icon'] = icon.group(1).encode().decode('unicode_escape')

    _callout_cache[content_root] = styles
    return styles

class BaseHandler(ABC):
    """
    Abstract base class for all synchronization handlers.
    """

    @abstractmethod
    def can_handle(self, file_path: str) -> bool:
        pass

    @abstractmethod
    def sync(self, file_path: str, course, module=None, canvas_obj=None, content_root=None):
        pass

    def add_to_module(self, module, item_dict, indent=0):
        """
        Helper to add or update an item in a module with indentation support.

        Args:
            module: The canvasapi.Module object.
            item_dict: Dictionary containing 'type', 'content_id' (or 'page_url'), 'title', and 'published'.
            indent: Integer (0-5) for indentation level.
        """
        title = item_dict.get('title')
        item_type = item_dict.get('type')
        content_id = item_dict.get('content_id')
        page_url = item_dict.get('page_url')
        external_url = item_dict.get('external_url')
        new_tab = item_dict.get('new_tab', False)
        published = item_dict.get('published') # Optional, might be None

        # Validate indent
        indent = max(0, min(5, int(indent)))

        items = module.get_module_items()

        existing_item = None
        for item in items:
            if item.type != item_type:
                continue

            # Match Logic
            match = False
            if item_type == 'Page' and item.page_url == page_url:
                match = True
            elif item_type == 'SubHeader' and item.title == title:
                match = True
            elif item_type in ['Assignment', 'Quiz', 'File']:
                try:
                    if int(item.content_id) == int(content_id):
                        match = True
                except (ValueError, TypeError):
                    if str(item.content_id) == str(content_id):
                        match = True
            elif item_type == 'ExternalUrl' and getattr(item, 'external_url', None) == external_url:
                match = True

            if match:
                existing_item = item
                break
        if existing_item:
            logger.debug("    Module item found: %s", title)
            updates = {}

            # Check Title
            if existing_item.title != title:
                logger.debug("      Updating title: %s -> %s", existing_item.title, title)
                updates['title'] = title

            # Check Indent
            if existing_item.indent != indent:
                logger.debug("      Updating indent: %s -> %s", existing_item.indent, indent)
                updates['indent'] = indent

            # Check Published (If provided)
            # Note: SubHeaders rely on this heavily.
            if published is not None and getattr(existing_item, 'published', None) != published:
                logger.debug("      Updating published: %s", published)
                updates['published'] = published

            if updates:
                existing_item.edit(module_item=updates)
            return existing_item
        else:
            logger.info("    [green]Adding to module:[/green] %s", module.name)
            payload = {
                'type': item_type,
                'title': title,
                'indent': indent
            }
            if content_id:
                payload['content_id'] = content_id
            if page_url:
                payload['page_url'] = page_url
            if external_url:
                payload['external_url'] = external_url
            if new_tab:
                payload['new_tab'] = True
            # Note: Canvas API ignores 'published' during create, so we don't include it here

            new_item = module.create_module_item(module_item=payload)

            # Canvas API ignores 'published' during creation, so we must update it separately
            if published is not None:
                logger.debug("      Setting published: %s", published)
                new_item.edit(module_item={'published': published})
            return new_item

    def _cleanup(self, qmd_path, html_path, files_dir):
        """Clean up temporary files from Quarto render."""
        if qmd_path:
            safe_delete_file(qmd_path)
        if html_path:
            safe_delete_file(html_path)
        if files_dir:
            safe_delete_dir(files_dir)

    def render_quarto_pdf(self, processed_content, base_path, filename):
        """
        Renders a processed QMD document to PDF via Quarto.
        Returns the path to the rendered PDF file, or None on failure.
        The caller is responsible for deleting the PDF after use.
        """
        temp_qmd = os.path.join(base_path, f"tmp-pdf-{filename}")
        temp_stem = os.path.splitext(f"tmp-pdf-{filename}")[0]
        temp_files_dir = os.path.join(base_path, f"{temp_stem}_files")
        temp_pdf = os.path.join(base_path, f"{temp_stem}.pdf")

        try:
            with open(temp_qmd, 'w', encoding='utf-8') as f:
                f.write(processed_content)

            cmd = ["quarto", "render", temp_qmd, "--to", "pdf"]
            subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

            if not os.path.exists(temp_pdf):
                logger.error("    Expected PDF output from Quarto render but file not found")
                self._cleanup(temp_qmd, None, temp_files_dir)
                return None

            # Clean up temp QMD and auxiliary files, but keep the PDF
            self._cleanup(temp_qmd, None, temp_files_dir)
            return temp_pdf

        except subprocess.CalledProcessError as e:
            stderr_text = e.stderr.decode('utf-8', errors='replace') if e.stderr else ''
            if 'no such file' in stderr_text.lower() and ('latex' in stderr_text.lower() or 'tinytex' in stderr_text.lower()):
                logger.error("    PDF render failed: LaTeX not found. Install with: quarto install tinytex")
            else:
                logger.error("    PDF render failed: %s", stderr_text or e)
            self._cleanup(temp_qmd, None, temp_files_dir)
            return None
        except Exception as e:
            logger.error("    PDF render failed: %s", e)
            self._cleanup(temp_qmd, None, temp_files_dir)
            return None

    def render_quarto_document(self, processed_content, base_path, filename, content_root=None):
        """
        Renders a processed QMD document to HTML via Quarto.
        Extracts the <main> content block and cleans up temp files.
        """
        temp_qmd = os.path.join(base_path, f"tmp-html-{filename}")
        temp_stem = os.path.splitext(f"tmp-html-{filename}")[0]
        temp_files_dir = os.path.join(base_path, f"{temp_stem}_files")
        temp_html = None

        try:
            with open(temp_qmd, 'w', encoding='utf-8') as f:
                f.write(processed_content)

            cmd = ["quarto", "render", temp_qmd, "--to", "html"]
            subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

            temp_html = temp_qmd.replace('.qmd', '.html')

            if not os.path.exists(temp_html):
                 logger.error("    Expected HTML output from Quarto render but file not found")
                 self._cleanup(temp_qmd, None, temp_files_dir)
                 return None

            with open(temp_html, 'r', encoding='utf-8') as f:
                full_html = f.read()

            # Extract Content
            main_match = re.search(r'<main[^>]*id="quarto-document-content"[^>]*>(.*?)</main>', full_html, re.DOTALL)

            if main_match:
                html_body = main_match.group(1)
                html_body = re.sub(r'<header[^>]*id="title-block-header"[^>]*>.*?</header>', '', html_body, flags=re.DOTALL)
            else:
                html_body = full_html
                html_body = re.sub(r'<header[^>]*id="title-block-header"[^>]*>.*?</header>', '', html_body, flags=re.DOTALL)

            # Inline styles for Canvas compatibility
            callout_styles = _load_callout_styles(content_root) if content_root else _DEFAULT_CALLOUT_STYLES
            html_body = self._inline_callout_styles(html_body, callout_styles)
            html_body = self._inline_figure_alignment(html_body)
            html_body = self._inline_syntax_highlighting(html_body)

            # Cleanup
            self._cleanup(temp_qmd, temp_html, temp_files_dir)
            return html_body

        except Exception as e:
            logger.error("    Quarto render failed: %s", e)
            self._cleanup(temp_qmd, None, temp_files_dir)
            return None

    @staticmethod
    def _inline_callout_styles(html, callout_styles):
        """Replace Quarto callout divs with inline-styled HTML for Canvas."""
        for cls, style in callout_styles.items():
            pattern = (
                r'<div class="callout callout-style-default ' + cls + r' callout-titled">\s*'
                r'<div class="callout-header d-flex align-content-center">\s*'
                r'<div class="callout-icon-container">\s*<i class="callout-icon"></i>\s*</div>\s*'
                r'<div class="callout-title-container flex-fill">\s*(.*?)\s*</div>\s*'
                r'</div>\s*'
                r'<div class="callout-body-container callout-body">\s*(.*?)\s*</div>\s*'
                r'</div>'
            )

            def make_replacement(match, s=style):
                title = match.group(1)
                body = match.group(2)
                # Quarto injects <span class="screen-reader-only">Note</span> before
                # the custom title for accessibility — strip it so it doesn't render
                # as visible text in Canvas where Quarto's CSS is absent.
                title = re.sub(r'<span[^>]*class="screen-reader-only"[^>]*>.*?</span>', '', title, flags=re.DOTALL).strip()
                return (
                    f'<div style="border-left: 4px solid {s["border"]}; '
                    f'background-color: {s["bg"]}; '
                    f'padding: 12px 16px; margin: 16px 0; border-radius: 4px;">'
                    f'<p style="margin: 0 0 8px 0; font-weight: bold;">'
                    f'{s["icon"]} {title}</p>'
                    f'{body}'
                    f'</div>'
                )

            html = re.sub(pattern, make_replacement, html, flags=re.DOTALL)

        return html

    @staticmethod
    def _inline_figure_alignment(html):
        """Inline text-align onto Quarto's fig-align figures so centering
        survives on Canvas where Quarto's stylesheet is absent.

        Canvas's HTML sanitizer is unpredictable about which wrapper
        elements/attributes survive, so we style every level — the wrapper
        div, the figure, the <p> around the img, and the figcaption."""
        for cls, align in (
            ('quarto-figure-center', 'center'),
            ('quarto-figure-left', 'left'),
            ('quarto-figure-right', 'right'),
        ):
            wrapper_pattern = (
                r'<div class="quarto-figure ' + re.escape(cls) + r'">\s*'
                r'<figure class="figure">(.*?)</figure>\s*'
                r'</div>'
            )

            def style_inner(inner, a=align):
                inner = re.sub(
                    r'<p>(\s*<img)',
                    f'<p style="text-align: {a};">\\1',
                    inner,
                )
                inner = inner.replace(
                    '<figcaption>',
                    f'<figcaption style="text-align: {a};">',
                )
                return inner

            html = re.sub(
                wrapper_pattern,
                lambda m, a=align: (
                    f'<div style="text-align: {a};">'
                    f'<figure class="figure" style="text-align: {a};">'
                    f'{style_inner(m.group(1), a)}'
                    f'</figure>'
                    f'</div>'
                ),
                html,
                flags=re.DOTALL,
            )
        return html

    @staticmethod
    def _inline_syntax_highlighting(html):
        """Inline Quarto syntax highlighting colors onto code spans for Canvas."""
        # Quarto default token colors (from quarto-syntax-highlighting.css)
        token_styles = {
            'ot': 'color:#003B4F',               # Other
            'at': 'color:#657422',               # Attribute
            'ss': 'color:#20794D',               # Special String
            'an': 'color:#5E5E5E',               # Annotation
            'fu': 'color:#4758AB',               # Function
            'st': 'color:#20794D',               # String
            'cf': 'color:#003B4F;font-weight:bold',  # Control Flow
            'op': 'color:#5E5E5E',               # Operator
            'er': 'color:#AD0000',               # Error
            'bn': 'color:#AD0000',               # Base N
            'al': 'color:#AD0000',               # Alert
            'va': 'color:#111111',               # Variable
            'pp': 'color:#AD0000',               # Preprocessor
            'in': 'color:#5E5E5E',               # Information
            'vs': 'color:#20794D',               # Verbatim String
            'wa': 'color:#5E5E5E;font-style:italic',  # Warning
            'do': 'color:#5E5E5E;font-style:italic',  # Documentation
            'im': 'color:#00769E',               # Import
            'ch': 'color:#20794D',               # Char
            'dt': 'color:#AD0000',               # Data Type
            'fl': 'color:#AD0000',               # Float
            'co': 'color:#5E5E5E',               # Comment
            'cv': 'color:#5E5E5E;font-style:italic',  # Comment Var
            'cn': 'color:#8f5902',               # Constant
            'sc': 'color:#5E5E5E',               # Special Char
            'dv': 'color:#AD0000',               # Decimal Value
            'kw': 'color:#003B4F;font-weight:bold',  # Keyword
        }

        # Inline style onto each <span class="xx">
        for token, style in token_styles.items():
            html = html.replace(
                f'<span class="{token}">',
                f'<span style="{style}">'
            )

        # Style the code block container: light grey background, padding, rounded
        html = re.sub(
            r'<div class="sourceCode"[^>]*>\s*<pre class="sourceCode[^"]*">',
            '<div><pre style="background-color:#f7f7f7; padding:12px 16px; '
            'border-radius:4px; overflow-x:auto; font-size:0.9em; '
            'color:#003B4F;">',
            html
        )

        # Remove the copy button Quarto adds
        html = re.sub(
            r'<button[^>]*class="code-copy-button"[^>]*>.*?</button>',
            '', html, flags=re.DOTALL
        )

        # Clean up line-number anchor links inside code spans
        html = re.sub(
            r'<a href="#cb\d+-\d+"[^>]*></a>',
            '', html
        )

        return html
