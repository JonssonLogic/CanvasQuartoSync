import os
import subprocess
import frontmatter
import re
import shutil
from canvasapi import Canvas
from handlers.base_handler import BaseHandler
from handlers.content_utils import process_content, safe_delete_file, safe_delete_dir, get_mapped_id, save_mapped_id, parse_module_name

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
        print(f"Syncing Page: {filename}")
        
        # 1. Check for Skip (Smart Sync)
        current_mtime = os.path.getmtime(file_path)
        existing_id, map_entry = get_mapped_id(content_root, file_path) if content_root else (None, None)
        
        needs_render = True
        page_obj = None

        if existing_id and isinstance(map_entry, dict):
            if map_entry.get('mtime') == current_mtime:
                print(f"    -> Skipping render (No changes detected).")
                needs_render = False
                try:
                    page_obj = course.get_page(existing_id)
                except:
                    print(f"    ! Cached Page ID {existing_id} not found. Re-rendering.")
                    needs_render = True

        # 1b. Parse Metadata (Needed for Module indent even if skipping render)
        post = frontmatter.load(file_path)
        title = post.metadata.get('title', parse_module_name(os.path.splitext(filename)[0]))
        canvas_meta = post.metadata.get('canvas', {})
        published = canvas_meta.get('published', False)
        indent = canvas_meta.get('indent', 0)

        # 1c. Process Content (ALWAYS, to track ACTIVE_ASSET_IDS for pruning)
        with open(file_path, 'r', encoding='utf-8') as f:
            raw_content = f.read()
            
        base_path = os.path.dirname(file_path)
        processed_content = process_content(raw_content, base_path, course, content_root=content_root)

        # Initialize for cleanup safety
        temp_qmd = temp_html = temp_files_dir = None

        if needs_render:
            # 2. Render HTML
            temp_qmd = os.path.join(base_path, f"_temp_{filename}")
            temp_stem = os.path.splitext(f"_temp_{filename}")[0]
            temp_files_dir = os.path.join(base_path, f"{temp_stem}_files")

            try:
                with open(temp_qmd, 'w', encoding='utf-8') as f:
                    f.write(processed_content)
                    
                cmd = ["quarto", "render", temp_qmd, "--to", "html"]
                subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                
                temp_html = temp_qmd.replace('.qmd', '.html')
                if not os.path.exists(temp_html):
                    print(f"    ! Error: Expected HTML output from temp render.")
                    self._cleanup(temp_qmd, None, temp_files_dir)
                    return

                with open(temp_html, 'r', encoding='utf-8') as f:
                    full_html = f.read()
                
                # 3. Extract Content
                main_match = re.search(r'<main[^>]*id="quarto-document-content"[^>]*>(.*?)</main>', full_html, re.DOTALL)
                
                if main_match:
                    html_body = main_match.group(1)
                    html_body = re.sub(r'<header[^>]*id="title-block-header"[^>]*>.*?</header>', '', html_body, flags=re.DOTALL)
                else:
                    html_body = full_html
                    html_body = re.sub(r'<header[^>]*id="title-block-header"[^>]*>.*?</header>', '', html_body, flags=re.DOTALL)
                
                # Cleanup success
                self._cleanup(temp_qmd, temp_html, temp_files_dir)

            except Exception as e:
                print(f"    ! Error processing: {e}")
                self._cleanup(temp_qmd, None, temp_files_dir)
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
                print(f"    -> Updating existing page: {title} (ID: {page_obj.page_id})")
                page_obj.edit(**page_args)
            else:
                # 4b. Double check Title Search if not found by ID
                pages = course.get_pages(search_term=title)
                existing_item = None
                for p in pages:
                    if p.title == title:
                        existing_item = p
                        break
                
                if existing_item:
                    print(f"    -> Updating existing page (by title): {title} (ID: {existing_item.page_id})")
                    existing_item.edit(**page_args)
                    page_obj = existing_item
                else:
                    print(f"    -> Creating new page: {title}")
                    page_obj = course.create_page(**page_args)

            # 4c. Update Sync Map
            if content_root:
                save_mapped_id(content_root, file_path, page_obj.page_id, mtime=current_mtime)
        else:
            # If we didn't need render, page_obj is already set from cache
            pass

        # 5. Add to Module
        if module:
            self.add_to_module(module, {
                'type': 'Page',
                'page_url': page_obj.url,
                'title': page_obj.title,
                'published': published 
            }, indent=indent)

    def _cleanup(self, qmd_path, html_path, files_dir):
        if qmd_path:
            safe_delete_file(qmd_path)
        if html_path:
            safe_delete_file(html_path)
        if files_dir:
            safe_delete_dir(files_dir)
