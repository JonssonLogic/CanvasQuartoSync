"""Read the Canvas module structure and reconcile it with local files.

A GUI, a script or a dry run all need the same question answered before
anything is written: what does Canvas currently hold, which of it corresponds
to a file on disk, and what exists on only one side.

``sync_to_canvas.py --module-structure`` prints the result as JSON on stdout.
Nothing here writes to Canvas.

The answer is in **local order**: modules and items come in the order the
course layout gives them (``handlers/course_layout.py``), because that is the
order a sync will produce and the order the Course Board lets you change.
Whatever exists only in Canvas is slotted in after the item it follows there.

Without a Canvas connection (``course=None``) the local half is still
returned, with the reason in ``problems``, so the board can show the course
and say what to fix instead of showing nothing.

Matching is deliberately layered, because none of the three signals is
reliable on its own:

1. the sync map, by Canvas id, which is exact when it is present
2. the filename, normalised
3. the frontmatter title, normalised

A fresh clone has no sync map (see the discussion in #8), so 2 and 3 are what
keep the answer useful there.
"""

import os
import re
from datetime import datetime, timezone

import frontmatter as fm

from handlers.content_utils import load_sync_map, save_sync_map, parse_module_name
from handlers.course_layout import load_layout
from handlers.drift_detector import check_all_drift


def _normalize_name(name: str) -> str:
    """Normalize a name for fuzzy matching: lowercase, strip special chars, collapse spaces."""
    name = re.sub(r'^\d+_', '', name)  # strip leading NN_ prefix
    name = os.path.splitext(name)[0]   # strip file extension
    name = re.sub(r'[^a-z0-9åäöéü]', '', name.lower())  # keep only alphanumeric + common Swedish
    return name


def _backfill_last_synced(sync_map: dict, content_root: str):
    """Backfill last_synced_at for entries that were synced before this field existed.

    Sets last_synced_at to 'now' — these items were in sync before this feature
    existed, so we treat the current state as the baseline. Any future Canvas
    edits or local edits will then be detected correctly.
    """
    now_iso = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    changed = False
    for rel_path, entry in sync_map.items():
        if not isinstance(entry, dict):
            continue
        if entry.get('last_synced_at'):
            continue
        # Only backfill entries that have been synced (have canvas_hash or id)
        if not entry.get('canvas_hash') and not entry.get('id'):
            continue
        entry['last_synced_at'] = now_iso
        changed = True
    if changed:
        save_sync_map(content_root, sync_map)


def _iso_mtime(path):
    try:
        mt = os.path.getmtime(path)
        return datetime.fromtimestamp(mt, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    except Exception:
        return ''


def _title_of(abs_path):
    """The frontmatter title of a .qmd/.md file, or ''."""
    if not abs_path.endswith(('.qmd', '.md')):
        return ''
    try:
        return fm.load(abs_path).metadata.get('title', '') or ''
    except Exception:
        return ''


def _display_title(rel_path, title):
    stem = os.path.splitext(rel_path.split('/')[-1])[0]
    return title or parse_module_name(stem).replace('_', ' ')


def _interleave(local, canvas, match_of, key=id):
    """Local entries in local order, Canvas-only ones after what they follow.

    ``canvas`` is in Canvas order. ``match_of(c)`` is the local entry a Canvas
    entry corresponds to, or None. A Canvas-only entry is anchored to the
    nearest matched entry before it in Canvas; with none, it goes first.
    ``key`` identifies a local entry: ``id`` for records, the value itself for
    paths. Returns ``[(local or None, canvas or None)]``.
    """
    after = {}      # key(local entry) -> [canvas-only entries]
    head = []
    anchor = None
    pair = {}
    for c in canvas:
        m = match_of(c)
        if m is not None:
            pair[key(m)] = c
            anchor = m
        elif anchor is None:
            head.append(c)
        else:
            after.setdefault(key(anchor), []).append(c)
    out = [(None, c) for c in head]
    for l in local:
        out.append((l, pair.get(key(l))))
        out += [(None, c) for c in after.get(key(l), [])]
    return out


def fetch_module_structure(course, content_root: str,
                          with_drift: bool = False, problems=None) -> dict:
    """Fetch Canvas module structure and match against local files.

    Returns a dict with course info and modules, each with items annotated
    with whether they exist locally.

    ``with_drift`` additionally reports, per item, whether Canvas has been
    edited since the last sync, as ``canvas_drift``. It is off by default
    because it costs one Canvas request per synced item, which would make
    opening a panel several seconds slower for an answer nobody asked for.
    Without it every item reports ``canvas_drift: None``, meaning "not
    checked" rather than "no drift".

    This is the only reliable answer for assignments. ``updated_at`` is
    fetched for pages alone, because Canvas bumps an assignment's timestamp
    for submissions, grading and due date edits, so it says nothing about
    the content. A content hash does.

    ``course`` may be None: the result then holds the local layout only, and
    ``problems`` (passed in by the caller) says why Canvas is missing.
    """
    problems = list(problems or [])
    layout = load_layout(content_root)
    sync_map = load_sync_map(content_root)

    if course is not None:
        # Backfill last_synced_at for legacy entries that were synced before this field existed
        _backfill_last_synced(sync_map, content_root)

    # Build reverse map: canvas_id -> local rel_path
    # Build last_synced lookup: rel_path -> last_synced_at ISO string
    id_to_local = {}
    path_to_last_synced = {}

    def _still_on_disk(rel: str) -> bool:
        return os.path.isfile(os.path.join(content_root, rel.replace('/', os.sep)))

    def _claim(key, rel_path: str):
        """Bind a Canvas id to a local path, preferring one that still exists.

        Two paths can carry the same id. Renaming a file leaves the old key in
        the map, and the title-search fallback in the handlers then re-attaches
        the new name to the same Canvas page, so both keys point at it. Plain
        last-write-wins let whichever sorted later win, which is how a deleted
        file came to be listed as the synced one while the file actually on
        disk was reported local-only.
        """
        incumbent = id_to_local.get(key)
        if incumbent is None or (_still_on_disk(rel_path) and not _still_on_disk(incumbent)):
            id_to_local[key] = rel_path

    for rel_path, entry in sync_map.items():
        if isinstance(entry, dict):
            canvas_id = entry.get('id')
            if canvas_id is not None:
                _claim(canvas_id, rel_path)
                _claim(str(canvas_id), rel_path)
                try:
                    _claim(int(canvas_id), rel_path)
                except (ValueError, TypeError):
                    pass
            last_synced = entry.get('last_synced_at', '')
            if last_synced:
                path_to_last_synced[rel_path] = last_synced

    # One record per local module, in layout order. 'files' are every file the
    # module could match a Canvas item to: what the sync includes, plus files
    # in the folder it does not (a PDF not yet listed), so an item uploaded
    # before still finds its source.
    local_mods = []
    for mod in layout.modules:
        files = mod.items + mod.unlisted
        titles = {}
        for f in files:
            t = _title_of(os.path.join(content_root, mod.dir, f))
            if t:
                titles[f"{mod.dir}/{f}"] = t
        local_mods.append({'mod': mod, 'titles': titles,
                           'paths': [f"{mod.dir}/{f}" for f in files]})

    base = {
        'course_name': None, 'course_code': '', 'course_id': None,
        'total_students': None, 'term': '', 'workflow_state': '',
        'default_view': '', 'time_zone': '', 'storage_quota_mb': None, 'created_at': '',
        'connected': course is not None,
        'problems': problems,
        'layout': {
            'mode': layout.mode,
            'problems': [p.__dict__ for p in layout.problems],
            'unlisted_dirs': layout.unlisted_dirs,
            'root_items': layout.root_items,
        },
    }

    def _local_item(lm, rel, canvas_known):
        """A row for a file with no Canvas item (yet)."""
        abs_p = os.path.join(content_root, rel.replace('/', os.sep))
        synced = rel in sync_map
        return {
            'title': _display_title(rel, lm['titles'].get(rel)),
            'type': 'LocalOnly',
            'published': None,
            'indent': 0,
            'local_path': rel,
            'content_id': None,
            'page_url': None,
            'module_item_id': None,
            'html_url': None,
            'local_mtime': _iso_mtime(abs_p),
            'last_synced_at': path_to_last_synced.get(rel, ''),
            # Offline we cannot say it is missing from Canvas, only whether the
            # map remembers syncing it.
            'local_only': canvas_known or not synced,
            'offline': not canvas_known,
            'included': True,
        }

    if course is None:
        modules = []
        for lm in local_mods:
            mod = lm['mod']
            modules.append({
                'name': mod.name, 'id': None, 'published': None,
                'local_dir': mod.dir, 'in_layout': True,
                'items': [_local_item(lm, p, False) for p in mod.item_paths()],
                'unlisted': mod.unlisted,
            })
        base['modules'] = modules
        return base

    # Batch-fetch updated_at for pages only (1 API call).
    #
    # It is not a content timestamp for anything, and the comment here used to
    # imply it was reliable for pages. It is not: publishing a page bumps it,
    # so the panel reported "Canvas newer" on a page immediately after
    # publishing it from the panel. For assignments it is worse still, moving
    # for submissions, grading and due date changes, which is why it is not
    # fetched for them at all.
    #
    # Treat it as "something happened here", never as "the content differs".
    # A content hash answers that; see with_drift above.
    page_updated = {}
    # A module item for a Page carries no content_id, only page_url, while the
    # sync map records the numeric page_id. Without this bridge a page can
    # never be matched by id, and falls back to matching on name inside its
    # module: the moment somebody renames the module in Canvas, the page looks
    # Canvas-only and its local file looks orphaned.
    page_id_by_slug = {}
    for p in course.get_pages():
        url = getattr(p, 'url', '') or ''
        page_updated[url] = getattr(p, 'updated_at', '')
        page_id = getattr(p, 'page_id', None)
        if page_id is not None:
            page_id_by_slug[url] = page_id
        slug = url.rsplit('/', 1)[-1] if url else ''
        if slug:
            page_updated[slug] = getattr(p, 'updated_at', '')
            if page_id is not None:
                page_id_by_slug[slug] = page_id

    # One pass over the sync map, not one per item. Empty and never consulted
    # unless the caller asked, so the default path makes no extra requests.
    drifted_paths = set()
    if with_drift:
        for d in check_all_drift(course, content_root, include_diff=False):
            drifted_paths.add(d['file'])

    def _id_match(item):
        """The local file for an item, from the sync map alone. No guessing."""
        item_id = (getattr(item, 'content_id', None)
                   or getattr(item, 'page_url', None)
                   or getattr(item, 'id', None))
        hit = None
        if item_id is not None:
            hit = id_to_local.get(item_id) or id_to_local.get(str(item_id))
        if not hit and item.type == 'Page':
            slug = getattr(item, 'page_url', None)
            if slug:
                hit = id_to_local.get(slug)
                if not hit:
                    pid = page_id_by_slug.get(slug)
                    if pid is not None:
                        hit = id_to_local.get(pid) or id_to_local.get(str(pid))
        return hit

    canvas_mods = []
    for module in course.get_modules():
        canvas_mods.append((module, list(module.get_module_items())))

    # Which local module is each Canvas module? By name first, because that is
    # what the sync itself uses. Renaming a module in Canvas breaks that, so
    # fall back to asking the items: whichever directory holds the files they
    # already map to is this module's directory, whatever it is called.
    by_norm = {}
    for lm in local_mods:
        by_norm.setdefault(_normalize_name(lm['mod'].name), lm)
        by_norm.setdefault(_normalize_name(lm['mod'].dir), lm)
    by_dir = {lm['mod'].dir: lm for lm in local_mods}
    mod_match = {}
    taken = set()
    for module, items in canvas_mods:
        lm = by_norm.get(_normalize_name(module.name))
        if lm is None:
            votes = {}
            for it in items:
                hit = _id_match(it)
                if hit and '/' in hit:
                    d = hit.split('/')[0]
                    votes[d] = votes.get(d, 0) + 1
            if votes:
                lm = by_dir.get(max(votes, key=votes.get))
        if lm is not None and id(lm) not in taken:
            taken.add(id(lm))
            mod_match[module.id] = lm

    def _match_item(item, lm, claimed):
        """The local path of a Canvas item inside module ``lm``, or None."""
        local_path = _id_match(item)
        if local_path:
            return local_path
        if lm is None:
            return None
        item_type = item.type
        item_title = getattr(item, 'title', 'Untitled')
        free = [p for p in lm['paths'] if p not in claimed]
        names = {_normalize_name(p.split('/')[-1]): p for p in free}
        titles = {_normalize_name(lm['titles'][p]): p for p in free if p in lm['titles']}

        norm_title = _normalize_name(item_title)
        # For File items with "(PDF)" — strip it and match against source QMD
        norm_title_alt = None
        if item_type == 'File' and '(PDF)' in item_title:
            norm_title_alt = _normalize_name(item_title.replace('(PDF)', ''))

        # Strategy 2: match by normalized filename
        for t in (norm_title, norm_title_alt):
            if t and t in names:
                return names[t]
        # Strategy 3: match by frontmatter title
        if norm_title in titles:
            return titles[norm_title]
        # Strategy 4: substring match on filename or frontmatter title
        check = [t for t in (norm_title, norm_title_alt) if t]
        for table in (names, titles):
            for local_norm, local_rel in table.items():
                if local_norm and any(local_norm in t or t in local_norm for t in check):
                    return local_rel
        return None

    def _canvas_row(module, item, local_path):
        item_type = item.type
        updated_at = ''
        if item_type == 'Page':
            # Look up updated_at, pages only. A hint that something moved,
            # not evidence that the content did. See the note above.
            updated_at = page_updated.get(getattr(item, 'page_url', ''), '')
        row = {
            'title': getattr(item, 'title', 'Untitled'),
            'type': item_type,
            'published': getattr(item, 'published', False),
            'indent': getattr(item, 'indent', 0),
            'local_path': local_path,
            'content_id': getattr(item, 'content_id', None),
            'page_url': getattr(item, 'page_url', None),
            'module_item_id': getattr(item, 'id', None),
            'html_url': getattr(item, 'html_url', None) or '',
            'updated_at': updated_at,
            'local_mtime': _iso_mtime(os.path.join(content_root, local_path.replace('/', os.sep))) if local_path else '',
            'last_synced_at': path_to_last_synced.get(local_path, '') if local_path else '',
            # True drifted, False checked and clean, None not checked.
            'canvas_drift': (local_path in drifted_paths) if (with_drift and local_path) else None,
        }
        external_url = getattr(item, 'external_url', None)
        if external_url:
            row['external_url'] = external_url
        return row

    def _module_rows(module, items, lm):
        mod = lm['mod'] if lm else None
        included = set(mod.item_paths()) if mod else set()
        claimed = set()
        matched = {}
        for it in items:
            p = _match_item(it, lm, claimed)
            if p and p not in claimed:
                claimed.add(p)
                matched[id(it)] = p
        if mod is None:
            return [_canvas_row(module, it, matched.get(id(it))) for it in items]

        local_paths = mod.item_paths()
        # Canvas items matched to a file the layout does not include (a PDF
        # nobody listed) keep their Canvas place, flagged.
        rows = []
        for l, c in _interleave(local_paths, items, lambda it: matched.get(id(it)) if matched.get(id(it)) in included else None,
                                 key=lambda p: p):
            if c is None:
                rows.append(_local_item(lm, l, True))
            else:
                row = _canvas_row(module, c, l or matched.get(id(c)))
                if row['local_path'] and row['local_path'] not in included:
                    row['included'] = False
                rows.append(row)
        return rows

    modules = []
    pairs = _interleave(local_mods, canvas_mods, lambda cm: mod_match.get(cm[0].id))
    for lm, cm in pairs:
        if cm is None:
            mod = lm['mod']
            modules.append({
                'name': mod.name, 'id': None, 'published': None,
                'local_dir': mod.dir, 'in_layout': True,
                'items': [_local_item(lm, p, True) for p in mod.item_paths()],
                'unlisted': mod.unlisted,
            })
            continue
        module, items = cm
        modules.append({
            'name': module.name,
            'id': module.id,
            'published': getattr(module, 'published', False),
            'local_dir': lm['mod'].dir if lm else '',
            'in_layout': lm is not None,
            'items': _module_rows(module, items, lm),
            'unlisted': lm['mod'].unlisted if lm else [],
        })

    base.update({
        'course_name': course.name,
        'course_code': getattr(course, 'course_code', ''),
        'course_id': course.id,
        'total_students': getattr(course, 'total_students', None),
        'term': getattr(course, 'term', {}).get('name', '') if isinstance(getattr(course, 'term', None), dict) else '',
        'workflow_state': getattr(course, 'workflow_state', ''),
        'default_view': getattr(course, 'default_view', ''),
        'time_zone': getattr(course, 'time_zone', ''),
        'storage_quota_mb': getattr(course, 'storage_quota_mb', None),
        'created_at': getattr(course, 'created_at', ''),
        'modules': modules,
    })
    return base
