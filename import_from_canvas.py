"""
Import existing Canvas course content into CanvasQuartoSync QMD structure.

Connects to a Canvas course and exports all modules, pages, assignments,
quizzes, and files into the local NN_ModuleName/NN_ItemName.qmd directory
structure that CanvasQuartoSync uses for syncing.

Usage:
    python import_from_canvas.py <output_path> [options]

Examples:
    # Import using config.toml in the output directory
    python import_from_canvas.py ./MyCourse

    # Import with explicit course ID
    python import_from_canvas.py ./MyCourse --course-id 12345

    # Dry run (show what would be created without writing files)
    python import_from_canvas.py ./MyCourse --dry-run

    # Import only specific content types
    python import_from_canvas.py ./MyCourse --include pages,assignments
"""

import os
import re
import sys
import json
import argparse
import hashlib
import requests
from html import unescape
from urllib.parse import unquote, urlparse, urljoin

from canvasapi import Canvas

from handlers import __version__
from handlers.log import logger, setup_logging
from handlers.config import load_config, get_api_credentials, get_course_id


# ---------------------------------------------------------------------------
# Asset Downloader
# ---------------------------------------------------------------------------

# File extensions considered downloadable assets
IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.gif', '.svg', '.webp', '.bmp', '.ico'}
VIDEO_EXTENSIONS = {'.mp4', '.webm', '.ogv', '.mov', '.avi'}
DOCUMENT_EXTENSIONS = {'.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx', '.zip', '.tar', '.gz', '.7z', '.txt', '.csv'}
ALL_ASSET_EXTENSIONS = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS | DOCUMENT_EXTENSIONS


class AssetDownloader:
    """Downloads Canvas assets (images, videos, documents) to local assets/ directories.

    Each module gets its own assets/ folder:
        <module_dir>/assets/
    """

    def __init__(self, base_dir: str, api_token: str = '', api_url: str = '', dry_run: bool = False):
        self.base_dir = base_dir
        self.api_token = api_token
        self.api_url = api_url.rstrip('/') if api_url else ''
        self.dry_run = dry_run
        self._downloaded = {}  # (url_key, assets_dir) -> local_abs_path (dedup cache)
        self._file_meta_cache = {}  # file_id -> {'filename': ..., 'url': ...}
        self._warned_hosts = set()  # external hosts we already warned about

    def download(self, url: str, context_dir: str = '') -> str:
        """Download an asset and return the relative path from context_dir.

        Args:
            url: The full URL to download.
            context_dir: The module directory where the QMD lives.
                        Assets are saved to <context_dir>/assets/.

        Returns:
            Relative path to the downloaded file (e.g., 'assets/image.png'),
            or the original URL if download fails or is skipped.
        """
        if not url or not url.startswith(('http://', 'https://')):
            return url

        # Skip equation images (handled by LaTeX conversion)
        if 'equation_images/' in url:
            return url

        # Only download from Canvas (instructure.com). Warn about external hosts.
        if 'instructure.com' not in url and 'canvas' not in url.lower():
            parsed = urlparse(url)
            host = parsed.hostname or ''
            if host not in self._warned_hosts:
                self._warned_hosts.add(host)
                logger.warning(
                    "[yellow]External images found from %s — skipping download.[/yellow]\n"
                    "      Upload these to Canvas instead of using external hosting.",
                    host
                )
            return url

        # Determine the assets directory (per module)
        assets_dir = os.path.join(context_dir or self.base_dir, 'assets')

        # Check dedup cache
        cache_key = (url.split('?')[0], assets_dir)
        if cache_key in self._downloaded:
            abs_path = self._downloaded[cache_key]
            return os.path.relpath(abs_path, context_dir or self.base_dir).replace('\\', '/')

        if not self.dry_run:
            os.makedirs(assets_dir, exist_ok=True)

        # Determine filename — all assets go to assets/
        filename = self._extract_filename(url)

        # For Canvas files, use the API download URL (pre-authenticated, no verifier needed)
        download_url = url
        files_match = re.search(r'/files/(\d+)', url)
        if files_match:
            meta = self._resolve_canvas_file(files_match.group(1))
            if meta.get('url'):
                download_url = meta['url']

        target_dir = assets_dir

        # Ensure unique filename
        target_path = os.path.join(target_dir, filename)
        if os.path.exists(target_path):
            base, ext = os.path.splitext(filename)
            counter = 1
            while os.path.exists(target_path):
                target_path = os.path.join(target_dir, f"{base}_{counter}{ext}")
                counter += 1
            filename = os.path.basename(target_path)

        abs_local = os.path.join(target_dir, filename)
        rel_path = os.path.relpath(abs_local, context_dir or self.base_dir).replace('\\', '/')

        if self.dry_run:
            logger.info("      [dim]Would download:[/dim] %s -> %s", url[:80], rel_path)
            self._downloaded[cache_key] = abs_local
            return rel_path

        # Download from Canvas
        try:
            headers = {}
            if self.api_token:
                headers['Authorization'] = f'Bearer {self.api_token}'

            resp = requests.get(download_url, headers=headers, stream=True, timeout=60, allow_redirects=True)
            resp.raise_for_status()

            # Try to get better filename from Content-Disposition
            cd = resp.headers.get('Content-Disposition', '')
            if 'filename=' in cd:
                cd_filename = re.search(r'filename[*]?=["\']?([^"\';\n]+)', cd)
                if cd_filename:
                    new_name = sanitize_filename(cd_filename.group(1).strip())
                    if new_name and '.' in new_name:
                        abs_local = os.path.join(target_dir, new_name)

            with open(abs_local, 'wb') as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    f.write(chunk)

            rel_path = os.path.relpath(abs_local, context_dir or self.base_dir).replace('\\', '/')
            logger.info("      [green]Downloaded:[/green] %s", rel_path)
            self._downloaded[cache_key] = abs_local
            return rel_path

        except Exception as e:
            logger.warning("      [yellow]Download failed:[/yellow] %s (%s)", url[:80], e)
            return url

    def _resolve_canvas_file(self, file_id: str) -> dict:
        """Resolve a Canvas file ID to metadata (filename, download URL) via API."""
        if file_id in self._file_meta_cache:
            return self._file_meta_cache[file_id]

        if not self.api_token or not self.api_url:
            return {}

        try:
            resp = requests.get(
                f"{self.api_url}/api/v1/files/{file_id}",
                headers={'Authorization': f'Bearer {self.api_token}'},
                timeout=15
            )
            resp.raise_for_status()
            data = resp.json()
            meta = {
                'filename': data.get('filename', ''),
                'display_name': data.get('display_name', ''),
                'url': data.get('url', ''),
            }
            self._file_meta_cache[file_id] = meta
            return meta
        except Exception as e:
            logger.debug("      Could not resolve Canvas file %s: %s", file_id, e)
            self._file_meta_cache[file_id] = {}
            return {}

    def _extract_filename(self, url: str) -> str:
        """Extract a reasonable filename from a URL."""
        parsed = urlparse(url)
        path = parsed.path

        # Canvas file URLs: /courses/123/files/456/preview -> resolve via API
        files_match = re.search(r'/files/(\d+)', path)
        if files_match:
            file_id = files_match.group(1)
            meta = self._resolve_canvas_file(file_id)
            if meta.get('filename'):
                return sanitize_filename(meta['filename'])
            # Fallback
            ext = os.path.splitext(path.rstrip('/'))[1]
            if not ext or ext in ('.preview', '.download'):
                ext = ''
            return f"canvas_file_{file_id}{ext}"

        # Standard URL path
        filename = os.path.basename(path.rstrip('/'))
        if filename and '.' in filename:
            return sanitize_filename(unquote(filename))

        # Fallback: hash the URL
        url_hash = hashlib.md5(url.encode()).hexdigest()[:8]
        return f"asset_{url_hash}"



# ---------------------------------------------------------------------------
# HTML to Markdown converter
# ---------------------------------------------------------------------------

class HtmlToMarkdown:
    """Lightweight HTML-to-Markdown converter for Canvas content.

    Handles the common HTML patterns found in Canvas pages and assignments
    without requiring external dependencies.

    If an AssetDownloader is provided, images/videos/documents are downloaded
    and URLs are rewritten to local relative paths.
    """

    def __init__(self, asset_downloader: AssetDownloader = None, context_dir: str = '',
                 sync_map: dict = None, content_root: str = ''):
        self.downloader = asset_downloader
        self.context_dir = context_dir
        self._sync_map = sync_map or {}
        self._content_root = content_root

    def _download_asset(self, url: str) -> str:
        """Download an asset if downloader is available, else return URL as-is."""
        if self.downloader and url.startswith(('http://', 'https://')):
            return self.downloader.download(url, self.context_dir)
        return url

    def convert(self, html: str) -> str:
        if not html:
            return ''

        text = html

        # Normalize line endings
        text = text.replace('\r\n', '\n').replace('\r', '\n')

        # === Canvas equation handling (MUST come first) ===
        # Canvas equation <img> tags have x-canvaslms-safe-mathml attributes containing
        # MathML with literal > characters, breaking naive [^>]* patterns.
        # Pattern: (?:"[^"]*"|[^">])* correctly handles > inside quoted attribute values.
        _ATTR_PAT = r'(?:"[^"]*"|[^">])*'  # Matches attribute pairs allowing > in quoted values
        text = re.sub(
            rf'<img\s{_ATTR_PAT}src="[^"]*equation_images/([^"?]+)[^"]*"{_ATTR_PAT}>',
            lambda m: f'${self._equation_to_latex_raw(m.group(1))}$',
            text, flags=re.DOTALL | re.IGNORECASE
        )
        # Handle <script type="math/tex"> tags
        text = re.sub(
            r'<script[^>]*type="math/tex"[^>]*>(.*?)</script>',
            lambda m: f'${m.group(1).strip()}$',
            text, flags=re.DOTALL | re.IGNORECASE
        )
        # Remove any remaining MathJax/equation wrapper spans
        text = re.sub(r'<span[^>]*class="[^"]*MathJax[_\w]*"[^>]*>.*?</span>', '', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<span[^>]*class="[^"]*math_equation_latex[^"]*"[^>]*>.*?</span>', '', text, flags=re.DOTALL | re.IGNORECASE)

        # Remove <head>, <script>, <style> blocks
        text = re.sub(r'<head[^>]*>.*?</head>', '', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<script[^>]*>.*?</script>', '', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)

        # === Callout reconstruction ===
        # Canvas stores callouts as inline-styled divs produced by _inline_callout_styles().
        # Reconstruct them back to Quarto ::: {.callout-*} syntax.
        text = self._reconstruct_callouts(text)

        # Process block elements first

        # Headings
        for level in range(6, 0, -1):
            tag = f'h{level}'
            prefix = '#' * level
            text = re.sub(
                rf'<{tag}[^>]*>(.*?)</{tag}>',
                lambda m, p=prefix: f'\n\n{p} {self._inline(m.group(1)).strip()}\n\n',
                text, flags=re.DOTALL | re.IGNORECASE
            )

        # Paragraphs
        text = re.sub(r'<p[^>]*>(.*?)</p>', lambda m: f'\n\n{self._inline(m.group(1)).strip()}\n\n', text, flags=re.DOTALL | re.IGNORECASE)

        # Line breaks
        text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)

        # Horizontal rules
        text = re.sub(r'<hr\s*/?>', '\n\n---\n\n', text, flags=re.IGNORECASE)

        # Blockquotes
        text = re.sub(
            r'<blockquote[^>]*>(.*?)</blockquote>',
            lambda m: '\n\n' + '\n'.join('> ' + line for line in self._inline(m.group(1)).strip().split('\n')) + '\n\n',
            text, flags=re.DOTALL | re.IGNORECASE
        )

        # Pre/code blocks
        text = re.sub(
            r'<pre[^>]*><code[^>]*(?:class="([^"]*)")?[^>]*>(.*?)</code></pre>',
            lambda m: self._code_block(m.group(2), m.group(1)),
            text, flags=re.DOTALL | re.IGNORECASE
        )
        text = re.sub(
            r'<pre[^>]*>(.*?)</pre>',
            lambda m: f'\n\n```\n{unescape(self._strip_tags(m.group(1)))}\n```\n\n',
            text, flags=re.DOTALL | re.IGNORECASE
        )

        # Tables
        text = self._convert_tables(text)

        # Lists
        text = self._convert_lists(text)

        # Remaining equation images (already handled above, but just in case)
        _ATTR_PAT2 = r'(?:"[^"]*"|[^">])*'
        text = re.sub(
            rf'<img\s{_ATTR_PAT2}src="[^"]*equation_images/([^"?]+)[^"]*"{_ATTR_PAT2}>',
            lambda m: self._equation_to_latex(m.group(1)),
            text, flags=re.DOTALL | re.IGNORECASE
        )

        # Images  (before links, since images use ![])
        text = re.sub(
            r'<img[^>]*src="([^"]*)"[^>]*(?:alt="([^"]*)")?[^>]*/?>',
            lambda m: f'![{m.group(2) or ""}]({self._download_asset(m.group(1))})',
            text, flags=re.IGNORECASE
        )

        # Video tags -> download and link
        text = re.sub(
            r'<video[^>]*>.*?<source[^>]*src="([^"]*)"[^>]*/?>.*?</video>',
            lambda m: f'![video]({self._download_asset(m.group(1))})',
            text, flags=re.DOTALL | re.IGNORECASE
        )

        # Canvas iframe media embeds -> download link
        text = re.sub(
            r'<iframe[^>]*src="([^"]*(?:media_objects|media_attachments)[^"]*)"[^>]*>.*?</iframe>',
            lambda m: f'[Embedded media]({m.group(1)})',
            text, flags=re.DOTALL | re.IGNORECASE
        )

        # External iframes (YouTube, Falstad, Wokwi, Tinkercad, etc.) -> preserve as raw HTML
        # Use placeholders to protect from _strip_tags later
        self._iframe_placeholders = {}
        def _protect_iframe(m):
            key = f'\x00IFRAME_{len(self._iframe_placeholders)}\x00'
            self._iframe_placeholders[key] = m.group(0)
            return f'\n\n{key}\n\n'
        text = re.sub(
            r'<iframe[^>]*src="([^"]*)"[^>]*>.*?</iframe>',
            _protect_iframe,
            text, flags=re.DOTALL | re.IGNORECASE
        )

        # Links — download linked files (pdf, docx, etc.)
        text = re.sub(
            r'<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>',
            lambda m: self._process_link(m.group(1), m.group(2)),
            text, flags=re.DOTALL | re.IGNORECASE
        )

        # Inline formatting
        text = re.sub(r'<strong[^>]*>(.*?)</strong>', r'**\1**', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<b[^>]*>(.*?)</b>', r'**\1**', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<em[^>]*>(.*?)</em>', r'*\1*', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<i[^>]*>(.*?)</i>', r'*\1*', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<code[^>]*>(.*?)</code>', r'`\1`', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<sup[^>]*>(.*?)</sup>', r'^{\1}', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<sub[^>]*>(.*?)</sub>', r'~{\1}', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<del[^>]*>(.*?)</del>', r'~~\1~~', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<s[^>]*>(.*?)</s>', r'~~\1~~', text, flags=re.DOTALL | re.IGNORECASE)

        # Strip remaining HTML tags
        text = self._strip_tags(text)

        # Decode HTML entities
        text = unescape(text)

        # Restore protected iframes
        for key, iframe_html in getattr(self, '_iframe_placeholders', {}).items():
            text = text.replace(key, iframe_html)

        # Clean up whitespace
        text = re.sub(r'\n{3,}', '\n\n', text)
        text = re.sub(r'[ \t]+\n', '\n', text)
        text = text.strip()

        return text

    def _inline(self, html: str) -> str:
        """Process inline HTML without adding block-level spacing."""
        if not html:
            return ''
        text = html
        # Canvas equation images (handle > inside quoted attributes)
        _AP = r'(?:"[^"]*"|[^">])*'
        text = re.sub(
            rf'<img\s{_AP}src="[^"]*equation_images/([^"?]+)[^"]*"{_AP}>',
            lambda m: f'${self._equation_to_latex_raw(m.group(1))}$',
            text, flags=re.DOTALL | re.IGNORECASE
        )
        text = re.sub(r'<script[^>]*type="math/tex"[^>]*>(.*?)</script>',
                       lambda m: f'${m.group(1).strip()}$', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<span[^>]*class="[^"]*MathJax[_\w]*"[^>]*>.*?</span>', '', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<span[^>]*class="[^"]*math_equation_latex[^"]*"[^>]*>.*?</span>', '', text, flags=re.DOTALL | re.IGNORECASE)

        text = re.sub(r'<strong[^>]*>(.*?)</strong>', r'**\1**', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<b[^>]*>(.*?)</b>', r'**\1**', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<em[^>]*>(.*?)</em>', r'*\1*', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<i[^>]*>(.*?)</i>', r'*\1*', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<code[^>]*>(.*?)</code>', r'`\1`', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)
        # Canvas equation images -> LaTeX (any remaining)
        text = re.sub(
            rf'<img\s{_AP}src="[^"]*equation_images/([^"?]+)[^"]*"{_AP}>',
            lambda m: self._equation_to_latex(m.group(1)),
            text, flags=re.DOTALL | re.IGNORECASE
        )
        # Links with download
        text = re.sub(
            r'<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>',
            lambda m: self._process_link(m.group(1), m.group(2)),
            text, flags=re.DOTALL | re.IGNORECASE
        )
        # Images with download
        text = re.sub(
            r'<img[^>]*src="([^"]*)"[^>]*(?:alt="([^"]*)")?[^>]*/?>',
            lambda m: f'![{m.group(2) or ""}]({self._download_asset(m.group(1))})',
            text, flags=re.IGNORECASE
        )
        return text

    def _process_link(self, url: str, inner_html: str) -> str:
        """Process an <a> tag: download linked files, reverse-resolve Canvas links, or convert to markdown link."""
        link_text = self._inline(inner_html).strip()
        if not link_text:
            link_text = self._strip_tags(inner_html).strip() or 'link'

        # Check if it's a downloadable file
        parsed = urlparse(url)
        path = parsed.path.rstrip('/')
        ext = os.path.splitext(path)[1].lower()

        # Canvas file links: /courses/X/files/Y (with or without verifier, preview, download, wrap)
        is_canvas_file = bool(re.search(r'/(?:courses|files)/\d+/files/\d+', url))
        # Direct file URLs with known extensions
        is_downloadable = ext in ALL_ASSET_EXTENSIONS

        if (is_canvas_file or is_downloadable) and self.downloader:
            local_path = self._download_asset(url)
            if local_path != url:  # Download succeeded
                return f'[{link_text}]({local_path})'

        # Try to reverse-resolve Canvas content URLs back to .qmd paths
        resolved = self._reverse_resolve_link(url)
        if resolved != url:
            return f'[{link_text}]({resolved})'

        return f'[{link_text}]({url})'

    def _equation_to_latex_raw(self, encoded_latex: str) -> str:
        """Decode a Canvas equation_images URL path to raw LaTeX string.

        Canvas encodes LaTeX in equation image URLs with double URL-encoding,
        e.g. %255C -> %5C -> backslash.
        """
        return unquote(unquote(encoded_latex))

    def _equation_to_latex(self, encoded_latex: str) -> str:
        """Convert a Canvas equation_images URL path to $LaTeX$ notation."""
        return f'${self._equation_to_latex_raw(encoded_latex)}$'

    def _strip_tags(self, html: str) -> str:
        return re.sub(r'<[^>]+>', '', html)

    def _code_block(self, code: str, lang_class: str) -> str:
        lang = ''
        if lang_class:
            m = re.search(r'language-(\w+)', lang_class)
            if m:
                lang = m.group(1)
        code = unescape(self._strip_tags(code))
        return f'\n\n```{lang}\n{code}\n```\n\n'

    def _convert_tables(self, html: str) -> str:
        """Convert HTML tables to markdown pipe tables."""
        def table_replacer(match):
            table_html = match.group(0)

            # Extract rows
            rows = re.findall(r'<tr[^>]*>(.*?)</tr>', table_html, re.DOTALL | re.IGNORECASE)
            if not rows:
                return table_html

            md_rows = []
            for row_html in rows:
                cells = re.findall(r'<t[hd][^>]*>(.*?)</t[hd]>', row_html, re.DOTALL | re.IGNORECASE)
                md_cells = [self._inline(c).strip().replace('|', '\\|') for c in cells]
                md_rows.append(md_cells)

            if not md_rows:
                return table_html

            # Build markdown table
            lines = []
            # Header row
            lines.append('| ' + ' | '.join(md_rows[0]) + ' |')
            # Separator
            lines.append('|' + '|'.join(':---' for _ in md_rows[0]) + '|')
            # Data rows
            for row in md_rows[1:]:
                # Pad if needed
                while len(row) < len(md_rows[0]):
                    row.append('')
                lines.append('| ' + ' | '.join(row[:len(md_rows[0])]) + ' |')

            return '\n\n' + '\n'.join(lines) + '\n\n'

        return re.sub(r'<table[^>]*>.*?</table>', table_replacer, html, flags=re.DOTALL | re.IGNORECASE)

    def _convert_lists(self, html: str) -> str:
        """Convert HTML lists to markdown lists."""
        def list_replacer(match):
            tag = match.group(1).lower()
            content = match.group(2)
            items = re.findall(r'<li[^>]*>(.*?)</li>', content, re.DOTALL | re.IGNORECASE)

            lines = []
            for i, item in enumerate(items):
                item_text = self._inline(item).strip()
                # Handle nested content
                item_text = self._strip_tags(item_text).strip()
                if tag == 'ol':
                    lines.append(f'{i + 1}. {item_text}')
                else:
                    lines.append(f'- {item_text}')

            return '\n\n' + '\n'.join(lines) + '\n\n'

        # Process nested lists (inner first)
        for _ in range(5):  # Max nesting depth
            prev = html
            html = re.sub(r'<(ol|ul)[^>]*>(.*?)</\1>', list_replacer, html, flags=re.DOTALL | re.IGNORECASE)
            if html == prev:
                break

        return html

    # --- Callout reconstruction ---

    # Map border colors (from _inline_callout_styles) back to callout types.
    # Covers defaults and common branding overrides.
    _BORDER_TO_CALLOUT = {
        '#198754': 'tip',
        '#dc3545': 'important',
        '#ffc107': 'warning',
        '#0d6efd': 'note',
        '#fd7e14': 'caution',
    }

    # Icons used by _inline_callout_styles (strip these from the title)
    _CALLOUT_ICONS = {
        '\U0001f4a1', '\u2757', '\u26a0\ufe0f', '\U0001f4dd', '\U0001f536',
        '💡', '❗', '⚠️', '📝', '🔶',
    }

    def _reconstruct_callouts(self, html: str) -> str:
        """Detect inline-styled callout divs and reconstruct ::: {.callout-*} syntax."""
        pattern = (
            r'<div\s+style="[^"]*border-left:\s*4px\s+solid\s+(#[0-9a-fA-F]{3,8})[^"]*">'
            r'\s*<p\s+style="[^"]*font-weight:\s*bold[^"]*">\s*(.*?)\s*</p>'
            r'\s*(.*?)\s*</div>'
        )

        def _callout_replacer(m):
            border_color = m.group(1).lower()
            raw_title = m.group(2).strip()
            body_html = m.group(3).strip()

            callout_type = self._BORDER_TO_CALLOUT.get(border_color, 'note')

            # Strip the emoji icon from the title
            title = raw_title
            for icon in self._CALLOUT_ICONS:
                title = title.replace(icon, '').strip()

            body_md = self.convert(body_html) if body_html else ''

            lines = [f'\n\n::: {{.callout-{callout_type}}}']
            if title:
                lines.append(f'## {title}')
            if body_md:
                lines.append(body_md)
            lines.append(':::\n\n')
            return '\n'.join(lines)

        return re.sub(pattern, _callout_replacer, html, flags=re.DOTALL | re.IGNORECASE)

    # --- Cross-link reverse resolution ---

    def _reverse_resolve_link(self, url: str) -> str:
        """Try to reverse-resolve a Canvas URL back to a relative .qmd path using the sync map."""
        if not self._sync_map or not self._content_root:
            return url

        # Extract Canvas object ID from common URL patterns:
        #   /courses/123/pages/page-slug
        #   /courses/123/assignments/456
        #   /courses/123/quizzes/456
        page_match = re.search(r'/courses/\d+/pages/([^/?#]+)', url)
        id_match = re.search(r'/courses/\d+/(?:assignments|quizzes)/(\d+)', url)

        if page_match:
            slug = page_match.group(1)
            # Search sync map for matching page slug
            for rel_path, entry in self._sync_map.items():
                if isinstance(entry, dict):
                    canvas_id = entry.get('id')
                    if isinstance(canvas_id, str) and canvas_id == slug:
                        return rel_path
        elif id_match:
            target_id = int(id_match.group(1))
            for rel_path, entry in self._sync_map.items():
                if isinstance(entry, dict):
                    canvas_id = entry.get('id')
                    if canvas_id == target_id:
                        return rel_path

        return url


# ---------------------------------------------------------------------------
# Canvas content fetchers
# ---------------------------------------------------------------------------

def sanitize_filename(name: str) -> str:
    """Make a string safe for use as a filename."""
    # Replace problematic characters
    name = re.sub(r'[<>:"/\\|?*]', '_', name)
    # Replace spaces with underscores
    name = name.replace(' ', '_')
    # Collapse multiple underscores
    name = re.sub(r'_+', '_', name)
    # Remove leading/trailing underscores
    name = name.strip('_')
    return name


def fetch_page_body(course, page_url: str) -> str:
    """Fetch the full body of a Canvas page."""
    try:
        page = course.get_page(page_url)
        return getattr(page, 'body', '') or ''
    except Exception as e:
        logger.error("    Failed to fetch page %s: %s", page_url, e)
        return ''


def fetch_assignment(course, assignment_id: int):
    """Fetch a Canvas assignment object."""
    try:
        return course.get_assignment(assignment_id)
    except Exception as e:
        logger.error("    Failed to fetch assignment %s: %s", assignment_id, e)
        return None


def fetch_quiz(course, quiz_id: int):
    """Fetch a Canvas quiz object."""
    try:
        return course.get_quiz(quiz_id)
    except Exception as e:
        logger.error("    Failed to fetch quiz %s: %s", quiz_id, e)
        return None


def fetch_quiz_questions(quiz):
    """Fetch all questions for a quiz."""
    try:
        return list(quiz.get_questions())
    except Exception as e:
        logger.error("    Failed to fetch quiz questions: %s", e)
        return []


def download_file(file_obj, target_path: str) -> bool:
    """Download a Canvas file to local path."""
    try:
        url = file_obj.url
        resp = requests.get(url, stream=True, timeout=60)
        resp.raise_for_status()
        os.makedirs(os.path.dirname(target_path), exist_ok=True)
        with open(target_path, 'wb') as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)
        return True
    except Exception as e:
        logger.error("    Failed to download file: %s", e)
        return False


# ---------------------------------------------------------------------------
# QMD generators
# ---------------------------------------------------------------------------

def generate_page_qmd(title: str, body_md: str, published: bool = False) -> str:
    """Generate a QMD file for a Canvas page."""
    # Escape quotes in title for YAML
    safe_title = title.replace('"', '\\"')
    return f'''---
title: "{safe_title}"
format:
  html:
    page-layout: article
canvas:
  type: page
  published: {str(published).lower()}
---

{body_md}
'''


def generate_assignment_qmd(title: str, body_md: str, assignment) -> str:
    """Generate a QMD file for a Canvas assignment."""
    safe_title = title.replace('"', '\\"')

    # Extract assignment properties
    published = getattr(assignment, 'published', False)
    points = getattr(assignment, 'points_possible', 0) or 0
    due_at = getattr(assignment, 'due_at', None) or ''
    unlock_at = getattr(assignment, 'unlock_at', None) or ''
    lock_at = getattr(assignment, 'lock_at', None) or ''
    submission_types = getattr(assignment, 'submission_types', ['online_upload'])
    allowed_extensions = getattr(assignment, 'allowed_extensions', [])
    grading_type = getattr(assignment, 'grading_type', '') or ''
    omit_from_final_grade = getattr(assignment, 'omit_from_final_grade', False)

    # Build canvas section
    canvas_lines = [
        f'  type: assignment',
        f'  published: {str(published).lower()}',
        f'  points: {points}',
    ]

    if submission_types:
        canvas_lines.append(f'  submission_types: [{", ".join(submission_types)}]')
    if allowed_extensions:
        canvas_lines.append(f'  allowed_extensions: [{", ".join(allowed_extensions)}]')
    if due_at:
        canvas_lines.append(f'  due_at: {due_at}')
    if unlock_at:
        canvas_lines.append(f'  unlock_at: {unlock_at}')
    if lock_at:
        canvas_lines.append(f'  lock_at: {lock_at}')
    if grading_type:
        canvas_lines.append(f'  grading_type: {grading_type}')
    if omit_from_final_grade:
        canvas_lines.append(f'  omit_from_final_grade: true')

    canvas_section = '\n'.join(canvas_lines)

    return f'''---
title: "{safe_title}"
format:
  html:
    page-layout: article
canvas:
{canvas_section}
---

{body_md}
'''


def generate_quiz_qmd(title: str, quiz, questions: list) -> str:
    """Generate a QMD file for a Canvas quiz (classic)."""
    safe_title = title.replace('"', '\\"')

    published = getattr(quiz, 'published', False)
    quiz_type = getattr(quiz, 'quiz_type', 'practice_quiz')
    time_limit = getattr(quiz, 'time_limit', None)
    allowed_attempts = getattr(quiz, 'allowed_attempts', -1)
    description = getattr(quiz, 'description', '') or ''

    canvas_lines = [
        f'  type: quiz',
        f'  published: {str(published).lower()}',
        f'  quiz_type: {quiz_type}',
    ]
    if time_limit:
        canvas_lines.append(f'  time_limit: {time_limit}')
    if allowed_attempts and allowed_attempts != -1:
        canvas_lines.append(f'  allowed_attempts: {allowed_attempts}')

    canvas_section = '\n'.join(canvas_lines)

    # Build question blocks
    converter = HtmlToMarkdown()
    q_blocks = []
    for q in questions:
        q_name = getattr(q, 'question_name', 'Question')
        q_text = getattr(q, 'question_text', '')
        q_type = getattr(q, 'question_type', 'multiple_choice_question')
        q_points = getattr(q, 'points_possible', 1)
        answers = getattr(q, 'answers', [])

        q_text_md = converter.convert(q_text) if q_text else ''

        # Build answer lines
        answer_lines = []
        for ans in answers:
            ans_text = ans.get('text', '') or ans.get('html', '') or ''
            if ans.get('html'):
                ans_text = converter.convert(ans['html'])
            weight = ans.get('weight', 0)
            if weight > 0:
                answer_lines.append(f'  - [x] {ans_text}')
            else:
                answer_lines.append(f'  - [ ] {ans_text}')

        answers_block = '\n'.join(answer_lines) if answer_lines else ''

        q_block = f''':::: {{.question name="{q_name}" type="{q_type}" points="{q_points}"}}
{q_text_md}

{answers_block}
::::'''
        q_blocks.append(q_block)

    questions_section = '\n\n'.join(q_blocks)

    desc_md = ''
    if description:
        desc_md = converter.convert(description)

    return f'''---
title: "{safe_title}"
canvas:
{canvas_section}
---

{desc_md}

{questions_section}
'''


def generate_external_link_qmd(title: str, url: str, published: bool = False, new_tab: bool = False) -> str:
    """Generate a QMD file for an external URL module item."""
    safe_title = title.replace('"', '\\"')
    return f'''---
title: "{safe_title}"
canvas:
  type: external_url
  url: "{url}"
  published: {str(published).lower()}
  new_tab: {str(new_tab).lower()}
---
'''


def generate_subheader_qmd(title: str, published: bool = True, indent: int = 0) -> str:
    """Generate a QMD file for a text sub-header module item."""
    safe_title = title.replace('"', '\\"')
    return f'''---
title: "{safe_title}"
canvas:
  type: subheader
  published: {str(published).lower()}
  indent: {indent}
---
'''


# ---------------------------------------------------------------------------
# Main import logic
# ---------------------------------------------------------------------------

def import_course(course, output_path: str, dry_run: bool = False, include_types: set = None, api_token: str = '', api_url: str = ''):
    """Import all modules and their items from a Canvas course."""

    # Create asset downloader (shared across modules for dedup)
    downloader = AssetDownloader(output_path, api_token=api_token, api_url=api_url, dry_run=dry_run)

    logger.info("[bold cyan]Fetching modules...[/bold cyan]")
    modules = list(course.get_modules())
    logger.info("Found [bold]%d[/bold] modules", len(modules))

    if not modules:
        logger.warning("No modules found in this course. Importing unmodularized content...")
        converter = HtmlToMarkdown(asset_downloader=downloader, context_dir=output_path)
        _import_standalone_pages(course, output_path, converter, dry_run, include_types)
        return

    for mod_idx, module in enumerate(modules, start=1):
        mod_name = module.name
        mod_prefix = f"{mod_idx:02d}"
        safe_mod_name = sanitize_filename(mod_name)
        mod_dir_name = f"{mod_prefix}_{safe_mod_name}"
        mod_dir = os.path.join(output_path, mod_dir_name)

        logger.info("[cyan]Module %d:[/cyan] [bold]%s[/bold] -> %s/", mod_idx, mod_name, mod_dir_name)

        if not dry_run:
            os.makedirs(mod_dir, exist_ok=True)

        # Create a converter for this module (assets go to mod_dir/assets/)
        converter = HtmlToMarkdown(asset_downloader=downloader, context_dir=mod_dir)

        # Fetch module items
        try:
            items = list(module.get_module_items())
        except Exception as e:
            logger.error("  Failed to fetch items for module %s: %s", mod_name, e)
            continue

        for item_idx, item in enumerate(items, start=1):
            item_prefix = f"{item_idx:02d}"
            item_type = item.type
            item_title = getattr(item, 'title', 'Untitled')
            safe_item_name = sanitize_filename(item_title)
            indent = getattr(item, 'indent', 0)
            published = getattr(item, 'published', False)

            logger.info("  [dim]%02d[/dim] [%s] %s", item_idx, item_type, item_title)

            try:
                if item_type == 'Page':
                    if include_types and 'pages' not in include_types:
                        continue
                    page_url = item.page_url
                    body_html = fetch_page_body(course, page_url)
                    body_md = converter.convert(body_html)
                    content = generate_page_qmd(item_title, body_md, published)
                    _write_qmd(mod_dir, item_prefix, safe_item_name, content, dry_run)

                elif item_type == 'Assignment':
                    if include_types and 'assignments' not in include_types:
                        continue
                    assignment = fetch_assignment(course, item.content_id)
                    if assignment:
                        desc_html = getattr(assignment, 'description', '') or ''
                        body_md = converter.convert(desc_html)
                        content = generate_assignment_qmd(item_title, body_md, assignment)
                        _write_qmd(mod_dir, item_prefix, safe_item_name, content, dry_run)

                elif item_type == 'Quiz':
                    if include_types and 'quizzes' not in include_types:
                        continue
                    quiz = fetch_quiz(course, item.content_id)
                    if quiz:
                        questions = fetch_quiz_questions(quiz)
                        content = generate_quiz_qmd(item_title, quiz, questions)
                        _write_qmd(mod_dir, item_prefix, safe_item_name, content, dry_run)

                elif item_type == 'File':
                    if include_types and 'files' not in include_types:
                        continue
                    _import_file_item(course, item, mod_dir, item_prefix, safe_item_name, dry_run)

                elif item_type == 'ExternalUrl':
                    if include_types and 'links' not in include_types:
                        continue
                    url = getattr(item, 'external_url', '')
                    new_tab = getattr(item, 'new_tab', False)
                    content = generate_external_link_qmd(item_title, url, published, new_tab)
                    _write_qmd(mod_dir, item_prefix, safe_item_name, content, dry_run)

                elif item_type == 'SubHeader':
                    content = generate_subheader_qmd(item_title, published, indent)
                    _write_qmd(mod_dir, item_prefix, safe_item_name, content, dry_run, ext='.md')

                elif item_type == 'ExternalTool':
                    # External tools (LTI) - save as external link with a note
                    url = getattr(item, 'external_url', '') or getattr(item, 'url', '') or ''
                    if url:
                        content = generate_external_link_qmd(item_title, url, published, new_tab=True)
                        _write_qmd(mod_dir, item_prefix, safe_item_name, content, dry_run)
                    else:
                        logger.warning("    Skipping ExternalTool (no URL): %s", item_title)

                else:
                    logger.warning("    Skipping unsupported item type: %s", item_type)

            except Exception as e:
                logger.error("  Failed to import item '%s': %s", item_title, e)

    # Also import pages that are NOT in any module
    standalone_converter = HtmlToMarkdown(asset_downloader=downloader, context_dir=output_path)
    _import_standalone_pages(course, output_path, standalone_converter, dry_run, include_types)

    logger.info("[bold green]Import complete.[/bold green]")


def _write_qmd(directory: str, prefix: str, name: str, content: str, dry_run: bool, ext: str = '.qmd'):
    """Write a QMD/MD file to disk."""
    filename = f"{prefix}_{name}{ext}"
    filepath = os.path.join(directory, filename)

    if dry_run:
        logger.info("    [dim]Would create:[/dim] %s", filepath)
        return

    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)
    logger.info("    [green]Created:[/green] %s", os.path.basename(filepath))


def _import_file_item(course, item, mod_dir: str, prefix: str, name: str, dry_run: bool):
    """Download a file module item."""
    try:
        content_id = item.content_id
        file_obj = course.get_file(content_id)
        original_name = getattr(file_obj, 'filename', name)
        ext = os.path.splitext(original_name)[1]
        target_name = f"{prefix}_{sanitize_filename(os.path.splitext(original_name)[0])}{ext}"
        target_path = os.path.join(mod_dir, target_name)

        if dry_run:
            logger.info("    [dim]Would download:[/dim] %s", target_path)
            return

        if download_file(file_obj, target_path):
            logger.info("    [green]Downloaded:[/green] %s", target_name)
    except Exception as e:
        logger.error("    Failed to download file item: %s", e)


def _import_standalone_pages(course, output_path: str, converter, dry_run: bool, include_types: set):
    """Import pages that are not part of any module."""
    if include_types and 'pages' not in include_types:
        return

    logger.info("[dim]Checking for standalone pages...[/dim]")
    try:
        pages = list(course.get_pages())
        # Filter: only pages not already imported (we track by checking existing files)
        # For simplicity, save them as root-level files with 99_ prefix
        standalone_count = 0
        for page in pages:
            page_url = page.url
            title = page.title

            # Check if we already created this page inside a module directory
            safe_name = sanitize_filename(title)
            already_exists = False
            for root, dirs, files in os.walk(output_path):
                for f in files:
                    if safe_name in f and f.endswith('.qmd'):
                        already_exists = True
                        break
                if already_exists:
                    break

            if already_exists:
                continue

            standalone_count += 1
            body_html = fetch_page_body(course, page_url)
            body_md = converter.convert(body_html)
            published = getattr(page, 'published', False)
            content = generate_page_qmd(title, body_md, published)

            filename = f"99_{safe_name}.qmd"
            filepath = os.path.join(output_path, filename)

            if dry_run:
                logger.info("  [dim]Would create standalone:[/dim] %s", filename)
            else:
                with open(filepath, 'w', encoding='utf-8') as f:
                    f.write(content)
                logger.info("  [green]Created standalone:[/green] %s", filename)

        if standalone_count == 0:
            logger.info("  No standalone pages found.")
    except Exception as e:
        logger.error("  Failed to fetch standalone pages: %s", e)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Import existing Canvas course content into CanvasQuartoSync QMD structure."
    )
    parser.add_argument("--version", action="version", version=f"CanvasQuartoSync {__version__}")
    parser.add_argument(
        "output_path",
        help="Path to the output content directory (must contain config.toml, or use --course-id)."
    )
    parser.add_argument("--course-id", help="Canvas Course ID (overrides config.toml).")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would be created without writing any files."
    )
    parser.add_argument(
        "--include",
        help="Comma-separated list of content types to import: pages,assignments,quizzes,files,links/external_urls (default: all)."
    )

    verbosity = parser.add_mutually_exclusive_group()
    verbosity.add_argument("--verbose", "-v", action="store_true", help="Show detailed debug output.")
    verbosity.add_argument("--quiet", "-q", action="store_true", help="Only show errors.")
    parser.add_argument("--log-file", help="Write full debug log to a file.")

    args = parser.parse_args()

    setup_logging(verbose=args.verbose, quiet=args.quiet, log_file=args.log_file)

    output_path = os.path.abspath(args.output_path)
    if not os.path.exists(output_path):
        logger.info("Creating output directory: %s", output_path)
        os.makedirs(output_path, exist_ok=True)

    # Resolve credentials
    API_URL, API_TOKEN = get_api_credentials(output_path)
    course_id = get_course_id(output_path, args.course_id)

    if not API_URL or not API_TOKEN:
        logger.error("[red]Canvas credentials not found.[/red] Set CANVAS_API_URL / CANVAS_API_TOKEN env vars, or provide canvas_api_url / canvas_token_path in config.toml.")
        return

    if not course_id:
        logger.error("[red]Course ID not specified.[/red] Provide it via --course-id, config.toml, or a 'course_id.txt' file.")
        return

    # Parse include filter
    include_types = None
    if args.include:
        include_types = set(t.strip().lower() for t in args.include.split(','))
        # Accept 'external_urls' as alias for 'links'
        if 'external_urls' in include_types:
            include_types.discard('external_urls')
            include_types.add('links')

    # Connect
    logger.info("[cyan]Connecting to Canvas...[/cyan]")
    try:
        canvas = Canvas(API_URL, API_TOKEN)
        course = canvas.get_course(course_id)
        logger.info("[green]Connected to course:[/green] [bold]%s[/bold] (ID: %s)", course.name, course.id)
    except Exception as e:
        logger.error("[red]Connection failed:[/red] %s", e)
        return

    if args.dry_run:
        logger.info("[yellow]Dry run mode — no files will be written.[/yellow]")

    import_course(course, output_path, dry_run=args.dry_run, include_types=include_types, api_token=API_TOKEN, api_url=API_URL)


if __name__ == "__main__":
    main()
