import frontmatter
import os
from handlers.base_handler import BaseHandler
from handlers.content_utils import parse_module_name

class ExternalLinkHandler(BaseHandler):
    def can_handle(self, file_path: str) -> bool:
        if not file_path.endswith('.qmd'):
            return False
        
        try:
            post = frontmatter.load(file_path)
            canvas_meta = post.metadata.get('canvas', {})
            return canvas_meta.get('type') == 'external_url'
        except:
            return False

    def sync(self, file_path: str, course, module=None, canvas_obj=None, content_root=None):
        filename = os.path.basename(file_path)
        post = frontmatter.load(file_path)
        title = post.metadata.get('title')
        if not title:
            title = parse_module_name(os.path.splitext(filename)[0])
        canvas_meta = post.metadata.get('canvas', {})
        published = canvas_meta.get('published', False)
        indent = canvas_meta.get('indent', 0)
        url = canvas_meta.get('url')
        new_tab = canvas_meta.get('new_tab', False)

        if not url:
            print(f"    ! Error: External link '{title}' is missing 'canvas.url' in frontmatter.")
            return

        print(f"Syncing External Link: {title} -> {url}")

        if not module:
            print(f"    ! Skipping: External links can only be added to a module.")
            return

        return self.add_to_module(module, {
            'type': 'ExternalUrl',
            'title': title,
            'external_url': url,
            'new_tab': new_tab,
            'published': published
        }, indent=indent)
