import os
import subprocess
import frontmatter
import re
import shutil
from datetime import datetime
from canvasapi import Canvas
from handlers.base_handler import BaseHandler
from handlers.content_utils import process_content, safe_delete_file, safe_delete_dir, get_mapped_id, save_mapped_id, parse_module_name

class AssignmentHandler(BaseHandler):
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
        print(f"Syncing Assignment: {filename}")
        
        # 1. Check for Skip (Smart Sync)
        current_mtime = os.path.getmtime(file_path)
        existing_id, map_entry = get_mapped_id(content_root, file_path) if content_root else (None, None)
        
        needs_render = True
        assign_obj = None

        if existing_id and isinstance(map_entry, dict):
            if map_entry.get('mtime') == current_mtime:
                print(f"    -> Skipping render (No changes detected).")
                needs_render = False
                try:
                    assign_obj = course.get_assignment(existing_id)
                except:
                    print(f"    ! Cached Assignment ID {existing_id} not found. Re-rendering.")
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
        indent = canvas_meta.get('indent', 0)

        # 1c. Process Content (ALWAYS, to track ACTIVE_ASSET_IDS)
        with open(file_path, 'r', encoding='utf-8') as f:
            raw_content = f.read()
            
        base_path = os.path.dirname(file_path)
        processed_content = process_content(raw_content, base_path, course, content_root=content_root)

        if needs_render:
            # 2. Render HTML
            html_body = self.render_quarto_document(processed_content, base_path, filename)
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
                'omit_from_final_grade': omit_from_final_grade
            }

            if assign_obj:
                print(f"    -> Updating assignment: {title} (ID: {assign_obj.id})")
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
                    print(f"    -> Updating assignment (by title): {title} (ID: {existing_item.id})")
                    existing_item.edit(assignment=assignment_args)
                    assign_obj = existing_item
                else:
                    print(f"    -> Creating assignment: {title}")
                    assign_obj = course.create_assignment(assignment=assignment_args)

            # 4c. Update Sync Map
            if content_root:
                save_mapped_id(content_root, file_path, assign_obj.id, mtime=current_mtime)
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

