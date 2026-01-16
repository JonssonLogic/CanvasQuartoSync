import frontmatter
import os
from handlers.base_handler import BaseHandler

class SubHeaderHandler(BaseHandler):
    def can_handle(self, file_path: str) -> bool:
        if not file_path.endswith('.md'):
            return False
        
        try:
            post = frontmatter.load(file_path)
            canvas_meta = post.metadata.get('canvas', {})
            return canvas_meta.get('type') == 'subheader'
        except:
            return False

    def sync(self, file_path: str, course, module=None, canvas_obj=None, content_root=None):
        filename = os.path.basename(file_path)
        post = frontmatter.load(file_path)
        title = post.metadata.get('title', os.path.splitext(filename)[0])
        canvas_meta = post.metadata.get('canvas', {})
        published = canvas_meta.get('published', True) 
        indent = canvas_meta.get('indent', 0)
        
        print(f"Syncing SubHeader: {title}")

        if not module:
            return

        self.add_to_module(module, {
            'type': 'SubHeader',
            'title': title,
            'published': published
        }, indent=indent)
