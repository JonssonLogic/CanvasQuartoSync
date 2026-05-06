import os
import sys
import subprocess
import frontmatter
import re
import shutil
from datetime import datetime
from canvasapi import Canvas
from handlers.base_handler import BaseHandler
from handlers.content_utils import process_content, safe_delete_file, safe_delete_dir, get_mapped_id, save_mapped_id, parse_module_name
from handlers.drift_detector import check_drift, store_canvas_hash
from handlers.log import logger

class AssignmentHandler(BaseHandler):
    _group_set_for_all = None  # Cached choice when user selects "all"

    def can_handle(self, file_path: str) -> bool:
        if not file_path.endswith('.qmd'):
            return False
        if os.path.basename(file_path).startswith('_temp_'):
            return False
        try:
            post = frontmatter.load(file_path)
            canvas_meta = post.metadata.get('canvas', {})
            return canvas_meta.get('type') == 'assignment'
        except:
            return False

    def sync(self, file_path: str, course, module=None, canvas_obj=None, content_root=None):
        filename = os.path.basename(file_path)
        logger.info("  [cyan]Syncing assignment:[/cyan] [bold]%s[/bold]", filename)

        # 1. Check for Skip (Smart Sync)
        current_mtime = os.path.getmtime(file_path)
        existing_id, map_entry = get_mapped_id(content_root, file_path) if content_root else (None, None)

        needs_render = True
        assign_obj = None

        if existing_id and isinstance(map_entry, dict):
            if map_entry.get('mtime') == current_mtime:
                logger.debug("    No changes detected, skipping render")
                needs_render = False
                try:
                    assign_obj = course.get_assignment(existing_id)
                except:
                    logger.warning("    Previously synced assignment not found in Canvas, re-syncing")
                    needs_render = True

        # 1b. Parse Metadata
        post = frontmatter.load(file_path)
        title = post.metadata.get('title', parse_module_name(os.path.splitext(filename)[0]))
        canvas_meta = post.metadata.get('canvas', {})
        published = canvas_meta.get('published', False)
        points = canvas_meta.get('points', 0)
        # Source of Truth: Use empty string to explicitly clear dates in Canvas API
        # (None values are ignored by the API, but '' clears the field)
        due_at = canvas_meta.get('due_at') or ''
        unlock_at = canvas_meta.get('unlock_at') or ''
        lock_at = canvas_meta.get('lock_at') or ''
        grading_type = canvas_meta.get('grading_type') or ''
        submission_types = canvas_meta.get('submission_types', ['online_upload'])
        allowed_extensions = canvas_meta.get('allowed_extensions', [])
        omit_from_final_grade = canvas_meta.get('omit_from_final_grade', False)
        allowed_attempts = canvas_meta.get('allowed_attempts', -1)
        indent = canvas_meta.get('indent', 0)

        # 1b-ii. Resolve group assignment
        group_category_id = None
        group_assignment = canvas_meta.get('group_assignment', False)
        group_set_name = canvas_meta.get('group_set')

        if group_assignment or group_set_name:
            group_category_id = self._resolve_group_set(
                course, file_path, post, canvas_meta,
                group_assignment, group_set_name
            )

        # 1c. Process Content (ALWAYS, to track ACTIVE_ASSET_IDS)
        with open(file_path, 'r', encoding='utf-8') as f:
            raw_content = f.read()

        base_path = os.path.dirname(file_path)
        processed_content = process_content(raw_content, base_path, course, content_root=content_root)

        if needs_render:
            # 2. Render HTML
            html_body = self.render_quarto_document(processed_content, base_path, filename, content_root=content_root)
            if html_body is None:
                return

            assignment_args = {
                'name': title,
                'description': html_body,
                'published': published,
                'points_possible': points,
                'due_at': due_at,
                'unlock_at': unlock_at,
                'lock_at': lock_at,
                'grading_type': grading_type,
                'submission_types': submission_types,
                'allowed_extensions': allowed_extensions,
                'omit_from_final_grade': omit_from_final_grade,
                'allowed_attempts': allowed_attempts
            }

            if group_category_id:
                assignment_args['group_category_id'] = group_category_id

            if assign_obj:
                # Drift detection: warn if Canvas content was modified outside sync
                if content_root:
                    canvas_desc = getattr(assign_obj, 'description', '') or ''
                    drift = check_drift(content_root, file_path, canvas_desc)
                    if drift['drifted']:
                        logger.warning("    [yellow]DRIFT DETECTED:[/yellow] '%s' was modified on Canvas since last sync. Overwriting with local version.", title)

                logger.info("    [yellow]Updating assignment:[/yellow] %s", title)
                logger.debug("    Matched by cached ID: %s", assign_obj.id)
                assign_obj.edit(assignment=assignment_args)
            else:
                # Double check Title Search
                assignments = course.get_assignments(search_term=title)
                existing_item = None
                for a in assignments:
                    if a.name == title:
                        existing_item = a
                        break

                if existing_item:
                    logger.info("    [yellow]Updating assignment:[/yellow] %s", title)
                    logger.debug("    Matched by title search (ID: %s)", existing_item.id)
                    existing_item.edit(assignment=assignment_args)
                    assign_obj = existing_item
                else:
                    logger.info("    [green]Creating assignment:[/green] %s", title)
                    assign_obj = course.create_assignment(assignment=assignment_args)

            # 4c. Update Sync Map and store content hash for drift detection
            if content_root:
                save_mapped_id(content_root, file_path, assign_obj.id, mtime=current_mtime)
                store_canvas_hash(content_root, file_path, html_body)
        else:
            # Render skipped, assign_obj already set
            pass

        # 5. Add to Module
        if module:
            return self.add_to_module(module, {
                'type': 'Assignment',
                'content_id': assign_obj.id,
                'title': assign_obj.name,
                'published': published
            }, indent=indent)

    def _resolve_group_set(self, course, file_path, post, canvas_meta, group_assignment, group_set_name):
        """Resolve group_assignment / group_set to a Canvas group_category_id.

        - If group_set is set: validate it exists on Canvas, return its ID.
        - If only group_assignment is true: list available group sets, prompt
          the user to pick one by name (interactive mode only), then write
          group_set back into the frontmatter so subsequent syncs don't prompt again.
          In non-interactive mode (e.g. VS Code extension), auto-applies if there
          is exactly one group set, otherwise skips with a warning.
        """
        interactive = sys.stdin.isatty()

        group_categories = list(course.get_group_categories())

        if not group_categories:
            logger.warning("    [yellow]group_assignment is true but no group sets exist on Canvas. Skipping group assignment.[/yellow]")
            return None

        gc_by_name = {gc.name: gc for gc in group_categories}

        # Case 1: group_set name is already specified — validate it
        if group_set_name:
            if group_set_name in gc_by_name:
                gc = gc_by_name[group_set_name]
                logger.info("    [cyan]Group set:[/cyan] %s (ID: %s)", gc.name, gc.id)
                return gc.id
            else:
                available = ', '.join(f'"{name}"' for name in gc_by_name)
                logger.error("    [red]Group set \"%s\" not found on Canvas.[/red] Available: %s", group_set_name, available)
                if not interactive:
                    logger.warning("    [yellow]Non-interactive mode: skipping. Update group_set in frontmatter to one of: %s[/yellow]", available)
                    return None
                choice = input(f"    Enter a valid group set name (or press Enter to skip): ").strip()
                if choice and choice in gc_by_name:
                    self._write_group_set_to_frontmatter(file_path, post, choice)
                    gc = gc_by_name[choice]
                    logger.info("    [green]Updated frontmatter with group_set: %s[/green]", choice)
                    return gc.id
                else:
                    logger.warning("    [yellow]Skipping group assignment for this file.[/yellow]")
                    return None

        # If user previously chose "all", reuse that choice
        if self._group_set_for_all:
            if self._group_set_for_all in gc_by_name:
                gc = gc_by_name[self._group_set_for_all]
                self._write_group_set_to_frontmatter(file_path, post, gc.name)
                logger.info("    [cyan]Group set (apply all):[/cyan] %s", gc.name)
                return gc.id

        # Case 2: group_assignment is true but no group_set specified
        if len(group_categories) == 1:
            gc = group_categories[0]
            logger.info("    [cyan]group_assignment: true — one group set available: \"%s\"[/cyan]", gc.name)
            if not interactive:
                # Auto-apply the only available group set
                self._write_group_set_to_frontmatter(file_path, post, gc.name)
                logger.info("    [green]Non-interactive mode: auto-applied group_set: %s[/green]", gc.name)
                return gc.id
            choice = input(f"    Use group set \"{gc.name}\"? [Y/n]: ").strip()
            if choice.lower() in ('', 'y', 'yes'):
                apply_all = input("    Apply to all remaining assignments? [y/N]: ").strip()
                if apply_all.lower() in ('y', 'yes'):
                    self._group_set_for_all = gc.name
                self._write_group_set_to_frontmatter(file_path, post, gc.name)
                logger.info("    [green]Updated frontmatter with group_set: %s[/green]", gc.name)
                return gc.id
            else:
                logger.warning("    [yellow]Skipping group assignment for this file.[/yellow]")
                return None
        else:
            available = ', '.join(f'"{name}"' for name in gc_by_name)
            logger.info("    [cyan]group_assignment: true — available group sets:[/cyan] %s", available)
            if not interactive:
                logger.warning("    [yellow]Non-interactive mode: skipping. Add group_set to frontmatter with one of: %s[/yellow]", available)
                return None
            for i, gc in enumerate(group_categories, 1):
                logger.info("      %d. %s", i, gc.name)
            choice = input("    Enter group set name or number (or press Enter to skip): ").strip()
            if choice.isdigit():
                idx = int(choice) - 1
                if 0 <= idx < len(group_categories):
                    choice = group_categories[idx].name
                else:
                    logger.warning("    [yellow]Invalid number: %s. Skipping group assignment.[/yellow]", choice)
                    return None
            if choice in gc_by_name:
                apply_all = input("    Apply to all remaining assignments? [y/N]: ").strip()
                if apply_all.lower() in ('y', 'yes'):
                    self._group_set_for_all = choice
                self._write_group_set_to_frontmatter(file_path, post, choice)
                gc = gc_by_name[choice]
                logger.info("    [green]Updated frontmatter with group_set: %s[/green]", choice)
                return gc.id
            else:
                if choice:
                    logger.warning("    [yellow]\"%s\" not found. Skipping group assignment.[/yellow]", choice)
                else:
                    logger.warning("    [yellow]Skipping group assignment for this file.[/yellow]")
                return None

    @staticmethod
    def _write_group_set_to_frontmatter(file_path, post, group_set_name):
        """Write group_set into the canvas section of the qmd frontmatter."""
        post.metadata.setdefault('canvas', {})['group_set'] = group_set_name
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(frontmatter.dumps(post))
