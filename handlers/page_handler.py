import os
import subprocess
import frontmatter
import re
import shutil
from canvasapi import Canvas
from canvasapi.exceptions import BadRequest
from handlers.base_handler import BaseHandler
from handlers.content_utils import process_content, safe_delete_file, safe_delete_dir, get_mapped_id, save_mapped_id, parse_module_name
from handlers.drift_detector import check_drift, store_canvas_hash
from handlers.log import logger

class PageHandler(BaseHandler):
    def can_handle(self, file_path: str) -> bool:
        if not file_path.endswith('.qmd'):
            return False
        if os.path.basename(file_path).startswith('_temp_'):
            return False

        try:
            post = frontmatter.load(file_path)
            canvas_meta = post.metadata.get('canvas', {})
            return canvas_meta.get('type') == 'page'
        except:
            return False

    def sync(self, file_path: str, course, module=None, canvas_obj=None, content_root=None):
        filename = os.path.basename(file_path)
        logger.info("  [cyan]Syncing page:[/cyan] [bold]%s[/bold]", filename)

        # 1. Check for Skip (Smart Sync)
        current_mtime = os.path.getmtime(file_path)
        existing_id, map_entry = get_mapped_id(content_root, file_path) if content_root else (None, None)

        needs_render = True
        page_obj = None

        if existing_id and isinstance(map_entry, dict):
            if map_entry.get('mtime') == current_mtime:
                logger.debug("    No changes detected, skipping render")
                needs_render = False
                try:
                    page_obj = course.get_page(existing_id)
                except:
                    logger.warning("    Previously synced page not found in Canvas, re-syncing")
                    needs_render = True

        # 1b. Parse Metadata (Needed for Module indent even if skipping render)
        post = frontmatter.load(file_path)
        title = post.metadata.get('title', parse_module_name(os.path.splitext(filename)[0]))
        canvas_meta = post.metadata.get('canvas', {})
        published = canvas_meta.get('published', False)
        front_page = canvas_meta.get('front_page', False)
        indent = canvas_meta.get('indent', 0)

        # 1c. Process Content (ALWAYS, to track ACTIVE_ASSET_IDS for pruning)
        with open(file_path, 'r', encoding='utf-8') as f:
            raw_content = f.read()

        base_path = os.path.dirname(file_path)
        processed_content = process_content(raw_content, base_path, course, content_root=content_root)

        if needs_render:
            # 2. Render HTML
            html_body = self.render_quarto_document(processed_content, base_path, filename, content_root=content_root)
            if html_body is None:
                return

            # 4. Create/Update Page
            page_args = {
                'wiki_page': {
                    'title': title,
                    'body': html_body,
                    'published': published
                }
            }

            if page_obj: # Found in Canvas but needs update
                # Drift detection: warn if Canvas content was modified outside sync
                if content_root:
                    canvas_body = getattr(page_obj, 'body', '') or ''
                    drift = check_drift(content_root, file_path, canvas_body)
                    if drift['drifted']:
                        logger.warning("    [yellow]DRIFT DETECTED:[/yellow] '%s' was modified on Canvas since last sync. Overwriting with local version.", title)

                logger.info("    [yellow]Updating page:[/yellow] %s", title)
                logger.debug("    Matched by cached ID: %s", page_obj.page_id)
                try:
                    page_obj.edit(**page_args)
                except BadRequest as e:
                    if '"published"' in str(e):
                        logger.warning("    [yellow]Cannot change published state (page may be the course front page). Syncing content only.[/yellow]")
                        page_args['wiki_page'].pop('published', None)
                        page_obj.edit(**page_args)
                    else:
                        raise
            else:
                # 4b. Double check Title Search if not found by ID
                pages = course.get_pages(search_term=title)
                existing_item = None
                for p in pages:
                    if p.title == title:
                        existing_item = p
                        break

                if existing_item:
                    logger.info("    [yellow]Updating page:[/yellow] %s", title)
                    logger.debug("    Matched by title search (ID: %s)", existing_item.page_id)
                    try:
                        existing_item.edit(**page_args)
                    except BadRequest as e:
                        if '"published"' in str(e):
                            logger.warning("    Cannot change published state (page may be the course front page). Syncing content only.")
                            page_args['wiki_page'].pop('published', None)
                            existing_item.edit(**page_args)
                        else:
                            raise
                    page_obj = existing_item
                else:
                    logger.info("    [green]Creating page:[/green] %s", title)
                    page_obj = course.create_page(**page_args)

            # 4c. Update Sync Map and store content hash for drift detection
            if content_root:
                save_mapped_id(content_root, file_path, page_obj.page_id, mtime=current_mtime)
                store_canvas_hash(content_root, file_path, html_body)
        else:
            # If we didn't need render, page_obj is already set from cache
            pass

        # 5. Set as front page
        if front_page and page_obj:
            try:
                page_obj.edit(wiki_page={'front_page': True})
                course.update(course={'default_view': 'wiki'})
                logger.info("    [green]Set as course front page[/green]")
            except Exception as e:
                logger.error("    [red]Failed to set front page:[/red] %s", e)

        # 6. Add to Module
        if module:
            return self.add_to_module(module, {
                'type': 'Page',
                'page_url': page_obj.url,
                'title': page_obj.title,
                'published': published
            }, indent=indent)
