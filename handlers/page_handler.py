import os
import subprocess
import frontmatter
import re
import shutil
from canvasapi import Canvas
from handlers.base_handler import BaseHandler
from handlers.content_utils import process_content

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

    def sync(self, file_path: str, course, module=None):
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
        
        # 1c. Modify Temp Metadata
        # We KEEP the title so Quarto renders it normally (no fallback to filename), 
        # but we will extract only the body content later to remove duplication.
        
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
            # Strategy: Extract <main id="quarto-document-content">...</main>
            # This skips the <header id="title-block-header"> which is usually outside main in 'article' layout
            # or we explicitly remove the header if inside.
            
            # Regex to find the main block
            main_match = re.search(r'<main[^>]*id="quarto-document-content"[^>]*>(.*?)</main>', full_html, re.DOTALL)
            
            if main_match:
                html_body = main_match.group(1)
                # Just in case title block is INSIDE main, strip it too.
                html_body = re.sub(r'<header[^>]*id="title-block-header"[^>]*>.*?</header>', '', html_body, flags=re.DOTALL)
            else:
                # Fallback: Use full body but try to strip header
                print("    ! Warning: Could not find main content block, using full body.")
                html_body = full_html
                html_body = re.sub(r'<header[^>]*id="title-block-header"[^>]*>.*?</header>', '', html_body, flags=re.DOTALL)
            
            # Cleanup success
            self._cleanup(temp_qmd, temp_html, temp_files_dir)

        except subprocess.CalledProcessError as e:
            print(f"    ! Error rendering Quarto: {e.stderr.decode()}")
            self._cleanup(temp_qmd, None, temp_files_dir)
            return
        except Exception as e:
            print(f"    ! Error processing: {e}")
            self._cleanup(temp_qmd, None, temp_files_dir)
            return

        # 4. Create/Update Page
        existing_page = None
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
            print(f"    -> Updating existing page: {title}")
            existing_page.edit(**page_args)
            page_obj = existing_page
        else:
            print(f"    -> Creating new page: {title}")
            page_obj = course.create_page(**page_args)

        # 5. Add to Module
        if module:
            self.add_to_module(module, {
                'type': 'Page',
                'page_url': page_obj.url,
                'title': page_obj.title,
                'published': published 
            }, indent=indent)

    def _cleanup(self, qmd_path, html_path, files_dir):
        try:
            if qmd_path and os.path.exists(qmd_path):
                os.remove(qmd_path)
            if html_path and os.path.exists(html_path):
                os.remove(html_path)
            if files_dir and os.path.exists(files_dir):
                shutil.rmtree(files_dir)
        except Exception as e:
            print(f"    ! Warning: Cleanup failed: {e}")
