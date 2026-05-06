import os
import argparse
import json
import sys
import re
from canvasapi import Canvas

from handlers.log import logger, setup_logging
from handlers.base_handler import BaseHandler
from handlers.study_guide_handler import StudyGuideHandler
from handlers.page_handler import PageHandler
from handlers.assignment_handler import AssignmentHandler
from handlers.quiz_handler import QuizHandler
from handlers.new_quiz_handler import NewQuizHandler
from handlers.calendar_handler import CalendarHandler
from handlers.subheader_handler import SubHeaderHandler
from handlers.external_link_handler import ExternalLinkHandler
from handlers.content_utils import upload_file, prune_orphaned_assets, FOLDER_FILES, parse_module_name, load_sync_map, save_sync_map
from handlers import __version__
from handlers.config import load_config, get_api_credentials, get_course_id
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
    from datetime import datetime, timezone
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


def _fetch_module_structure(course, content_root: str) -> dict:
    """Fetch Canvas module structure and match against local files.

    Returns a dict with course info and modules, each with items annotated
    with whether they exist locally.
    """
    sync_map = load_sync_map(content_root)

    # Backfill last_synced_at for legacy entries that were synced before this field existed
    _backfill_last_synced(sync_map, content_root)

    # Build reverse map: canvas_id -> local rel_path
    # Build last_synced lookup: rel_path -> last_synced_at ISO string
    id_to_local = {}
    path_to_last_synced = {}
    for rel_path, entry in sync_map.items():
        if isinstance(entry, dict):
            canvas_id = entry.get('id')
            if canvas_id is not None:
                id_to_local[canvas_id] = rel_path
                id_to_local[str(canvas_id)] = rel_path
                try:
                    id_to_local[int(canvas_id)] = rel_path
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
    import frontmatter as fm
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
    # Assignment updated_at is NOT reliable — Canvas bumps it for student
    # submissions, grading, due date changes, etc. No content-specific
    # timestamp exists in the Canvas API for assignments.
    page_updated = {}
    for p in course.get_pages():
        page_updated[getattr(p, 'url', '')] = getattr(p, 'updated_at', '')
        slug = getattr(p, 'url', '').rsplit('/', 1)[-1] if getattr(p, 'url', '') else ''
        if slug:
            page_updated[slug] = getattr(p, 'updated_at', '')

    modules = []
    for module in course.get_modules():
        mod_name = module.name
        mod_items = []

        # Find matching local module dir by normalized name
        norm_mod = _normalize_name(mod_name)
        local_mod_files = local_name_index.get(norm_mod, {})
        local_mod_titles = local_title_index.get(norm_mod, {})

        for item in module.get_module_items():
            item_type = item.type
            item_title = getattr(item, 'title', 'Untitled')
            item_id = getattr(item, 'content_id', None) or getattr(item, 'page_url', None) or getattr(item, 'id', None)
            published = getattr(item, 'published', False)
            indent = getattr(item, 'indent', 0)
            external_url = getattr(item, 'external_url', None)

            # Look up updated_at — only for Pages (reliable)
            updated_at = ''
            if item_type == 'Page':
                page_slug = getattr(item, 'page_url', '')
                updated_at = page_updated.get(page_slug, '')

            # Strategy 1: match via sync map (canvas ID)
            local_path = None
            if item_id is not None:
                local_path = id_to_local.get(item_id) or id_to_local.get(str(item_id))
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
                    from datetime import datetime, timezone
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
            }
            if external_url:
                item_data['external_url'] = external_url
            mod_items.append(item_data)

        modules.append({
            'name': mod_name,
            'id': module.id,
            'published': getattr(module, 'published', False),
            'items': mod_items,
            'local_dir': norm_to_local_dir.get(norm_mod, ''),
        })

    # Inject unmatched local files into their matching Canvas modules
    all_local_paths = set()
    for files in local_files_by_module.values():
        all_local_paths.update(files)
    matched_paths = {item['local_path'] for mod in modules for item in mod['items'] if item['local_path']}
    unmatched_local = sorted(all_local_paths - matched_paths)

    # Build reverse lookup: normalized module name → module index
    norm_to_mod_idx = {}
    for idx, mod in enumerate(modules):
        norm_to_mod_idx[_normalize_name(mod['name'])] = idx

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


def _import_single_item(course, content_root: str, item_json: str) -> dict:
    """Import a single Canvas item to a local QMD file."""
    from import_from_canvas import (
        HtmlToMarkdown, generate_page_qmd, generate_assignment_qmd,
        generate_external_link_qmd, generate_subheader_qmd, sanitize_filename
    )

    try:
        item = json.loads(item_json)
    except json.JSONDecodeError as e:
        return {'success': False, 'error': f'Invalid JSON: {e}'}

    module_dir = item.get('module_dir', '')
    item_type = item.get('item_type', '')
    title = item.get('title', 'Untitled')
    content_id = item.get('content_id')
    page_url = item.get('page_url')
    published = item.get('published', False)
    indent = item.get('indent', 0)
    external_url = item.get('external_url', '')

    # Ensure module dir exists
    mod_path = os.path.join(content_root, module_dir)
    os.makedirs(mod_path, exist_ok=True)

    converter = HtmlToMarkdown(
        sync_map=load_sync_map(content_root),
        content_root=content_root
    )

    # Determine next file index
    existing = sorted(f for f in os.listdir(mod_path) if is_valid_name(f))
    if existing:
        last_num = int(re.match(r'^(\d+)', existing[-1]).group(1))
        next_idx = last_num + 1
    else:
        next_idx = 1
    prefix = f'{next_idx:02d}'
    safe_name = sanitize_filename(title)

    content = ''
    ext = '.qmd'

    try:
        if item_type == 'Page' and page_url:
            page = course.get_page(page_url)
            body_html = getattr(page, 'body', '') or ''
            body_md = converter.convert(body_html)
            content = generate_page_qmd(title, body_md, published)

        elif item_type == 'Assignment' and content_id:
            assignment = course.get_assignment(content_id)
            body_html = getattr(assignment, 'description', '') or ''
            body_md = converter.convert(body_html)
            content = generate_assignment_qmd(title, body_md, assignment)

        elif item_type == 'ExternalUrl':
            new_tab = item.get('new_tab', False)
            content = generate_external_link_qmd(title, external_url, published, new_tab)

        elif item_type == 'SubHeader':
            content = generate_subheader_qmd(title, published, indent)
            ext = '.md'

        elif item_type == 'File' and content_id:
            # Download file
            try:
                file_obj = course.get_file(content_id)
                original_name = getattr(file_obj, 'filename', safe_name)
                file_path = os.path.join(mod_path, f'{prefix}_{original_name}')
                file_obj.download(file_path)
                rel = os.path.relpath(file_path, content_root).replace('\\', '/')
                return {'success': True, 'file': rel}
            except Exception as e:
                return {'success': False, 'error': f'File download failed: {e}'}

        else:
            return {'success': False, 'error': f'Unsupported item type: {item_type}'}

    except Exception as e:
        return {'success': False, 'error': f'Canvas API error: {e}'}

    if not content:
        return {'success': False, 'error': 'No content generated'}

    filename = f'{prefix}_{safe_name}{ext}'
    filepath = os.path.join(mod_path, filename)
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)

    rel = os.path.relpath(filepath, content_root).replace('\\', '/')
    return {'success': True, 'file': rel}


def _set_published(course, pub_json: str) -> dict:
    """Set published state for a module or module item."""
    try:
        data = json.loads(pub_json)
    except json.JSONDecodeError as e:
        return {'success': False, 'error': f'Invalid JSON: {e}'}

    target = data.get('target', '')
    published = data.get('published', False)

    try:
        if target == 'module':
            module_id = data.get('module_id')
            if not module_id:
                return {'success': False, 'error': 'Missing module_id'}
            module = course.get_module(module_id)
            module.edit(module={'published': published})
            state = 'published' if published else 'unpublished'
            return {'success': True, 'message': f'Module {state}'}

        elif target == 'item':
            module_id = data.get('module_id')
            item_id = data.get('item_id')
            if not module_id or not item_id:
                return {'success': False, 'error': 'Missing module_id or item_id'}
            module = course.get_module(module_id)
            item = module.get_module_item(item_id)
            item.edit(module_item={'published': published})
            state = 'published' if published else 'unpublished'
            return {'success': True, 'message': f'Item {state}'}

        else:
            return {'success': False, 'error': f'Unknown target: {target}'}

    except Exception as e:
        return {'success': False, 'error': str(e)}


def _create_module(course, content_root: str, payload_json: str) -> dict:
    """Create a new Canvas module and matching local directory."""
    try:
        data = json.loads(payload_json)
    except json.JSONDecodeError as e:
        return {'success': False, 'error': f'Invalid JSON: {e}'}

    name = (data.get('name') or '').strip()
    published = bool(data.get('published', False))
    if not name:
        return {'success': False, 'error': 'Missing module name'}

    norm_new = _normalize_name(name)

    # Refuse if a Canvas module with the same (normalized) name already exists
    try:
        for m in course.get_modules():
            if _normalize_name(getattr(m, 'name', '')) == norm_new:
                return {'success': False, 'error': f'Canvas already has a module named "{m.name}"'}
    except Exception as e:
        return {'success': False, 'error': f'Could not list Canvas modules: {e}'}

    # Refuse if a local folder normalizes to the same name
    try:
        for d in os.listdir(content_root):
            if os.path.isdir(os.path.join(content_root, d)) and is_valid_name(d):
                if _normalize_name(d) == norm_new:
                    return {'success': False, 'error': f'Local folder "{d}" already matches that name'}
    except Exception:
        pass

    try:
        # Create the Canvas module
        module_obj = course.create_module(module={'name': name, 'published': published})
    except Exception as e:
        return {'success': False, 'error': f'Canvas create failed: {e}'}

    # Determine next local prefix by scanning content_root for NN_* dirs
    next_idx = 1
    try:
        existing = [d for d in os.listdir(content_root)
                    if os.path.isdir(os.path.join(content_root, d)) and re.match(r'^\d{2,}_', d)]
        max_idx = 0
        for d in existing:
            m = re.match(r'^(\d+)_', d)
            if m:
                max_idx = max(max_idx, int(m.group(1)))
        next_idx = max_idx + 1
    except Exception:
        pass

    safe = re.sub(r'[^A-Za-z0-9åäöÅÄÖ _-]', '', name).strip()
    safe = re.sub(r'\s+', ' ', safe)
    if not safe:
        safe = 'Module'
    prefix = f'{next_idx:02d}'
    dir_name = f'{prefix}_{safe}'
    dir_path = os.path.join(content_root, dir_name)
    try:
        os.makedirs(dir_path, exist_ok=True)
    except Exception as e:
        return {'success': False, 'error': f'Could not create local dir: {e}', 'module_id': module_obj.id}

    return {
        'success': True,
        'message': f'Module "{name}" created',
        'module_id': module_obj.id,
        'module_dir': dir_name,
    }


def _delete_items(course, content_root: str, payload_json: str) -> dict:
    """Delete a batch of Canvas items/modules and optionally their local files.

    Payload: {"items":[{target,module_id,item_id?,local_path?,local_dir?}, ...]}
    """
    try:
        data = json.loads(payload_json)
    except json.JSONDecodeError as e:
        return {'success': False, 'error': f'Invalid JSON: {e}'}

    items = data.get('items') or []
    deleted = 0
    failed = 0
    errors = []
    import shutil

    for entry in items:
        target = entry.get('target')
        try:
            if target == 'item':
                module_id = entry.get('module_id')
                item_id = entry.get('item_id')
                if module_id and item_id:
                    module = course.get_module(module_id)
                    mi = module.get_module_item(item_id)
                    mi.delete()
                local_path = entry.get('local_path')
                if local_path:
                    abs_p = os.path.join(content_root, local_path.replace('/', os.sep))
                    if os.path.isfile(abs_p):
                        os.remove(abs_p)
                deleted += 1

            elif target == 'module':
                module_id = entry.get('module_id')
                if module_id:
                    module = course.get_module(module_id)
                    module.delete()
                local_dir = entry.get('local_dir')
                if local_dir:
                    abs_d = os.path.join(content_root, local_dir)
                    if os.path.isdir(abs_d):
                        shutil.rmtree(abs_d)
                deleted += 1

            elif target == 'local_file':
                local_path = entry.get('local_path')
                if local_path:
                    abs_p = os.path.join(content_root, local_path.replace('/', os.sep))
                    if os.path.isfile(abs_p):
                        os.remove(abs_p)
                    deleted += 1

            else:
                failed += 1
                errors.append(f'Unknown target: {target}')
        except Exception as e:
            failed += 1
            errors.append(str(e))

    return {'success': failed == 0, 'deleted': deleted, 'failed': failed, 'errors': errors}


def is_valid_name(name):
    """
    Checks if the name starts with two or more digits followed by an underscore.
    Example: '01_Intro' -> True, '000_Link' -> True, 'Intro' -> False, '1_Intro' -> False
    """
    return bool(re.match(r'^\d{2,}_', name))

def main():
    parser = argparse.ArgumentParser(description="Sync local content to Canvas.")
    parser.add_argument("--version", action="version", version=f"CanvasQuartoSync {__version__}")
    parser.add_argument("content_path", nargs="?", default=".", help="Path to the content directory (default: current dir).")
    parser.add_argument("--sync-calendar", action="store_true", help="Enable calendar synchronization (Opt-in).")
    parser.add_argument("--course-id", help="Canvas Course ID (Override).")
    parser.add_argument("--force", "-f", action="store_true", help="Force re-render all files (ignore cached mtimes).")
    parser.add_argument("--check-drift", action="store_true", help="Check if Canvas content was modified outside sync (no sync performed).")
    parser.add_argument("--show-diff", action="store_true", help="Show full diff when using --check-drift.")
    parser.add_argument("--diff-json", action="store_true", help="Output drift results as JSON (for VS Code extension).")
    parser.add_argument("--only", help="Sync only a specific file (relative path from content dir, e.g. '01_Intro/02_Welcome.qmd').")
    parser.add_argument("--module-structure", action="store_true", help="Output Canvas module structure as JSON (for VS Code extension).")
    parser.add_argument("--import-item", help="Import a single Canvas item as JSON: {\"module_dir\":...,\"item_type\":...,\"content_id\":...,\"page_url\":...,\"title\":...,\"published\":...,\"indent\":...,\"external_url\":...}")
    parser.add_argument("--set-published", help="Set published state as JSON: {\"target\":\"module\"|\"item\",\"module_id\":N,\"item_id\":N,\"published\":bool}")
    parser.add_argument("--create-module", help="Create a new Canvas module as JSON: {\"name\":\"...\",\"published\":bool}")
    parser.add_argument("--delete", help="Batch delete items/modules as JSON: {\"items\":[{\"target\":\"item|module|local_file\",...}]}")

    verbosity = parser.add_mutually_exclusive_group()
    verbosity.add_argument("--verbose", "-v", action="store_true", help="Show detailed debug output.")
    verbosity.add_argument("--quiet", "-q", action="store_true", help="Only show errors.")
    parser.add_argument("--log-file", help="Write full debug log to a file.")

    args = parser.parse_args()

    # Set up logging before anything else
    setup_logging(verbose=args.verbose, quiet=args.quiet, log_file=args.log_file)

    # Helper to resolve paths relative to content_path
    content_root = os.path.abspath(args.content_path)
    if not os.path.exists(content_root):
        logger.error("[red]Content directory not found:[/red] %s", content_root)
        return

    logger.info("Target content directory: [dim]%s[/dim]", content_root)

    # Force re-render: clear cached mtimes (but preserve other sync map data)
    if args.force:
        sync_map = load_sync_map(content_root)
        if args.only:
            # Single-file force: only clear mtime for that file
            only_rel = args.only.replace('\\', '/')
            entry = sync_map.get(only_rel)
            if isinstance(entry, dict) and 'mtime' in entry:
                del entry['mtime']
                save_sync_map(content_root, sync_map)
                logger.info("[yellow]Force mode:[/yellow] cleared mtime for %s", only_rel)
        else:
            # Full force: clear all mtimes
            changed = False
            for entry in sync_map.values():
                if isinstance(entry, dict) and 'mtime' in entry:
                    del entry['mtime']
                    changed = True
            if changed:
                save_sync_map(content_root, sync_map)
            logger.info("[yellow]Force mode:[/yellow] cleared cached mtimes, all files will re-render")

    # Resolve Context
    API_URL, API_TOKEN = get_api_credentials(content_root)
    course_id = get_course_id(content_root, args.course_id)

    if not API_URL or not API_TOKEN:
         logger.error("[red]Canvas credentials not found.[/red] Set CANVAS_API_URL / CANVAS_API_TOKEN env vars, or provide canvas_api_url / canvas_token_path in config.toml.")
         return

    if not course_id:
        logger.error("[red]Course ID not specified.[/red] Provide it via --course-id, config.toml, or a 'course_id.txt' file in the content directory.")
        return

    logger.info("[cyan]Connecting to Canvas...[/cyan]")
    try:
        canvas = Canvas(API_URL, API_TOKEN)
        course = canvas.get_course(course_id, include=['total_students', 'term'])
        logger.info("[green]Connected to course:[/green] [bold]%s[/bold] (ID: %s)", course.name, course.id)
    except Exception as e:
        logger.error("[red]Connection failed:[/red] %s", e)
        return

    # Module structure mode: output Canvas module structure as JSON, then exit
    if args.module_structure:
        structure = _fetch_module_structure(course, content_root)
        print(f'MODULE_STRUCTURE_JSON:{json.dumps(structure, ensure_ascii=False)}')
        return

    # Import single item mode
    if args.import_item:
        result = _import_single_item(course, content_root, args.import_item)
        print(f'IMPORT_RESULT_JSON:{json.dumps(result, ensure_ascii=False)}')
        return

    # Publish/unpublish mode
    if args.set_published:
        result = _set_published(course, args.set_published)
        print(f'PUBLISH_RESULT_JSON:{json.dumps(result, ensure_ascii=False)}')
        return

    if args.create_module:
        result = _create_module(course, content_root, args.create_module)
        print(f'CREATE_MODULE_JSON:{json.dumps(result, ensure_ascii=False)}')
        return

    if args.delete:
        result = _delete_items(course, content_root, args.delete)
        print(f'DELETE_RESULT_JSON:{json.dumps(result, ensure_ascii=False)}')
        return

    # Drift check mode: only check for Canvas-side modifications, then exit
    if args.check_drift:
        logger.info("[bold cyan]Checking for Canvas-side modifications...[/bold cyan]")
        drifted = check_all_drift(course, content_root)

        # JSON output mode for VS Code extension
        if args.diff_json:
            result = []
            for item in drifted:
                result.append({
                    'file': item['file'],
                    'type': item['type'],
                    'title': item['title'],
                    'canvas_qmd_path': item.get('canvas_qmd_path', ''),
                    'local_path': os.path.join(content_root, item['file'].replace('/', os.sep)),
                })
            # Write JSON to stdout on a clearly marked line for the extension to parse
            print(f'DRIFT_JSON:{json.dumps(result)}')
            if not drifted:
                logger.info("[green]No drift detected. Canvas content matches last sync.[/green]")
            return

        if drifted:
            logger.warning("[yellow]%d item(s) have been modified on Canvas since last sync:[/yellow]", len(drifted))
            for item in drifted:
                logger.warning("  [yellow]DRIFTED[/yellow] [%s] %s (%s)", item['type'], item['title'], item['file'])
                if args.show_diff and item.get('diff'):
                    for line in item['diff'].split('\n'):
                        if line.startswith('+') and not line.startswith('+++'):
                            logger.warning("    [green]%s[/green]", line)
                        elif line.startswith('-') and not line.startswith('---'):
                            logger.warning("    [red]%s[/red]", line)
                        elif line.startswith('@@'):
                            logger.warning("    [cyan]%s[/cyan]", line)
                        else:
                            logger.warning("    %s", line)
            logger.warning("[yellow]Use import_from_canvas.py to pull changes, or --force to overwrite.[/yellow]")
        else:
            logger.info("[green]No drift detected. Canvas content matches last sync.[/green]")
        return

    handlers = [
        StudyGuideHandler(),
        PageHandler(),
        AssignmentHandler(),
        NewQuizHandler(),
        QuizHandler(),
        ExternalLinkHandler(),
        SubHeaderHandler()
    ]

    # Calendar Sync (Opt-in)
    if args.sync_calendar:
        logger.info("[bold cyan]Starting calendar sync...[/bold cyan]")
        cal_handler = CalendarHandler()
        schedule_path = os.path.join(content_root, "schedule.yaml")
        try:
            cal_handler.sync(schedule_path, course, canvas_obj=canvas)
        except FileNotFoundError:
            logger.warning("No schedule.yaml found at %s", schedule_path)
        except Exception as e:
            logger.error("Calendar sync failed: %s", e)
    else:
        logger.info("[dim]Skipping calendar sync (use --sync-calendar to enable)[/dim]")

    # --only filter: resolve to absolute path for matching
    only_filter = None
    if args.only:
        # Try as relative to content_root first, then as relative to CWD / absolute
        candidate = os.path.abspath(os.path.join(content_root, args.only))
        if not os.path.exists(candidate):
            candidate = os.path.abspath(args.only)
        if not os.path.exists(candidate):
            logger.error("[red]File not found:[/red] %s", args.only)
            return
        # Verify the file is inside the content root
        if not candidate.startswith(content_root):
            logger.error("[red]File is not inside content directory:[/red] %s", candidate)
            return
        only_filter = candidate
        rel_display = os.path.relpath(only_filter, content_root)
        logger.info("[cyan]Syncing only:[/cyan] %s", rel_display)

    logger.info("[bold cyan]Starting content sync...[/bold cyan]")

    # 1. Walk the directory
    # Sort ensure robust ordering
    items = sorted(os.listdir(content_root))

    module_count = 0
    item_count = 0

    for item in items:
        item_path = os.path.join(content_root, item)

        # Case A: Module Directory
        if os.path.isdir(item_path):
            if not is_valid_name(item):
                continue

            # --only: skip modules that don't contain the target file
            if only_filter and not only_filter.startswith(item_path):
                continue

            # This is a Module directory
            module_name = parse_module_name(item)
            logger.info("[cyan]Processing module:[/cyan] [bold]%s[/bold]", module_name)
            module_count += 1

            # Find or Create Module in Canvas
            module_obj = None
            try:
                # Helper to find module
                modules = course.get_modules(search_term=module_name)
                for m in modules:
                    if m.name == module_name:
                        module_obj = m
                        break

                if not module_obj:
                    logger.info("  [green]Creating new module:[/green] %s", module_name)
                    module_obj = course.create_module(module={'name': module_name})
                else:
                    logger.debug("  Found existing module: %s (ID: %s)", module_name, module_obj.id)

                # Walk files inside the module
                module_files = sorted(os.listdir(item_path))

                # Track synced items for reordering
                synced_module_items = []

                for filename in module_files:
                    file_path = os.path.join(item_path, filename)

                    if os.path.isdir(file_path):
                        continue

                    if not is_valid_name(filename):
                        continue

                    # --only: skip files that don't match
                    if only_filter and os.path.abspath(file_path) != only_filter:
                        continue

                    # Delegation Logic
                    handled = False
                    for handler in handlers:
                        if handler.can_handle(file_path):
                            try:
                                mod_item = handler.sync(file_path, course, module_obj, canvas_obj=canvas, content_root=content_root)
                                if mod_item:
                                    synced_module_items.append(mod_item)
                                item_count += 1
                            except Exception as e:
                                logger.exception("Failed to sync %s", filename)
                            handled = True
                            break

                    if not handled:
                        # Case C: Solo Asset (PDF, ZIP, etc) with NN_ prefix in Module
                        logger.info("  [yellow]Uploading file:[/yellow] %s", filename)

                        # Upload to namespaced folder
                        file_url, file_id = upload_file(course, file_path, FOLDER_FILES, content_root=content_root)

                        if file_id and module_obj:
                            # Add to module as File item
                            mod_item = handlers[0].add_to_module(module_obj, {
                                'type': 'File',
                                'content_id': file_id,
                                'title': parse_module_name(filename),
                                'published': True
                            })
                            if mod_item:
                                synced_module_items.append(mod_item)
                            item_count += 1

                # Reorder Module Items (skip when --only, since we didn't sync all items)
                if synced_module_items and not only_filter:
                    logger.debug("  Verifying module item order (%d items)...", len(synced_module_items))
                    for i, mod_item in enumerate(synced_module_items):
                        expected_position = i + 1
                        if mod_item.position != expected_position:
                            logger.debug("    Moving '%s' to position %d (was %d)", mod_item.title, expected_position, mod_item.position)
                            try:
                                mod_item.edit(module_item={'position': expected_position})
                                mod_item.position = expected_position
                            except Exception as e:
                                logger.error("  Failed to reorder item %s: %s", mod_item.title, e)

            except Exception as e:
                 logger.error("Failed to process module %s: %s", module_name, e)

        # Case B: Root File (No Module)
        elif os.path.isfile(item_path):
             if not is_valid_name(item):
                 continue

             # --only: skip root files that don't match
             if only_filter and os.path.abspath(item_path) != only_filter:
                 continue

             # Delegation Logic
             handled = False
             for handler in handlers:
                # Skip SubHeaders and ExternalLinks in root (doesn't make sense without a module)
                if isinstance(handler, (SubHeaderHandler, ExternalLinkHandler)):
                    continue

                if handler.can_handle(item_path):
                    logger.info("[cyan]Syncing root item:[/cyan] %s", item)
                    try:
                        # Pass module=None
                        handler.sync(item_path, course, module=None, canvas_obj=canvas, content_root=content_root)
                        item_count += 1
                    except Exception as e:
                        logger.exception("Failed to sync root item %s", item)
                    handled = True
                    break

    # 3. Cleanup Orphans (skip when --only to avoid deleting assets from un-synced files)
    if not only_filter:
        prune_orphaned_assets(course)

    logger.info("[bold green]Sync complete.[/bold green] Processed %d modules, synced %d items.", module_count, item_count)

if __name__ == "__main__":
    main()
