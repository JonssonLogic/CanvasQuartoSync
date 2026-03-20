from abc import ABC, abstractmethod
import os
import subprocess
import re
from handlers.content_utils import safe_delete_file, safe_delete_dir
from handlers.log import logger

class BaseHandler(ABC):
    """
    Abstract base class for all synchronization handlers.
    """

    @abstractmethod
    def can_handle(self, file_path: str) -> bool:
        pass

    @abstractmethod
    def sync(self, file_path: str, course, module=None, canvas_obj=None, content_root=None):
        pass

    def add_to_module(self, module, item_dict, indent=0):
        """
        Helper to add or update an item in a module with indentation support.

        Args:
            module: The canvasapi.Module object.
            item_dict: Dictionary containing 'type', 'content_id' (or 'page_url'), 'title', and 'published'.
            indent: Integer (0-5) for indentation level.
        """
        title = item_dict.get('title')
        item_type = item_dict.get('type')
        content_id = item_dict.get('content_id')
        page_url = item_dict.get('page_url')
        external_url = item_dict.get('external_url')
        new_tab = item_dict.get('new_tab', False)
        published = item_dict.get('published') # Optional, might be None

        # Validate indent
        indent = max(0, min(5, int(indent)))

        items = module.get_module_items()

        existing_item = None
        for item in items:
            if item.type != item_type:
                continue

            # Match Logic
            match = False
            if item_type == 'Page' and item.page_url == page_url:
                match = True
            elif item_type == 'SubHeader' and item.title == title:
                match = True
            elif item_type in ['Assignment', 'Quiz', 'File']:
                try:
                    if int(item.content_id) == int(content_id):
                        match = True
                except (ValueError, TypeError):
                    if str(item.content_id) == str(content_id):
                        match = True
            elif item_type == 'ExternalUrl' and getattr(item, 'external_url', None) == external_url:
                match = True

            if match:
                existing_item = item
                break
        if existing_item:
            logger.debug("    Module item found: %s", title)
            updates = {}

            # Check Title
            if existing_item.title != title:
                logger.debug("      Updating title: %s -> %s", existing_item.title, title)
                updates['title'] = title

            # Check Indent
            if existing_item.indent != indent:
                logger.debug("      Updating indent: %s -> %s", existing_item.indent, indent)
                updates['indent'] = indent

            # Check Published (If provided)
            # Note: SubHeaders rely on this heavily.
            if published is not None and getattr(existing_item, 'published', None) != published:
                logger.debug("      Updating published: %s", published)
                updates['published'] = published

            if updates:
                existing_item.edit(module_item=updates)
            return existing_item
        else:
            logger.info("    [green]Adding to module:[/green] %s", module.name)
            payload = {
                'type': item_type,
                'title': title,
                'indent': indent
            }
            if content_id:
                payload['content_id'] = content_id
            if page_url:
                payload['page_url'] = page_url
            if external_url:
                payload['external_url'] = external_url
            if new_tab:
                payload['new_tab'] = True
            # Note: Canvas API ignores 'published' during create, so we don't include it here

            new_item = module.create_module_item(module_item=payload)

            # Canvas API ignores 'published' during creation, so we must update it separately
            if published is not None:
                logger.debug("      Setting published: %s", published)
                new_item.edit(module_item={'published': published})
            return new_item

    def _cleanup(self, qmd_path, html_path, files_dir):
        """Clean up temporary files from Quarto render."""
        if qmd_path:
            safe_delete_file(qmd_path)
        if html_path:
            safe_delete_file(html_path)
        if files_dir:
            safe_delete_dir(files_dir)

    def render_quarto_pdf(self, processed_content, base_path, filename):
        """
        Renders a processed QMD document to PDF via Quarto.
        Returns the path to the rendered PDF file, or None on failure.
        The caller is responsible for deleting the PDF after use.
        """
        temp_qmd = os.path.join(base_path, f"_temp_pdf_{filename}")
        temp_stem = os.path.splitext(f"_temp_pdf_{filename}")[0]
        temp_files_dir = os.path.join(base_path, f"{temp_stem}_files")
        temp_pdf = os.path.join(base_path, f"{temp_stem}.pdf")

        try:
            with open(temp_qmd, 'w', encoding='utf-8') as f:
                f.write(processed_content)

            cmd = ["quarto", "render", temp_qmd, "--to", "pdf"]
            subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

            if not os.path.exists(temp_pdf):
                logger.error("    Expected PDF output from Quarto render but file not found")
                self._cleanup(temp_qmd, None, temp_files_dir)
                return None

            # Clean up temp QMD and auxiliary files, but keep the PDF
            self._cleanup(temp_qmd, None, temp_files_dir)
            return temp_pdf

        except subprocess.CalledProcessError as e:
            stderr_text = e.stderr.decode('utf-8', errors='replace') if e.stderr else ''
            if 'tinytex' in stderr_text.lower() or 'latex' in stderr_text.lower() or 'pdflatex' in stderr_text.lower():
                logger.error("    PDF render failed: LaTeX not found. Install with: quarto install tinytex")
            else:
                logger.error("    PDF render failed: %s", stderr_text or e)
            self._cleanup(temp_qmd, None, temp_files_dir)
            return None
        except Exception as e:
            logger.error("    PDF render failed: %s", e)
            self._cleanup(temp_qmd, None, temp_files_dir)
            return None

    def render_quarto_document(self, processed_content, base_path, filename):
        """
        Renders a processed QMD document to HTML via Quarto.
        Extracts the <main> content block and cleans up temp files.
        """
        temp_qmd = os.path.join(base_path, f"_temp_{filename}")
        temp_stem = os.path.splitext(f"_temp_{filename}")[0]
        temp_files_dir = os.path.join(base_path, f"{temp_stem}_files")
        temp_html = None

        try:
            with open(temp_qmd, 'w', encoding='utf-8') as f:
                f.write(processed_content)

            cmd = ["quarto", "render", temp_qmd, "--to", "html"]
            subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

            temp_html = temp_qmd.replace('.qmd', '.html')

            if not os.path.exists(temp_html):
                 logger.error("    Expected HTML output from Quarto render but file not found")
                 self._cleanup(temp_qmd, None, temp_files_dir)
                 return None

            with open(temp_html, 'r', encoding='utf-8') as f:
                full_html = f.read()

            # Extract Content
            main_match = re.search(r'<main[^>]*id="quarto-document-content"[^>]*>(.*?)</main>', full_html, re.DOTALL)

            if main_match:
                html_body = main_match.group(1)
                html_body = re.sub(r'<header[^>]*id="title-block-header"[^>]*>.*?</header>', '', html_body, flags=re.DOTALL)
            else:
                html_body = full_html
                html_body = re.sub(r'<header[^>]*id="title-block-header"[^>]*>.*?</header>', '', html_body, flags=re.DOTALL)

            # Cleanup
            self._cleanup(temp_qmd, temp_html, temp_files_dir)
            return html_body

        except Exception as e:
            logger.error("    Quarto render failed: %s", e)
            self._cleanup(temp_qmd, None, temp_files_dir)
            return None
