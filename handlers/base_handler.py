from abc import ABC, abstractmethod

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
            elif item_type in ['Assignment', 'Quiz', 'File'] and item.content_id == content_id:
                match = True
            
            if match:
                existing_item = item
                break
        if existing_item:
            print(f"    -> Module Item found: {title}")
            updates = {}
            
            # Check Title
            if existing_item.title != title:
                print(f"       Updating title: {existing_item.title} -> {title}")
                updates['title'] = title
            
            # Check Indent
            if existing_item.indent != indent:
                print(f"       Updating indent: {existing_item.indent} -> {indent}")
                updates['indent'] = indent
            
            # Check Published (If provided)
            # Note: SubHeaders rely on this heavily.
            if published is not None and getattr(existing_item, 'published', None) != published:
                print(f"       Updating published: {published}")
                updates['published'] = published
                
            if updates:
                existing_item.edit(module_item=updates)
            return existing_item
        else:
            print(f"    -> Adding to module: {module.name} (Indent: {indent})")
            payload = {
                'type': item_type,
                'title': title,
                'indent': indent
            }
            if content_id:
                payload['content_id'] = content_id
            if page_url:
                payload['page_url'] = page_url
            # Note: Canvas API ignores 'published' during create, so we don't include it here
                
            new_item = module.create_module_item(module_item=payload)
            
            # Canvas API ignores 'published' during creation, so we must update it separately
            if published is not None:
                print(f"       Setting published: {published}")
                new_item.edit(module_item={'published': published})
            return new_item
