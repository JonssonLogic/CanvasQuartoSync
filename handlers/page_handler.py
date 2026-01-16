import os
import subprocess
import frontmatter
import re
import shutil
from canvasapi import Canvas
from handlers.base_handler import BaseHandler
from handlers.content_utils import process_content, safe_delete_file, safe_delete_dir, get_mapped_id, save_mapped_id

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
        
        # 1. Parse Metadata & Content
        post = frontmatter.load(file_path)
        title = post.metadata.get('title', os.path.splitext(filename)[0])
        canvas_meta = post.metadata.get('canvas', {})
        published = canvas_meta.get('published', False)
        indent = canvas_meta.get('indent', 0)
        
        # 1b. Process Images AND Links
        with open(file_path, 'r', encoding='utf-8') as f:
            raw_content = f.read()
            
        base_path = os.path.dirname(file_path)
        processed_content = process_content(raw_content, base_path, course)
        
        # ... (Rendering logic remains same) ...
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
        existing_page = None

        # 4a. Try ID lookup via Sync Map
        if content_root:
            mapped_id = get_mapped_id(content_root, file_path)
            if mapped_id:
                try:
                    existing_page = course.get_page(mapped_id)
                except:
                    print(f"    ! Mapped Page ID {mapped_id} not found in Canvas. Falling back to search.")

        # 4b. Fallback to Title Search
        if not existing_page:
            pages = course.get_pages(search_term=title)
            for p in pages:
                if p.title == title:
                    existing_page = p
                    break
        
        page_args = {
            'wiki_page': {
                'title': title,
                'body': html_body,
                'published': published
            }
        }

        if existing_page:
            print(f"    -> Updating existing page: {title} (ID: {existing_page.page_id})")
            existing_page.edit(**page_args)
            page_obj = existing_page
        else:
            print(f"    -> Creating new page: {title}")
            page_obj = course.create_page(**page_args)

        # 4c. Update Sync Map
        if content_root:
            save_mapped_id(content_root, file_path, page_obj.page_id)

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
