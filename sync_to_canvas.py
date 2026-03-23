import os
import argparse
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
from handlers.content_utils import upload_file, prune_orphaned_assets, FOLDER_FILES, parse_module_name
from handlers import __version__
from handlers.config import load_config, get_api_credentials, get_course_id
from handlers.drift_detector import check_all_drift


def is_valid_name(name):
    """
    Checks if the name starts with exactly two digits followed by an underscore.
    Example: '01_Intro' -> True, 'Intro' -> False, '1_Intro' -> False
    """
    return bool(re.match(r'^\d{2}_', name))

def main():
    parser = argparse.ArgumentParser(description="Sync local content to Canvas.")
    parser.add_argument("--version", action="version", version=f"CanvasQuartoSync {__version__}")
    parser.add_argument("content_path", nargs="?", default=".", help="Path to the content directory (default: current dir).")
    parser.add_argument("--sync-calendar", action="store_true", help="Enable calendar synchronization (Opt-in).")
    parser.add_argument("--course-id", help="Canvas Course ID (Override).")
    parser.add_argument("--force", "-f", action="store_true", help="Force re-render all files (ignore cached mtimes).")
    parser.add_argument("--check-drift", action="store_true", help="Check if Canvas content was modified outside sync (no sync performed).")
    parser.add_argument("--show-diff", action="store_true", help="Show full diff when using --check-drift.")
    parser.add_argument("--only", help="Sync only a specific file (relative path from content dir, e.g. '01_Intro/02_Welcome.qmd').")

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

    # Force re-render: delete sync map to clear cached mtimes
    if args.force:
        sync_map_path = os.path.join(content_root, '.canvas_sync_map.json')
        if os.path.exists(sync_map_path):
            os.remove(sync_map_path)
            logger.info("[yellow]Force mode:[/yellow] cleared sync map, all files will re-render")

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
        course = canvas.get_course(course_id)
        logger.info("[green]Connected to course:[/green] [bold]%s[/bold] (ID: %s)", course.name, course.id)
    except Exception as e:
        logger.error("[red]Connection failed:[/red] %s", e)
        return

    # Drift check mode: only check for Canvas-side modifications, then exit
    if args.check_drift:
        logger.info("[bold cyan]Checking for Canvas-side modifications...[/bold cyan]")
        drifted = check_all_drift(course, content_root)
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
