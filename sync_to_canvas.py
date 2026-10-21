import os
import sys
import json
import argparse
import json
import sys
import re
from canvasapi import Canvas

from handlers.log import logger, setup_logging
from handlers.calendar_handler import CalendarHandler
from handlers.subheader_handler import SubHeaderHandler
from handlers.external_link_handler import ExternalLinkHandler
from handlers.content_utils import upload_file, prune_orphaned_assets, FOLDER_FILES, parse_module_name, is_valid_name, verify_sync_map_course, load_sync_map, save_sync_map, set_sync_identity
from handlers.single_sync import build_handlers, find_or_create_module, arrange_modules, remove_stale_placements, sync_single_file
from handlers.course_layout import load_layout, is_ordered, free_name, append_item, add_module, remove_module
from handlers.module_structure import fetch_module_structure
from handlers import __version__
from handlers.config import get_api_credentials, get_course_id, load_config
from handlers.drift_detector import check_all_drift, drift_report, forget_synced_file, forget_synced_dir
from handlers.sync_policy import SyncPolicy, set_policy, get_policy, SOLO, TEAM
from handlers.team import TeamSync, TeamModeError


def _normalize_name(name: str) -> str:
    """Normalize a name for fuzzy matching: lowercase, strip special chars, collapse spaces."""
    name = re.sub(r'^\d+_', '', name)  # strip leading NN_ prefix
    name = os.path.splitext(name)[0]   # strip file extension
    name = re.sub(r'[^a-z0-9åäöéü]', '', name.lower())  # keep only alphanumeric + common Swedish
    return name


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

    ordered = is_ordered(content_root)
    safe_name = sanitize_filename(title)
    if ordered:
        prefix = ''
    else:
        # Determine next file index
        existing = sorted(f for f in os.listdir(mod_path) if is_valid_name(f))
        if existing:
            last_num = int(re.match(r'^(\d+)', existing[-1]).group(1))
            next_idx = last_num + 1
        else:
            next_idx = 1
        prefix = f'{next_idx:02d}_'

    def _place(filename):
        """The path to write, and in an ordered course, list it last."""
        if ordered:
            filename = free_name(mod_path, filename)
            append_item(content_root, module_dir, filename, item.get('module_name'))
        return os.path.join(mod_path, filename)

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
                file_path = _place(f'{prefix}{original_name}')
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

    filepath = _place(f'{prefix}{safe_name}{ext}')
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

    ordered = is_ordered(content_root)

    # Refuse if a local folder normalizes to the same name
    try:
        for d in os.listdir(content_root):
            if os.path.isdir(os.path.join(content_root, d)) and (ordered or is_valid_name(d)):
                if _normalize_name(d) == norm_new:
                    return {'success': False, 'error': f'Local folder "{d}" already matches that name'}
    except Exception:
        pass

    try:
        # Create the Canvas module
        module_obj = course.create_module(module={'name': name, 'published': published})
    except Exception as e:
        return {'success': False, 'error': f'Canvas create failed: {e}'}

    safe = re.sub(r'[^A-Za-z0-9åäöÅÄÖ _-]', '', name).strip()
    safe = re.sub(r'\s+', ' ', safe)
    if not safe:
        safe = 'Module'

    if ordered:
        dir_name = safe
    else:
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
        dir_name = f'{next_idx:02d}_{safe}'
    dir_path = os.path.join(content_root, dir_name)
    try:
        os.makedirs(dir_path, exist_ok=True)
        if ordered:
            add_module(content_root, dir_name, name)
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
                    # Unconditionally, even when the file was already gone: it
                    # is the sync map entry, not the file, that makes a deleted
                    # page keep showing up as synced in the module panel.
                    forget_synced_file(content_root, local_path)
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
                    forget_synced_dir(content_root, local_dir)
                    if is_ordered(content_root):
                        remove_module(content_root, local_dir)
                deleted += 1

            elif target == 'local_file':
                local_path = entry.get('local_path')
                if local_path:
                    abs_p = os.path.join(content_root, local_path.replace('/', os.sep))
                    if os.path.isfile(abs_p):
                        os.remove(abs_p)
                    forget_synced_file(content_root, local_path)
                    deleted += 1

            else:
                failed += 1
                errors.append(f'Unknown target: {target}')
        except Exception as e:
            failed += 1
            errors.append(str(e))

    return {'success': failed == 0, 'deleted': deleted, 'failed': failed, 'errors': errors}


def _problem(level, code, message, action=''):
    return {'level': level, 'code': code, 'message': message, 'action': action}


def _print_module_structure(content_root, api_url, api_token, course_id, with_drift):
    """Structure mode: what Canvas holds and how it lines up with local files.

    Read-only, JSON on stdout. Always exits 0 with a document: when Canvas
    cannot be reached the document holds the local course and ``problems``
    says why, each with what to do about it.
    """
    problems = []
    course = None
    if not course_id and not os.path.isfile(os.path.join(content_root, 'config.toml')):
        problems.append(_problem('error', 'no_config',
            'This folder has no config.toml, so the board does not know which Canvas course it belongs to.',
            'Create config.toml with course_id and canvas_api_url. New Project in the sidebar sets one up.'))
    elif not course_id:
        problems.append(_problem('error', 'no_course_id',
            'No course_id in config.toml.',
            'Open config.toml and set course_id to the number in the Canvas course URL (…/courses/12345).'))
    if not problems and (not api_url or not api_token):
        missing = 'canvas_api_url' if not api_url else 'a Canvas API token'
        problems.append(_problem('error', 'no_credentials',
            f'No {missing}, so Canvas cannot be reached.',
            'Set canvas_api_url in config.toml, and the token in the CANVAS_API_TOKEN environment '
            'variable (or canvas_token_path in config.toml).'))
    if not problems:
        try:
            canvas = Canvas(api_url, api_token)
            course = canvas.get_course(course_id, include=['total_students', 'term'])
        except Exception as e:
            text = str(e)
            if 'Invalid access token' in text or '401' in text:
                problems.append(_problem('error', 'bad_token',
                    'Canvas refused the API token.',
                    'Create a new token in Canvas (Account > Settings > New access token) and set CANVAS_API_TOKEN.'))
            elif 'Not Found' in text or '404' in text:
                problems.append(_problem('error', 'course_not_found',
                    f'Canvas has no course {course_id}, or this token cannot see it.',
                    'Check course_id in config.toml.'))
            else:
                problems.append(_problem('error', 'connect_failed',
                    f'Could not reach Canvas: {text[:200]}',
                    'Check the network and canvas_api_url in config.toml, then Refresh.'))
            course = None
    if course is not None and not verify_sync_map_course(content_root, course_id, getattr(course, 'name', None)):
        problems.append(_problem('error', 'wrong_course',
            f'.canvas_sync_map.json belongs to another course than {course_id}.',
            'Check course_id in config.toml. If the course really changed, delete the sync map.'))
        course = None

    try:
        from handlers.course_layout import is_ordered
        if not is_ordered(content_root) and load_layout(content_root).modules:
            problems.append(_problem('info', 'nn_prefix',
                'This course orders modules with NN_ prefixes in file names.',
                'Convert it to order files to rearrange the course by dragging. Nothing in Canvas changes.'))
    except Exception:
        pass

    print(json.dumps(fetch_module_structure(course, content_root, with_drift=with_drift,
                                            problems=problems),
                     ensure_ascii=False))
    return 0


def main():
    parser = argparse.ArgumentParser(description="Sync local content to Canvas.")
    parser.add_argument("--version", action="version", version=f"CanvasQuartoSync {__version__}")
    parser.add_argument("content_path", nargs="?", default=".", help="Path to the content directory (default: current dir).")
    parser.add_argument("--sync-calendar", action="store_true", help="Enable calendar synchronization (Opt-in).")
    parser.add_argument("--course-id", help="Canvas Course ID (Override).")
    parser.add_argument("--force", "-f", action="store_true", help="Force re-render all files (ignore recorded source fingerprints).")
    parser.add_argument("--accept-drift", action="append", default=[], metavar="FILE",
                        help="Publish FILE even though it was edited in Canvas since the last sync, "
                             "overwriting that edit. Repeat for several files, or pass 'all'.")
    parser.add_argument("--break-lock", action="store_true",
                        help="Team mode: take over the sync lock from a sync that crashed.")
    parser.add_argument("--check-drift", action="store_true", help="Check if Canvas content was modified outside sync (no sync performed).")
    parser.add_argument("--show-diff", action="store_true", help="Show full diff when using --check-drift.")
    parser.add_argument("--json", action="store_true", help="With --check-drift, print the result as JSON on stdout instead of a human report.")
    parser.add_argument("--exit-code", action="store_true", help="With --check-drift, exit 2 when drift is found. Without it the check reports and exits 0, as git diff does.")
    parser.add_argument("--only", help="Sync only a specific file (relative path from content dir, e.g. '01_Intro/02_Welcome.qmd').")
    parser.add_argument("--module-structure", action="store_true", help="Print the Canvas module structure as JSON, reconciled with local files. Reads only, syncs nothing.")
    parser.add_argument("--with-drift", action="store_true", help="With --module-structure: also report whether Canvas has been edited since the last sync. Costs one request per synced item.")
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
    # Modes that write a JSON document to stdout. The logger is a rich console
    # on stdout, so "Connecting to Canvas..." in front of the document makes it
    # unparseable. Asking for machine output implies quiet; errors still reach
    # the console and the exit code carries the verdict.
    #
    # Listed rather than or-ed together so adding a mode is one word here
    # instead of an edit to an expression every new mode has to touch.
    machine_output = any(getattr(args, flag, False)
                         for flag in ('module_structure', 'json'))
    setup_logging(verbose=args.verbose,
                  quiet=args.quiet or machine_output,
                  log_file=args.log_file)

    # Helper to resolve paths relative to content_path
    content_root = os.path.abspath(args.content_path)
    if not os.path.exists(content_root):
        logger.error("[red]Content directory not found:[/red] %s", content_root)
        return 1

    logger.info("Target content directory: [dim]%s[/dim]", content_root)

    # Advisory only: tell the user if this folder's AI authoring kit predates the
    # tool. Never blocks a sync and never rewrites anything.
    try:
        from init_content_project import kit_status
        stale = kit_status(content_root)
        if stale:
            logger.warning("[yellow]%s[/yellow]", stale)
    except Exception:
        pass

    # Resolve Context
    API_URL, API_TOKEN = get_api_credentials(content_root)
    course_id = get_course_id(content_root, args.course_id)

    # The Course Board asks for the structure even when Canvas is out of
    # reach, and shows the local course with a banner saying what to fix.
    # So this mode reports a missing setting instead of failing on it.
    if args.module_structure:
        return _print_module_structure(content_root, API_URL, API_TOKEN, course_id, args.with_drift)

    if not API_URL or not API_TOKEN:
         logger.error("[red]Canvas credentials not found.[/red] Set CANVAS_API_URL / CANVAS_API_TOKEN env vars, or provide canvas_api_url / canvas_token_path in config.toml.")
         return 1

    if not course_id:
        logger.error("[red]Course ID not specified.[/red] Set course_id in config.toml, or pass --course-id.")
        return 1

    logger.info("[cyan]Connecting to Canvas...[/cyan]")
    try:
        canvas = Canvas(API_URL, API_TOKEN)
        course = canvas.get_course(course_id, include=['total_students', 'term'])
        logger.info("[green]Connected to course:[/green] [bold]%s[/bold] (ID: %s)", course.name, course.id)
    except Exception as e:
        logger.error("[red]Connection failed:[/red] %s", e)
        return 1

    # The sync map's Canvas ids only mean anything in the course they came from,
    # so refuse before touching anything if this folder belongs to another course.
    if not verify_sync_map_course(content_root, course_id, getattr(course, 'name', None)):
        return 1

    workflow = str(load_config(content_root).get('workflow', SOLO)).strip().lower()
    if workflow not in (SOLO, TEAM):
        logger.error('[red]Unknown workflow "%s" in config.toml.[/red] Use "solo" or "team".', workflow)
        return 1

    # Who is syncing, for the shared map and the team lock.
    identity = ''
    try:
        identity = getattr(canvas.get_current_user(), 'name', '') or ''
    except Exception:
        pass
    set_sync_identity(identity)

    accept = {('all' if p.strip().lower() == 'all' else p.replace('\\', '/'))
              for p in args.accept_drift}
    set_policy(SyncPolicy(workflow=workflow, accept=accept))

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

        # --only was accepted and then ignored here, so "check just this file"
        # checked every synced item and offered the caller a list of all of
        # them. The flag means the same thing for a drift check as it does for
        # a sync: act on this one file.
        if args.only:
            wanted = args.only.replace('\\', '/')
            drifted = [d for d in drifted if d['file'] == wanted]

        if args.json:
            print(json.dumps(
                drift_report(course, drifted, content_root, args.show_diff),
                ensure_ascii=False))
            # --exit-code applies here too: a caller redirecting the JSON to a
            # file still wants the verdict in $?, and losing it would make the
            # two flags mutually exclusive for no reason.
            return 2 if (drifted and args.exit_code) else 0

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
            logger.warning("[yellow]Bring the change into the .qmd and sync with --accept-drift <file>, "
                           "or overwrite it with --accept-drift <file> directly.[/yellow]")
            # Findings are not a failure unless the caller asked for that. git
            # diff behaves the same way: reporting is the default, --exit-code
            # opts in. 2 rather than 1 so a caller can still tell "someone
            # edited a page" from "the check could not run".
            return 2 if args.exit_code else 0
        logger.info("[green]No drift detected. Canvas content matches last sync.[/green]")
        return 0

    team = None
    if workflow == TEAM:
        team = TeamSync(content_root, who=identity, break_lock=args.break_lock)
        try:
            team.begin()
        except TeamModeError as e:
            logger.error("[red]Not syncing (team mode).[/red] %s", e)
            return 1

    rc = 1
    team_ok = True
    try:
        _apply_force(args, content_root)
        rc = _sync_content(args, course, canvas, content_root)
        if get_policy().report() and rc == 0:
            rc = 2
    finally:
        if team is not None:
            team_ok = team.end()
    if not team_ok and rc == 0:
        rc = 1
    return rc


def _apply_force(args, content_root):
    """--force: forget the recorded source fingerprints, so everything re-renders.

    The rest of each entry (Canvas ids, drift baselines) is kept. Runs after the
    team-mode pull, so it edits the map everyone just agreed on.
    """
    if not args.force:
        return
    sync_map = load_sync_map(content_root)
    only_rel = args.only.replace('\\', '/') if args.only else None
    changed = False
    for rel, entry in sync_map.items():
        if only_rel and rel != only_rel:
            continue
        if isinstance(entry, dict):
            for key in ('source_hash', 'mtime'):
                if key in entry:
                    del entry[key]
                    changed = True
    if changed:
        save_sync_map(content_root, sync_map)
    if only_rel:
        logger.info("[yellow]Force mode:[/yellow] %s will re-render", only_rel)
    else:
        logger.info("[yellow]Force mode:[/yellow] all files will re-render")


def _sync_content(args, course, canvas, content_root):
    """Sync one file (--only) or the whole course. Returns the exit code."""
    handlers = build_handlers()

    # --only: sync a single asset and exit (delegates to the reusable function
    # so the same logic is available to a future GUI).
    if args.only:
        candidate = os.path.abspath(os.path.join(content_root, args.only))
        if not os.path.exists(candidate):
            candidate = os.path.abspath(args.only)
        result = sync_single_file(course, content_root, candidate, canvas=canvas, handlers=handlers)
        if result.success:
            logger.info("[bold green]%s[/bold green]", result.message)
            return 0
        logger.error("[red]%s[/red]", result.message)
        return 1

    # Calendar Sync (Opt-in)
    if args.sync_calendar:
        logger.info("[bold cyan]Starting calendar sync...[/bold cyan]")
        cal_handler = CalendarHandler()
        schedule_path = os.path.join(content_root, "schedule.yaml")
        try:
            cal_handler.sync(schedule_path, course, canvas_obj=canvas, content_root=content_root)
        except FileNotFoundError:
            logger.warning("No schedule.yaml found at %s", schedule_path)
        except Exception as e:
            logger.error("Calendar sync failed: %s", e)
    else:
        logger.info("[dim]Skipping calendar sync (use --sync-calendar to enable)[/dim]")

    logger.info("[bold cyan]Starting content sync...[/bold cyan]")

    # 1. Walk the course in layout order: _modules.yml and _order.yml, or NN_
    # prefixes in a course that has not moved to order files.
    layout = load_layout(content_root)
    for p in layout.problems:
        log = logger.error if p.level == 'error' else logger.warning
        log("[yellow]%s[/yellow] %s", p.path, p.message)

    module_count = 0
    item_count = 0
    placed = []     # (module id, module item) for every item put in place

    # Case B: Root files, synced outside any module.
    for item in layout.root_items:
        item_path = os.path.join(content_root, item)
        for handler in handlers:
            # SubHeaders and ExternalLinks make no sense without a module.
            if isinstance(handler, (SubHeaderHandler, ExternalLinkHandler)):
                continue
            if handler.can_handle(item_path):
                logger.info("[cyan]Syncing root item:[/cyan] %s", item)
                try:
                    handler.sync(item_path, course, module=None, canvas_obj=canvas, content_root=content_root)
                    item_count += 1
                except Exception as e:
                    logger.exception("Failed to sync root item %s", item)
                break

    # Case A: Modules.
    for mod in layout.modules:
        item_path = os.path.join(content_root, mod.dir)
        module_name = mod.name
        logger.info("[cyan]Processing module:[/cyan] [bold]%s[/bold]", module_name)
        module_count += 1

        try:
            module_obj = find_or_create_module(course, module_name)

            # Track synced items for reordering
            synced_module_items = []

            for filename in mod.items:
                file_path = os.path.join(item_path, filename)

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
                    # Case C: Solo Asset (PDF, ZIP, etc) listed in the module
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

            placed += [(module_obj.id, mi) for mi in synced_module_items]

            # Reorder Module Items
            if synced_module_items:
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

    # 2. Put the modules themselves in order. Without this a module created
    # by the sync stayed wherever Canvas appended it.
    if layout.modules:
        try:
            arrange_modules(course, [m.name for m in layout.modules])
        except Exception as e:
            logger.error("Failed to order modules: %s", e)
        # A file moved to another module folder leaves its item in the old one.
        try:
            remove_stale_placements(course, placed, {m.name for m in layout.modules})
        except Exception as e:
            logger.error("Failed to check for moved items: %s", e)

    # 3. Cleanup Orphans
    prune_orphaned_assets(course)

    logger.info("[bold green]Sync complete.[/bold green] Processed %d modules, synced %d items.", module_count, item_count)
    return 0


if __name__ == "__main__":
    sys.exit(main())
