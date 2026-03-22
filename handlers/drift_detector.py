"""
Drift detection for CanvasQuartoSync.

Detects when Canvas content has been modified outside of the sync tool
(e.g., via the Canvas web UI) since the last sync. This prevents
accidental overwrites of manual edits.

Two storage layers:
  1. A fast content hash in .canvas_sync_map.json (detects *that* something changed)
  2. A full HTML snapshot in .canvas_snapshots/ (shows *what* changed via diff)

Usage (standalone):
    python -m handlers.drift_detector <content_path>

Usage (from sync_to_canvas.py):
    from handlers.drift_detector import check_drift, store_canvas_hash
"""

import difflib
import hashlib
import json
import os
import re
from html import unescape

from handlers.content_utils import load_sync_map, save_sync_map
from handlers.log import logger


# ---------------------------------------------------------------------------
# Snapshot directory (stores the HTML we last pushed, for diffing)
# ---------------------------------------------------------------------------

SNAPSHOT_DIR = '.canvas_snapshots'


def _snapshot_dir(content_root: str) -> str:
    d = os.path.join(content_root, SNAPSHOT_DIR)
    os.makedirs(d, exist_ok=True)
    return d


def _snapshot_path(content_root: str, file_path: str) -> str:
    """Return the path where we store the last-synced HTML for a given file."""
    rel = os.path.relpath(file_path, content_root).replace('\\', '/')
    safe = rel.replace('/', '__').replace(' ', '_')
    return os.path.join(_snapshot_dir(content_root), safe + '.html')


# ---------------------------------------------------------------------------
# Normalization & hashing
# ---------------------------------------------------------------------------

def _normalize_html(html: str) -> str:
    """Normalize HTML for comparison.

    Strips whitespace variations and Canvas-injected attributes that change
    without meaningful content edits (e.g., data-api-endpoint, class attrs).
    """
    if not html:
        return ''
    # Remove data-* attributes
    text = re.sub(r'\s+data-[\w-]+="[^"]*"', '', html)
    # Remove class attributes (Canvas adds them dynamically)
    text = re.sub(r'\s+class="[^"]*"', '', text)
    # Remove style attributes
    text = re.sub(r'\s+style="[^"]*"', '', text)
    # Normalize whitespace
    text = re.sub(r'\s+', ' ', text)
    text = text.strip()
    return text


def _html_to_text(html: str) -> str:
    """Very simple HTML-to-text for readable diffs (not full conversion)."""
    if not html:
        return ''
    text = html
    # Block elements get newlines
    text = re.sub(r'<(?:p|div|h[1-6]|li|tr|br)[^>]*/?>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'</(?:p|div|h[1-6]|li|tr|table|ul|ol)>', '\n', text, flags=re.IGNORECASE)
    # Strip all remaining tags
    text = re.sub(r'<[^>]+>', '', text)
    text = unescape(text)
    # Clean up whitespace
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def compute_content_hash(html: str) -> str:
    """Compute a stable hash of HTML content for drift detection."""
    normalized = _normalize_html(html)
    return hashlib.sha256(normalized.encode('utf-8')).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Store / check
# ---------------------------------------------------------------------------

def store_canvas_hash(content_root: str, file_path: str, canvas_html: str):
    """Store the hash AND full snapshot of the content we just pushed.

    Call this after a successful sync to record what Canvas should contain.
    """
    content_hash = compute_content_hash(canvas_html)

    # 1. Update hash in sync map
    sync_map = load_sync_map(content_root)
    rel_path = os.path.relpath(file_path, content_root).replace('\\', '/')
    entry = sync_map.get(rel_path)

    if isinstance(entry, dict):
        entry['canvas_hash'] = content_hash
    else:
        sync_map[rel_path] = {
            'id': entry,
            'canvas_hash': content_hash
        }

    save_sync_map(content_root, sync_map)

    # 2. Store full HTML snapshot for diffing
    snap_path = _snapshot_path(content_root, file_path)
    try:
        with open(snap_path, 'w', encoding='utf-8') as f:
            f.write(canvas_html)
    except Exception as e:
        logger.debug("    Could not write snapshot: %s", e)


def check_drift(content_root: str, file_path: str, current_canvas_html: str) -> dict:
    """Check if Canvas content has drifted from what we last synced.

    Returns a dict with:
        'drifted': bool — True if Canvas content changed since last sync
        'stored_hash': str — hash from last sync (or None if first sync)
        'current_hash': str — hash of current Canvas content
        'diff': str — human-readable diff (only if drifted, else '')
    """
    current_hash = compute_content_hash(current_canvas_html)

    sync_map = load_sync_map(content_root)
    rel_path = os.path.relpath(file_path, content_root).replace('\\', '/')
    entry = sync_map.get(rel_path)

    stored_hash = None
    if isinstance(entry, dict):
        stored_hash = entry.get('canvas_hash')

    if stored_hash is None:
        return {'drifted': False, 'stored_hash': None, 'current_hash': current_hash, 'diff': ''}

    drifted = stored_hash != current_hash
    diff_text = ''

    if drifted:
        diff_text = _compute_diff(content_root, file_path, current_canvas_html)

    return {'drifted': drifted, 'stored_hash': stored_hash, 'current_hash': current_hash, 'diff': diff_text}


def _compute_diff(content_root: str, file_path: str, current_html: str) -> str:
    """Compute a readable diff between the last-synced snapshot and current Canvas content."""
    snap_path = _snapshot_path(content_root, file_path)

    stored_html = ''
    if os.path.exists(snap_path):
        try:
            with open(snap_path, 'r', encoding='utf-8') as f:
                stored_html = f.read()
        except Exception:
            pass

    if not stored_html:
        return '(no snapshot available — cannot show diff)'

    # Convert both to readable text for a meaningful diff
    old_text = _html_to_text(stored_html)
    new_text = _html_to_text(current_html)

    old_lines = old_text.splitlines(keepends=True)
    new_lines = new_text.splitlines(keepends=True)

    diff = difflib.unified_diff(
        old_lines, new_lines,
        fromfile='last-synced',
        tofile='current-canvas',
        lineterm=''
    )

    return '\n'.join(diff)


# ---------------------------------------------------------------------------
# Batch check (all items)
# ---------------------------------------------------------------------------

def check_all_drift(course, content_root: str) -> list:
    """Check drift for all synced items.

    Returns a list of dicts for items that have drifted:
        [{'file': str, 'type': str, 'title': str, 'diff': str, ...}]
    """
    sync_map = load_sync_map(content_root)
    drifted_items = []

    for rel_path, entry in sync_map.items():
        if not isinstance(entry, dict):
            continue
        if 'canvas_hash' not in entry:
            continue

        canvas_id = entry.get('id')
        if not canvas_id:
            continue

        stored_hash = entry['canvas_hash']

        try:
            if rel_path.endswith('.qmd') or rel_path.endswith('.md'):
                current_html = None
                item_type = 'unknown'
                title = rel_path

                # Try as page
                try:
                    page = course.get_page(canvas_id)
                    current_html = getattr(page, 'body', '') or ''
                    item_type = 'page'
                    title = page.title
                except Exception:
                    pass

                # Try as assignment
                if current_html is None:
                    try:
                        assignment = course.get_assignment(canvas_id)
                        current_html = getattr(assignment, 'description', '') or ''
                        item_type = 'assignment'
                        title = assignment.name
                    except Exception:
                        pass

                if current_html is not None:
                    current_hash = compute_content_hash(current_html)
                    if current_hash != stored_hash:
                        # Build diff
                        abs_path = os.path.join(content_root, rel_path.replace('/', os.sep))
                        diff_text = _compute_diff(content_root, abs_path, current_html)

                        drifted_items.append({
                            'file': rel_path,
                            'type': item_type,
                            'title': title,
                            'stored_hash': stored_hash,
                            'current_hash': current_hash,
                            'diff': diff_text,
                        })

        except Exception as e:
            logger.debug("  Could not check drift for %s: %s", rel_path, e)

    return drifted_items


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    import argparse
    from canvasapi import Canvas
    from handlers.config import get_api_credentials, get_course_id

    parser = argparse.ArgumentParser(description="Check for Canvas-side content drift.")
    parser.add_argument("content_path", nargs="?", default=".", help="Path to the content directory.")
    parser.add_argument("--course-id", help="Canvas Course ID (override).")
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument("--show-diff", action="store_true", help="Show full diff for drifted items.")
    args = parser.parse_args()

    from handlers.log import setup_logging
    setup_logging(verbose=args.verbose)

    content_root = os.path.abspath(args.content_path)
    API_URL, API_TOKEN = get_api_credentials(content_root)
    course_id = get_course_id(content_root, args.course_id)

    if not API_URL or not API_TOKEN or not course_id:
        logger.error("[red]Missing credentials or course ID.[/red]")
        return

    canvas = Canvas(API_URL, API_TOKEN)
    course = canvas.get_course(course_id)
    logger.info("[green]Connected to:[/green] %s", course.name)

    logger.info("[cyan]Checking for Canvas-side modifications...[/cyan]")
    drifted = check_all_drift(course, content_root)

    if drifted:
        logger.warning("[yellow]%d item(s) have been modified on Canvas since last sync:[/yellow]", len(drifted))
        for item in drifted:
            logger.warning("  [yellow]DRIFTED[/yellow] [%s] %s (%s)", item['type'], item['title'], item['file'])
            if args.show_diff and item.get('diff'):
                # Print diff lines
                for line in item['diff'].split('\n'):
                    if line.startswith('+') and not line.startswith('+++'):
                        logger.warning("    [green]%s[/green]", line)
                    elif line.startswith('-') and not line.startswith('---'):
                        logger.warning("    [red]%s[/red]", line)
                    elif line.startswith('@@'):
                        logger.warning("    [cyan]%s[/cyan]", line)
                    else:
                        logger.warning("    %s", line)
        logger.warning("[yellow]Use --force to overwrite, or import changes first with import_from_canvas.py[/yellow]")
    else:
        logger.info("[green]No drift detected. Canvas matches last sync.[/green]")


if __name__ == '__main__':
    main()
