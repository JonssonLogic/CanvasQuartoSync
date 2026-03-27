import os
import shutil
import frontmatter
from canvasapi.exceptions import BadRequest
from handlers.base_handler import BaseHandler
from handlers.content_utils import (
    process_content, upload_file, get_mapped_id, save_mapped_id,
    load_sync_map, save_sync_map, parse_module_name, safe_delete_file,
    FOLDER_FILES, ACTIVE_ASSET_IDS
)
from handlers.drift_detector import check_drift, store_canvas_hash
from handlers.log import logger


class StudyGuideHandler(BaseHandler):
    def can_handle(self, file_path: str) -> bool:
        if not file_path.endswith('.qmd'):
            return False
        basename = os.path.basename(file_path)
        if basename.startswith(('_temp_', 'tmp-')):
            return False

        # Match by filename (case-insensitive): *StudyGuide* or *KursPM*
        name_lower = basename.lower()
        if 'studyguide' in name_lower or 'kurspm' in name_lower:
            return True

        # Match by frontmatter
        try:
            post = frontmatter.load(file_path)
            canvas_meta = post.metadata.get('canvas', {})
            return canvas_meta.get('type') == 'study_guide'
        except:
            return False

    def sync(self, file_path: str, course, module=None, canvas_obj=None, content_root=None):
        filename = os.path.basename(file_path)
        logger.info("  [cyan]Syncing study guide:[/cyan] [bold]%s[/bold]", filename)

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

                # Track cached PDF as active to prevent orphan pruning
                pdf_file_id = map_entry.get('pdf_file_id')
                if pdf_file_id:
                    ACTIVE_ASSET_IDS.add(pdf_file_id)

        # 2. Parse Metadata
        post = frontmatter.load(file_path)
        title = post.metadata.get('title', parse_module_name(os.path.splitext(filename)[0]))
        canvas_meta = post.metadata.get('canvas', {})
        published = canvas_meta.get('published', False)
        front_page = canvas_meta.get('front_page', False)
        indent = canvas_meta.get('indent', 0)

        pdf_config = canvas_meta.get('pdf', {})
        pdf_target_module_name = pdf_config.get('target_module')
        pdf_filename = pdf_config.get('filename', f"{title}.pdf")
        pdf_title = pdf_config.get('title', pdf_filename)
        pdf_published = pdf_config.get('published', False)

        # Default target module for study guides detected by filename
        if not pdf_target_module_name:
            if module:
                pdf_target_module_name = module.name
                logger.debug("    No 'canvas.pdf.target_module' set, defaulting to current module: %s", module.name)
            else:
                logger.warning("    [yellow]No 'canvas.pdf.target_module' set and no module context. PDF will be uploaded but not added to a module.[/yellow]")

        # 3. Process Content (ALWAYS, to track ACTIVE_ASSET_IDS for pruning)
        with open(file_path, 'r', encoding='utf-8') as f:
            raw_content = f.read()

        # 3a. Preprocess study guide (expand minimal QMD into dual-format styled QMD)
        if canvas_meta.get('preprocess'):
            from handlers.qmd_preprocessor import preprocess_study_guide
            from handlers.config import load_config
            config = load_config(content_root) if content_root else {}
            config_dir = content_root or os.path.dirname(file_path)
            raw_content = preprocess_study_guide(raw_content, config, config_dir=config_dir)
            logger.debug("    Preprocessed study guide with dual-format styling")

        base_path = os.path.dirname(file_path)
        processed_content = process_content(raw_content, base_path, course, content_root=content_root)

        if needs_render:
            # 4. Render HTML
            html_body = self.render_quarto_document(processed_content, base_path, filename, content_root=content_root)
            if html_body is None:
                return

            # 5. Render PDF
            pdf_path = None
            pdf_file_id = None
            pdf_file_url = None

            pdf_path = self.render_quarto_pdf(raw_content, base_path, filename)
            if pdf_path is None:
                logger.warning("    [yellow]PDF render failed — syncing HTML page only.[/yellow]")

            # 6. Create/Update Canvas Page
            page_args = {
                'wiki_page': {
                    'title': title,
                    'body': html_body,
                    'published': published
                }
            }

            if page_obj:
                # Drift detection
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
                # Title search fallback
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

            # 7. Upload PDF (rename to desired filename first)
            if pdf_path:
                renamed_pdf = os.path.join(os.path.dirname(pdf_path), pdf_filename)
                try:
                    shutil.move(pdf_path, renamed_pdf)
                    pdf_file_url, pdf_file_id = upload_file(course, renamed_pdf, FOLDER_FILES, content_root=content_root)
                except Exception as e:
                    logger.error("    [red]PDF upload failed:[/red] %s", e)
                finally:
                    safe_delete_file(renamed_pdf)
                    if os.path.exists(pdf_path):
                        safe_delete_file(pdf_path)

            # 8. Update Sync Map and store content hash for drift detection
            if content_root:
                save_mapped_id(content_root, file_path, page_obj.page_id, mtime=current_mtime)
                store_canvas_hash(content_root, file_path, html_body)
                if pdf_file_id:
                    sync_map = load_sync_map(content_root)
                    rel_path = os.path.relpath(file_path, content_root).replace('\\', '/')
                    sync_map[rel_path]['pdf_file_id'] = pdf_file_id
                    sync_map[rel_path]['pdf_url'] = pdf_file_url
                    save_sync_map(content_root, sync_map)

            # 9. Add PDF to target module
            if pdf_file_id and pdf_target_module_name:
                target_module = self._resolve_module(course, pdf_target_module_name)
                if target_module:
                    self.add_to_module(target_module, {
                        'type': 'File',
                        'content_id': pdf_file_id,
                        'title': pdf_title,
                        'published': pdf_published
                    })

        # 10. Set as front page
        if front_page and page_obj:
            try:
                page_obj.edit(wiki_page={'front_page': True})
                course.update(course={'default_view': 'wiki'})
                logger.info("    [green]Set as course front page[/green]")
            except Exception as e:
                logger.error("    [red]Failed to set front page:[/red] %s", e)

        # 11. Add HTML page to current module
        if module and page_obj:
            return self.add_to_module(module, {
                'type': 'Page',
                'page_url': page_obj.url,
                'title': page_obj.title,
                'published': published
            }, indent=indent)

    def _resolve_module(self, course, module_name):
        """Find an existing module by name, or create it."""
        try:
            modules = course.get_modules(search_term=module_name)
            for m in modules:
                if m.name == module_name:
                    return m
            # Not found — create it
            logger.info("    [green]Creating target module:[/green] %s", module_name)
            return course.create_module(module={'name': module_name})
        except Exception as e:
            logger.error("    [red]Failed to resolve target module '%s':[/red] %s", module_name, e)
            return None
