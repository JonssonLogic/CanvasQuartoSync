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
import tempfile
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
    return os.path.join(content_root, SNAPSHOT_DIR, safe + '.html')


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


def resolve_stored_html(obj, attr: str, sent_html: str, refetch=None) -> str:
    """Return the HTML Canvas reports it *stored*, not the HTML we sent.

    Canvas rewrites content on save. Observed on a real course: course-relative
    links are absolutized, ``<tbody>`` is injected into tables, void tags are
    normalized (``<br/>`` -> ``<br>``), and account-level theme CSS may be
    prepended. So the body we PUT is not the body Canvas will serve back.

    Hashing the sent HTML therefore stores a baseline that can never match what
    :func:`check_drift` later fetches, and every check reports drift on content
    nobody has touched. False positives train people to ignore the warning,
    which is worse than having no check at all.

    canvasapi copies the create/update response onto the object it returns, so
    ``getattr(obj, attr)`` is Canvas's own stored version and costs no extra
    request. If the response carried no body we re-fetch, and only if that also
    fails do we fall back to the sent HTML: a slightly wrong baseline still
    beats none.
    """
    html = getattr(obj, attr, None) if obj is not None else None
    if html:
        return html

    if refetch is not None:
        try:
            html = getattr(refetch(), attr, None)
            if html:
                return html
        except Exception as e:
            logger.debug("    Could not re-fetch stored content for drift baseline: %s", e)

    logger.debug("    Canvas returned no stored content; using sent HTML as drift baseline.")
    return sent_html


# ---------------------------------------------------------------------------
# Store / check
# ---------------------------------------------------------------------------

# Assignment settings a person might change in Canvas, and the sync would then
# silently overwrite. `published` is left out on purpose: the extension's
# publish toggle changes it directly in Canvas, and that is not an edit to
# protect. Dates come back from Canvas in one canonical UTC form, so comparing
# the stored strings is safe.
ASSIGNMENT_SETTING_FIELDS = (
    'name', 'points_possible', 'due_at', 'unlock_at', 'lock_at',
    'grading_type', 'submission_types', 'allowed_extensions',
    'allowed_attempts', 'omit_from_final_grade',
)


def assignment_settings_hash(obj) -> str:
    """Fingerprint of an assignment's settings as Canvas reports them."""
    values = {}
    for field in ASSIGNMENT_SETTING_FIELDS:
        v = getattr(obj, field, None)
        if isinstance(v, (list, tuple)):
            v = sorted(str(x) for x in v)
        values[field] = v
    blob = json.dumps(values, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode('utf-8')).hexdigest()[:16]


# Quizzes have no single HTML body, so their Canvas state is written out as one
# canonical text: the settings a person could change, then every question. It
# goes through the same hash, snapshot and diff as a page body, so nothing
# downstream needs to know it is not HTML. Built from what Canvas returns after
# a sync, never from what was sent, for the reason given in resolve_stored_html.
CLASSIC_QUIZ_FIELDS = (
    'title', 'description', 'quiz_type', 'due_at', 'unlock_at', 'lock_at',
    'time_limit', 'allowed_attempts', 'shuffle_answers', 'show_correct_answers',
    'one_question_at_a_time', 'cant_go_back',
)
CLASSIC_QUESTION_FIELDS = (
    'question_name', 'question_type', 'question_text', 'points_possible', 'answers',
)
NEW_QUIZ_FIELDS = ('title', 'instructions', 'due_at', 'unlock_at', 'lock_at')
NEW_QUIZ_ENTRY_FIELDS = (
    'title', 'item_body', 'interaction_type_slug', 'interaction_data',
    'scoring_data', 'scoring_algorithm',
)
QUIZ_KINDS = ('classic_quiz', 'new_quiz')


def _canonical(data) -> str:
    return json.dumps(data, sort_keys=True, ensure_ascii=False, indent=1, default=str)


def classic_quiz_text(quiz, questions) -> str:
    """A classic quiz and its questions as one stable text."""
    data = {f: getattr(quiz, f, None) for f in CLASSIC_QUIZ_FIELDS}
    ordered = sorted(questions, key=lambda q: (getattr(q, 'position', None) or 0,
                                               str(getattr(q, 'id', ''))))
    data['questions'] = [{f: getattr(q, f, None) for f in CLASSIC_QUESTION_FIELDS}
                         for q in ordered]
    return _canonical(data)


def new_quiz_text(quiz: dict, items: list) -> str:
    """A New Quiz and its items, as returned by the New Quizzes API, as one stable text."""
    quiz = quiz if isinstance(quiz, dict) else {}
    data = {f: quiz.get(f) for f in NEW_QUIZ_FIELDS}
    ordered = sorted(items or [], key=lambda i: (i.get('position') or 0, str(i.get('id'))))
    data['items'] = [{
        'position': it.get('position'),
        'points_possible': it.get('points_possible'),
        'entry': {f: (it.get('entry') or {}).get(f) for f in NEW_QUIZ_ENTRY_FIELDS},
    } for it in ordered]
    return _canonical(data)


def store_canvas_hash(content_root: str, file_path: str, canvas_html: str,
                      settings_hash: str = None, kind: str = None):
    """Store the hash AND full snapshot of the content we just pushed.

    Call this after a successful sync to record what Canvas should contain.
    ``settings_hash`` (assignments) records the settings too, so a due date
    changed in Canvas is caught like an edited description. ``kind`` records
    what the id refers to, so a later check does not have to guess.
    """
    from handlers.content_utils import sync_identity
    from datetime import datetime, timezone
    content_hash = compute_content_hash(canvas_html)
    now_iso = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

    # 1. Update hash and last_synced_at in sync map
    sync_map = load_sync_map(content_root)
    rel_path = os.path.relpath(file_path, content_root).replace('\\', '/')
    entry = sync_map.get(rel_path)

    if not isinstance(entry, dict):
        entry = {'id': entry}
        sync_map[rel_path] = entry
    entry['canvas_hash'] = content_hash
    entry['last_synced_at'] = now_iso
    if sync_identity():
        entry['last_synced_by'] = sync_identity()
    if settings_hash is not None:
        entry['canvas_settings_hash'] = settings_hash
    if kind:
        entry['canvas_kind'] = kind

    save_sync_map(content_root, sync_map)

    # 2. Store full HTML snapshot for diffing
    _snapshot_dir(content_root)
    snap_path = _snapshot_path(content_root, file_path)
    try:
        with open(snap_path, 'w', encoding='utf-8') as f:
            f.write(canvas_html)
    except Exception as e:
        logger.debug("    Could not write snapshot: %s", e)


def _remove_snapshot(content_root: str, rel_path: str) -> bool:
    """Delete the stored snapshot for a relative path, if there is one."""
    # Forgetting a file should never bring a snapshot directory into
    # existence, so this only computes the path.
    snap = os.path.join(content_root, SNAPSHOT_DIR,
                        rel_path.replace('/', '__').replace(' ', '_') + '.html')
    if not os.path.isfile(snap):
        return False
    try:
        os.remove(snap)
        return True
    except OSError as e:
        logger.debug("    Could not remove snapshot %s: %s", snap, e)
        return False


def forget_synced_file(content_root: str, rel_path: str) -> bool:
    """Drop everything we remember about a file: sync map entry and snapshot.

    The mirror of :func:`store_canvas_hash`. Without it, deleting content left
    both behind, and the module panel went on reporting a file that is no
    longer on disk as synced. The sharper failure is a stale entry that shares
    a Canvas id with a live file: the reverse id lookup in module_structure is
    last-write-wins, so the dead path could win and hide the real one.

    Returns True if anything was removed.
    """
    rel_path = rel_path.replace('\\', '/')
    removed = False

    sync_map = load_sync_map(content_root)
    if rel_path in sync_map:
        del sync_map[rel_path]
        save_sync_map(content_root, sync_map)
        removed = True

    if _remove_snapshot(content_root, rel_path):
        removed = True

    return removed


def forget_synced_dir(content_root: str, local_dir: str) -> int:
    """Forget every tracked file under a directory. Returns how many were dropped."""
    prefix = local_dir.replace('\\', '/').rstrip('/') + '/'
    sync_map = load_sync_map(content_root)
    doomed = [k for k in sync_map if k.startswith(prefix)]
    if not doomed:
        return 0

    for rel_path in doomed:
        del sync_map[rel_path]
    save_sync_map(content_root, sync_map)

    for rel_path in doomed:
        _remove_snapshot(content_root, rel_path)

    return len(doomed)


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


def check_settings_drift(content_root: str, file_path: str, current_settings_hash: str) -> bool:
    """True if the settings recorded at the last publish differ from Canvas now.

    Entries written before settings were recorded have no hash, and are
    reported as unchanged until their next publish records one.
    """
    sync_map = load_sync_map(content_root)
    rel_path = os.path.relpath(file_path, content_root).replace('\\', '/')
    entry = sync_map.get(rel_path)
    stored = entry.get('canvas_settings_hash') if isinstance(entry, dict) else None
    return stored is not None and stored != current_settings_hash


def baseline_from_revisions(page, stored_hash: str, limit: int = 10):
    """The page body as we last published it, found in Canvas's own history.

    The local snapshot only exists on the machine that published. A colleague
    checking the same page has none, but Canvas keeps every revision of a page,
    so the one whose hash matches the map's canvas_hash is what we published.
    Returns None if it is not among the last ``limit`` revisions.
    """
    try:
        revisions = list(page.get_revisions())[:limit]
    except Exception as e:
        logger.debug("    Could not list page revisions: %s", e)
        return None
    for rev in revisions:
        try:
            full = page.get_revision_by_id(rev.revision_id)
            body = getattr(full, 'body', None)
        except Exception:
            continue
        if body is not None and compute_content_hash(body) == stored_hash:
            return body
    return None


def _compute_diff(content_root: str, file_path: str, current_html: str,
                  baseline_html: str = None) -> str:
    """Compute a readable diff between the last-synced snapshot and current Canvas content.

    ``baseline_html`` is used when this machine has no snapshot, e.g. the
    published version recovered from Canvas's page history.
    """
    snap_path = _snapshot_path(content_root, file_path)

    stored_html = ''
    if os.path.exists(snap_path):
        try:
            with open(snap_path, 'r', encoding='utf-8') as f:
                stored_html = f.read()
        except Exception:
            pass

    if not stored_html and baseline_html:
        stored_html = baseline_html

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
# Canvas → QMD markdown conversion (for diff display)
# ---------------------------------------------------------------------------

def _build_frontmatter(item_type: str, canvas_obj) -> str:
    """Build YAML frontmatter from a Canvas API object for diff comparison."""
    import yaml

    meta = {'canvas': {'type': item_type}}
    if item_type == 'page':
        meta['title'] = getattr(canvas_obj, 'title', '')
        meta['canvas']['published'] = getattr(canvas_obj, 'published', False)
    elif item_type == 'assignment':
        meta['title'] = getattr(canvas_obj, 'name', '')
        meta['canvas']['published'] = getattr(canvas_obj, 'published', False)
        pts = getattr(canvas_obj, 'points_possible', None)
        if pts is not None:
            meta['canvas']['points'] = pts
        sub_types = getattr(canvas_obj, 'submission_types', None)
        if sub_types:
            meta['canvas']['submission_types'] = list(sub_types)
        allowed_ext = getattr(canvas_obj, 'allowed_extensions', None)
        if allowed_ext:
            meta['canvas']['allowed_extensions'] = list(allowed_ext)
        grading = getattr(canvas_obj, 'grading_type', None)
        if grading:
            meta['canvas']['grading_type'] = grading
        for date_field in ('due_at', 'unlock_at', 'lock_at'):
            val = getattr(canvas_obj, date_field, None)
            if val:
                meta['canvas'][date_field] = val
        if getattr(canvas_obj, 'omit_from_final_grade', False):
            meta['canvas']['omit_from_final_grade'] = True
        # Group assignment detection
        group_id = getattr(canvas_obj, 'group_category_id', None)
        if group_id:
            meta['canvas']['group_assignment'] = True

    fm = yaml.dump(meta, default_flow_style=False, allow_unicode=True, sort_keys=False).strip()
    return f'---\n{fm}\n---\n'


def _canvas_html_to_qmd(html: str, item_type: str, canvas_obj,
                         sync_map: dict, content_root: str) -> str:
    """Convert Canvas HTML + API metadata into a .qmd-like string for diff comparison."""
    from import_from_canvas import HtmlToMarkdown

    converter = HtmlToMarkdown(sync_map=sync_map, content_root=content_root)
    body_md = converter.convert(html)
    frontmatter = _build_frontmatter(item_type, canvas_obj)
    return frontmatter + '\n' + body_md


DIFF_TEMP_DIR = '.canvas_diff_temp'


def _write_diff_temp(content_root: str, rel_path: str, content: str) -> str:
    """Write Canvas markdown to a temp file for VS Code diff editor."""
    diff_dir = os.path.join(content_root, DIFF_TEMP_DIR)
    os.makedirs(diff_dir, exist_ok=True)
    safe_name = rel_path.replace('/', '__').replace('\\', '__')
    temp_path = os.path.join(diff_dir, f'canvas__{safe_name}')
    with open(temp_path, 'w', encoding='utf-8') as f:
        f.write(content)
    return temp_path


def cleanup_diff_temp(content_root: str):
    """Remove the temp diff directory."""
    diff_dir = os.path.join(content_root, DIFF_TEMP_DIR)
    if os.path.exists(diff_dir):
        import shutil
        shutil.rmtree(diff_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Batch check (all items)
# ---------------------------------------------------------------------------

def drift_report(course, drifted, content_root, include_diff=False):
    """Shape the result of :func:`check_all_drift` for machine consumption.

    Kept here rather than inline in main() so it can be tested without a
    Canvas connection, and so sync_to_canvas.py stays thin.

    The diff *text* is opt-in because it can be long, matching the human report
    where --show-diff controls the same thing. ``canvas_qmd_path`` is not: it
    is one path, and it is the only way a caller can open a diff editor on the
    Canvas side. Leaving it out meant the extension read undefined, threw
    inside an async handler, and showed the user nothing at all.
    """
    items = []
    for item in drifted:
        entry = {
            'file': item['file'],
            'type': item['type'],
            'title': item['title'],
            'local_path': os.path.join(content_root,
                                       item['file'].replace('/', os.sep)),
            'stored_hash': item.get('stored_hash', ''),
            'current_hash': item.get('current_hash', ''),
        }
        # Absent when the drift check ran without building diffs.
        if item.get('canvas_qmd_path'):
            entry['canvas_qmd_path'] = item['canvas_qmd_path']
        if include_diff:
            entry['diff'] = item.get('diff', '')
        items.append(entry)

    return {
        'course_id': course.id,
        'course_name': course.name,
        'drifted': items,
    }


def check_all_drift(course, content_root: str, include_diff: bool = True) -> list:
    """Check drift for all synced items.

    Returns a list of dicts for items that have drifted:
        [{'file': str, 'type': str, 'title': str, 'diff': str,
          'canvas_qmd_path': str, ...}]

    canvas_qmd_path is a temp file containing the Canvas content converted
    to .qmd format, suitable for opening in VS Code's diff editor.

    ``include_diff=False`` answers only *whether* each item drifted, and
    writes nothing. A caller that just wants a status light should use it:
    building the diff costs an HTML-to-text conversion per item and leaves
    files in .canvas_diff_temp/, which is wasted work when nobody is going
    to open a diff editor. ``diff`` and ``canvas_qmd_path`` are then absent.
    """
    sync_map = load_sync_map(content_root)
    drifted_items = []
    unchecked = []

    for rel_path, entry in sync_map.items():
        if not isinstance(entry, dict):
            continue
        if 'canvas_hash' not in entry:
            continue

        canvas_id = entry.get('id')
        if not canvas_id:
            continue

        stored_hash = entry['canvas_hash']
        kind = entry.get('canvas_kind')
        settings_changed = False

        try:
            if rel_path.endswith(('.qmd', '.md', '.json')):
                current_html = None
                item_type = 'unknown'
                title = rel_path
                canvas_obj = None

                if kind in QUIZ_KINDS:
                    item_type = 'quiz'
                    current_html, title, canvas_obj = _fetch_quiz_text(
                        course, content_root, kind, canvas_id, rel_path)
                    if current_html is None:
                        unchecked.append(rel_path)
                        continue

                # Try as page. Entries without a kind predate it being
                # recorded, and fall through to the old page-then-assignment
                # guess.
                if current_html is None and kind in (None, 'page'):
                    try:
                        page = course.get_page(canvas_id)
                        current_html = getattr(page, 'body', '') or ''
                        item_type = 'page'
                        title = page.title
                        canvas_obj = page
                    except Exception:
                        pass

                # Try as assignment
                if current_html is None and kind in (None, 'assignment'):
                    try:
                        assignment = course.get_assignment(canvas_id)
                        current_html = getattr(assignment, 'description', '') or ''
                        item_type = 'assignment'
                        title = assignment.name
                        canvas_obj = assignment
                        settings_changed = check_settings_drift(
                            content_root, os.path.join(content_root, rel_path),
                            assignment_settings_hash(assignment))
                    except Exception:
                        pass

                if current_html is None:
                    unchecked.append(rel_path)

                if current_html is not None:
                    current_hash = compute_content_hash(current_html)
                    if current_hash != stored_hash or settings_changed:
                        item = {
                            'file': rel_path,
                            'type': item_type,
                            'title': title,
                            'stored_hash': stored_hash,
                            'current_hash': current_hash,
                        }
                        if settings_changed:
                            item['settings_changed'] = True

                        if include_diff:
                            abs_path = os.path.join(content_root, rel_path.replace('/', os.sep))
                            baseline = None
                            if (item_type == 'page'
                                    and not os.path.exists(_snapshot_path(content_root, abs_path))):
                                # Published from another machine: recover what
                                # was published from Canvas's page history.
                                baseline = baseline_from_revisions(canvas_obj, stored_hash)
                            item['diff'] = _compute_diff(content_root, abs_path, current_html,
                                                         baseline_html=baseline)

                            # Canvas QMD for VS Code's diff editor
                            canvas_qmd = _canvas_html_to_qmd(
                                current_html, item_type, canvas_obj,
                                sync_map, content_root
                            )
                            item['canvas_qmd_path'] = _write_diff_temp(
                                content_root, rel_path, canvas_qmd
                            )

                        drifted_items.append(item)

        except Exception as e:
            logger.debug("  Could not check drift for %s: %s", rel_path, e)
            unchecked.append(rel_path)

    # "Could not check" is not "clean". Saying so is the difference between a
    # check people can trust and one that quietly passes (#15).
    if unchecked:
        logger.warning("[yellow]Could not check %d item(s) against Canvas:[/yellow] %s",
                       len(unchecked), ', '.join(unchecked))

    return drifted_items


def _fetch_quiz_text(course, content_root, kind, canvas_id, rel_path):
    """Canvas's current state of a quiz as canonical text, its title and object.

    Returns (None, rel_path, None) if it could not be fetched.
    """
    try:
        if kind == 'classic_quiz':
            quiz = course.get_quiz(canvas_id)
            return (classic_quiz_text(quiz, list(quiz.get_questions())),
                    getattr(quiz, 'title', rel_path), quiz)
        from handlers.new_quiz_api import NewQuizAPIClient, resolve_credentials
        client = NewQuizAPIClient(*resolve_credentials(course, content_root))
        quiz = client.get_quiz(course.id, canvas_id)
        items = client.list_items(course.id, canvas_id)
        return (new_quiz_text(quiz, items if isinstance(items, list) else []),
                (quiz or {}).get('title', rel_path), None)
    except Exception as e:
        logger.debug("  Could not fetch quiz %s: %s", rel_path, e)
        return None, rel_path, None


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
        logger.warning("[yellow]Bring the change into the .qmd and sync with --accept-drift <file>, "
                       "or overwrite it with --accept-drift <file> directly.[/yellow]")
    else:
        logger.info("[green]No drift detected. Canvas matches last sync.[/green]")


if __name__ == '__main__':
    main()
