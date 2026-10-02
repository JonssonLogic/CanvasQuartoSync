"""Read the Canvas module structure and reconcile it with local files.

A GUI, a script or a dry run all need the same question answered before
anything is written: what does Canvas currently hold, which of it corresponds
to a file on disk, and what exists on only one side.

``sync_to_canvas.py --module-structure`` prints the result as JSON on stdout.
Nothing here writes to Canvas.

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

from handlers.content_utils import load_sync_map, save_sync_map, is_valid_name
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


def fetch_module_structure(course, content_root: str,
                          with_drift: bool = False) -> dict:
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
    """
    sync_map = load_sync_map(content_root)

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

    # Walk local module dirs to find files
    # local_files_by_module: { dir_name: [rel_path, ...] }
    # local_name_index: { normalized_module_name: { normalized_file_name: rel_path } }
    # local_title_index: { normalized_module_name: { normalized_frontmatter_title: rel_path } }
    # local_path_to_title: { rel_path: frontmatter_title }
    local_files_by_module = {}
    local_name_index = {}
    local_title_index = {}
    local_path_to_title = {}
    for entry in sorted(os.listdir(content_root)):
        mod_dir = os.path.join(content_root, entry)
        if not os.path.isdir(mod_dir) or not is_valid_name(entry):
            continue
        files = []
        name_map = {}
        title_map = {}
        for fname in sorted(os.listdir(mod_dir)):
            fpath = os.path.join(mod_dir, fname)
            # Quarto render artefacts, written next to the file being rendered
            # and deleted when it finishes. A panel refresh during a sync would
            # otherwise match a Canvas item to 'tmp-pdf-01_Page.qmd' and report
            # the real file as local-only. Every handler and the validator skip
            # these already; this walk was the one place that did not.
            if fname.startswith(('_temp_', 'tmp-')):
                continue
            if os.path.isfile(fpath) and (fname.endswith('.qmd') or fname.endswith('.md') or fname.endswith('.json') or fname.endswith('.pdf')):
                rel = os.path.join(entry, fname).replace('\\', '/')
                files.append(rel)
                name_map[_normalize_name(fname)] = rel
                # Read frontmatter title for QMD/MD files
                if fname.endswith('.qmd') or fname.endswith('.md'):
                    try:
                        post = fm.load(fpath)
                        ft = post.metadata.get('title', '')
                        if ft:
                            title_map[_normalize_name(ft)] = rel
                            local_path_to_title[rel] = ft
                    except Exception:
                        pass
        local_files_by_module[entry] = files
        norm_mod = _normalize_name(entry)
        local_name_index[norm_mod] = name_map
        local_title_index[norm_mod] = title_map

    # Map normalized module name -> local dir name (for empty modules)
    norm_to_local_dir = {}
    for entry in local_files_by_module:
        norm_to_local_dir[_normalize_name(entry)] = entry

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

    modules = []
    for module in course.get_modules():
        mod_name = module.name
        mod_items = []
        items = list(module.get_module_items())

        # Which local directory is this module? By name first, because that is
        # what the sync itself uses. Renaming a module in Canvas breaks that,
        # so fall back to asking the items: whichever directory holds the files
        # they already map to is this module's directory, whatever it is called.
        norm_mod = _normalize_name(mod_name)
        local_dir = norm_to_local_dir.get(norm_mod)
        if local_dir is None:
            votes = {}
            for it in items:
                hit = _id_match(it)
                if hit and '/' in hit:
                    d = hit.split('/')[0]
                    votes[d] = votes.get(d, 0) + 1
            if votes:
                local_dir = max(votes, key=votes.get)

        norm_dir = _normalize_name(local_dir) if local_dir else norm_mod
        local_mod_files = local_name_index.get(norm_dir, {})
        local_mod_titles = local_title_index.get(norm_dir, {})

        for item in items:
            item_type = item.type
            item_title = getattr(item, 'title', 'Untitled')
            item_id = getattr(item, 'content_id', None) or getattr(item, 'page_url', None) or getattr(item, 'id', None)
            published = getattr(item, 'published', False)
            indent = getattr(item, 'indent', 0)
            external_url = getattr(item, 'external_url', None)

            # Look up updated_at, pages only. A hint that something moved,
            # not evidence that the content did. See the note above.
            updated_at = ''
            if item_type == 'Page':
                page_slug = getattr(item, 'page_url', '')
                updated_at = page_updated.get(page_slug, '')

            # Strategy 1: match via sync map (canvas ID)
            local_path = _id_match(item)
            if not local_path and item_type == 'Page':
                page_url = getattr(item, 'page_url', None)
                if page_url:
                    local_path = id_to_local.get(page_url)

            # Strategy 2: match by normalized filename within the same module
            if not local_path and local_mod_files:
                norm_title = _normalize_name(item_title)
                # For File items with "(PDF)" — strip it and match against source QMD
                norm_title_alt = None
                if item_type == 'File' and '(PDF)' in item_title:
                    norm_title_alt = _normalize_name(item_title.replace('(PDF)', ''))
                # Exact match on filename
                if norm_title in local_mod_files:
                    local_path = local_mod_files[norm_title]
                elif norm_title_alt and norm_title_alt in local_mod_files:
                    local_path = local_mod_files[norm_title_alt]

            # Strategy 3: match by frontmatter title
            if not local_path and local_mod_titles:
                norm_title = _normalize_name(item_title)
                if norm_title in local_mod_titles:
                    local_path = local_mod_titles[norm_title]

            # Strategy 4: substring match on filename or frontmatter title
            if not local_path and (local_mod_files or local_mod_titles):
                norm_title = _normalize_name(item_title)
                norm_title_alt = None
                if item_type == 'File' and '(PDF)' in item_title:
                    norm_title_alt = _normalize_name(item_title.replace('(PDF)', ''))
                check_titles = [t for t in [norm_title, norm_title_alt] if t]
                # Check filenames
                for local_norm, local_rel in local_mod_files.items():
                    if not local_norm:
                        continue
                    for t in check_titles:
                        if local_norm in t or t in local_norm:
                            local_path = local_rel
                            break
                    if local_path:
                        break
                # Check frontmatter titles
                if not local_path:
                    for local_norm, local_rel in local_mod_titles.items():
                        if not local_norm:
                            continue
                        for t in check_titles:
                            if local_norm in t or t in local_norm:
                                local_path = local_rel
                                break
                        if local_path:
                            break

            # Collect canvas IDs for API operations
            content_id = getattr(item, 'content_id', None)
            page_url = getattr(item, 'page_url', None)
            item_canvas_id = getattr(item, 'id', None)

            html_url = getattr(item, 'html_url', None) or ''

            # Get local file mtime if matched
            local_mtime = ''
            if local_path:
                abs_local = os.path.join(content_root, local_path.replace('/', os.sep))
                try:
                    mt = os.path.getmtime(abs_local)
                    local_mtime = datetime.fromtimestamp(mt, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
                except Exception:
                    pass

            item_data = {
                'title': item_title,
                'type': item_type,
                'published': published,
                'indent': indent,
                'local_path': local_path,
                'content_id': content_id,
                'page_url': page_url,
                'module_item_id': item_canvas_id,
                'html_url': html_url,
                'updated_at': updated_at,
                'local_mtime': local_mtime,
                'last_synced_at': path_to_last_synced.get(local_path, '') if local_path else '',
                # True drifted, False checked and clean, None not checked.
                'canvas_drift': (local_path in drifted_paths) if (with_drift and local_path) else None,
            }
            if external_url:
                item_data['external_url'] = external_url
            mod_items.append(item_data)

        modules.append({
            'name': mod_name,
            'id': module.id,
            'published': getattr(module, 'published', False),
            'items': mod_items,
            'local_dir': local_dir or '',
        })

    # Inject unmatched local files into their matching Canvas modules
    all_local_paths = set()
    for files in local_files_by_module.values():
        all_local_paths.update(files)
    matched_paths = {item['local_path'] for mod in modules for item in mod['items'] if item['local_path']}
    unmatched_local = sorted(all_local_paths - matched_paths)

    # Reverse lookup for placing unmatched local files, keyed on the module's
    # resolved local directory rather than its Canvas name. Keying on the name
    # meant that renaming a module in Canvas sent every unmatched file in its
    # directory to the "no Canvas module" list, even though the module is right
    # there and the rest of its items matched.
    norm_to_mod_idx = {}
    for idx, mod in enumerate(modules):
        norm_to_mod_idx[_normalize_name(mod['name'])] = idx
    for idx, mod in enumerate(modules):
        if mod['local_dir']:
            norm_to_mod_idx[_normalize_name(mod['local_dir'])] = idx

    orphan_files_by_dir = {}
    for rel_path in unmatched_local:
        dir_name = rel_path.split('/')[0]
        norm_dir = _normalize_name(dir_name)
        mod_idx = norm_to_mod_idx.get(norm_dir)

        if mod_idx is not None:
            # Get display title: prefer frontmatter title, else strip prefix/ext from filename
            fname = rel_path.split('/')[-1]
            display_title = local_path_to_title.get(rel_path) or re.sub(r'^\d+_', '', os.path.splitext(fname)[0]).replace('_', ' ')

            modules[mod_idx]['items'].append({
                'title': display_title,
                'type': 'LocalOnly',
                'published': None,
                'indent': 0,
                'local_path': rel_path,
                'content_id': None,
                'page_url': None,
                'module_item_id': None,
                'html_url': None,
                'local_only': True,
            })
        else:
            orphan_files_by_dir.setdefault(dir_name, []).append(rel_path)

    local_only_modules = [
        {'dir_name': d, 'files': files}
        for d, files in sorted(orphan_files_by_dir.items())
    ]

    return {
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
        'local_only_modules': local_only_modules,
    }
