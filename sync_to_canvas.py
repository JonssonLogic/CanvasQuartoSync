import os
import argparse
import sys
import re
from canvasapi import Canvas

# Add current directory to sys.path to ensure we can find handlers if running from elsewhere?
# Actually, if we run the script, its directory is in path.

from handlers.base_handler import BaseHandler
from handlers.page_handler import PageHandler
from handlers.assignment_handler import AssignmentHandler
from handlers.quiz_handler import QuizHandler
from handlers.calendar_handler import CalendarHandler
from handlers.subheader_handler import SubHeaderHandler
from handlers.content_utils import upload_file, prune_orphaned_assets, FOLDER_FILES, parse_module_name

# --- Configuration ---
API_URL = os.environ.get("CANVAS_API_URL")
API_TOKEN = os.environ.get("CANVAS_API_TOKEN")

def is_valid_name(name):
    """
    Checks if the name starts with exactly two digits followed by an underscore.
    Example: '01_Intro' -> True, 'Intro' -> False, '1_Intro' -> False
    """
    return bool(re.match(r'^\d{2}_', name))


def get_course_id(content_root, arg_course_id):
    """
    Determines the Course ID.
    Priority:
    1. CLI Argument (--course-id)
    2. File 'course_id.txt' in content_root
    """
    if arg_course_id:
        return arg_course_id
    
    file_path = os.path.join(content_root, "course_id.txt")
    if os.path.exists(file_path):
        try:
            with open(file_path, 'r') as f:
                cid = f.read().strip()
                if cid:
                    print(f"Found Course ID in file: {cid}")
                    return cid
        except Exception as e:
            print(f"Error reading course_id.txt: {e}")
            
    return None

def main():
    parser = argparse.ArgumentParser(description="Sync local content to Canvas.")
    parser.add_argument("content_path", nargs="?", default=".", help="Path to the content directory (default: current dir).")
    parser.add_argument("--sync-calendar", action="store_true", help="Enable calendar synchronization (Opt-in).")
    parser.add_argument("--course-id", help="Canvas Course ID (Override).")
    args = parser.parse_args()

    # Helper to resolve paths relative to content_path
    content_root = os.path.abspath(args.content_path)
    if not os.path.exists(content_root):
        print(f"Error: Content directory not found: {content_root}")
        return

    print(f"Target Content Directory: {content_root}")

    # Resolve Context
    course_id = get_course_id(content_root, args.course_id)

    if not API_URL or not API_TOKEN:
         print("Error: CANVAS_API_URL and CANVAS_API_TOKEN environment variables match be set.")
         return

    if not course_id:
        print("Error: Course ID not specified.")
        print("Please provide it via --course-id argument or Place a 'course_id.txt' file in the content directory.")
        return

    print("Connecting to Canvas...")
    try:
        canvas = Canvas(API_URL, API_TOKEN)
        course = canvas.get_course(course_id)
        print(f"Connected to course: {course.name} (ID: {course.id})")
    except Exception as e:
        print(f"Connection failed: {e}")
        return

    # Handlers
    handlers = [
        PageHandler(),
        AssignmentHandler(),
        QuizHandler(),
        SubHeaderHandler()
    ]
    
    # Calendar Sync (Opt-in)
    if args.sync_calendar:
        print(">> Starting Calendar Sync...")
        cal_handler = CalendarHandler()
        schedule_path = os.path.join(content_root, "schedule.yaml")
        try:
            cal_handler.sync(schedule_path, course, canvas_obj=canvas)
        except FileNotFoundError:
            print(f"   (No schedule.yaml found at {schedule_path})")
        except Exception as e:
            print(f"   Error in calendar sync: {e}")
    else:
        print(">> Skipping Calendar Sync (Use --sync-calendar to enable)")

    print(">> Starting Content Sync...")
    
    # 1. Walk the directory
    # Sort ensure robust ordering
    items = sorted(os.listdir(content_root))
    
    for item in items:
        item_path = os.path.join(content_root, item)
        
        # Case A: Module Directory
        if os.path.isdir(item_path):
            if not is_valid_name(item):
                continue
            
            # This is a Module directory
            module_name = parse_module_name(item)
            print(f"Processing module: {module_name} ({item})")
            
            # Find or Create Module in Canvas
            module_obj = None
            try:
                # Helper to find module
                modules = course.get_modules(search_term=module_name)
                for m in modules:
                    if m.name == module_name:
                        module_obj = m
                        break
                
                if not module_obj:
                    print(f"  -> Creating new module: {module_name}")
                    module_obj = course.create_module(module={'name': module_name})
                else:
                    print(f"  -> Found existing module: {module_name} (ID: {module_obj.id})")

                # Walk files inside the module
                module_files = sorted(os.listdir(item_path))
                
                # Track synced items for reordering
                synced_module_items = []
                
                for filename in module_files:
                    file_path = os.path.join(item_path, filename)
                    
                    if os.path.isdir(file_path): 
                        continue

                    if not is_valid_name(filename):
                        continue
                    
                    # Delegation Logic
                    handled = False
                    for handler in handlers:
                        if handler.can_handle(file_path):
                            try:
                                item = handler.sync(file_path, course, module_obj, canvas_obj=canvas, content_root=content_root)
                                if item:
                                    synced_module_items.append(item)
                            except Exception as e:
                                print(f"    ERROR syncing {filename}: {e}")
                                import traceback
                                traceback.print_exc()
                            handled = True
                            break
                    
                    if not handled:
                        # Case C: Solo Asset (PDF, ZIP, etc) with NN_ prefix in Module
                        ext = os.path.splitext(filename)[1].lower()
                        print(f"    (Non-content file detected: {filename})")
                        
                        # Upload to namespaced folder
                        file_url, file_id = upload_file(course, file_path, FOLDER_FILES, content_root=content_root)
                        
                        if file_id and module_obj:
                            # Add to module as File item
                            # We use handlers[0] (or any handler) to access the add_to_module helper
                            item = handlers[0].add_to_module(module_obj, {
                                'type': 'File',
                                'content_id': file_id,
                                'title': parse_module_name(filename),
                                'published': True
                            })
                            if item:
                                synced_module_items.append(item)

                # Reorder Module Items
                if synced_module_items:
                    print(f"  -> Verifying module item order ({len(synced_module_items)} items)...")
                    for i, mod_item in enumerate(synced_module_items):
                        expected_position = i + 1
                        if mod_item.position != expected_position:
                            print(f"     [Reorder] Moving '{mod_item.title}' to position {expected_position} (was {mod_item.position})")
                            try:
                                mod_item.edit(module_item={'position': expected_position})
                                mod_item.position = expected_position 
                            except Exception as e:
                                print(f"     ! Error reordering item {mod_item.title}: {e}")

            except Exception as e:
                 print(f"  Error processing module {module_name}: {e}")

        # Case B: Root File (No Module)
        elif os.path.isfile(item_path):
             if not is_valid_name(item):
                 continue
             
             # Delegation Logic
             handled = False
             for handler in handlers:
                # Skip SubHeaders in root (doesn't make sense without a module)
                if isinstance(handler, SubHeaderHandler):
                    continue

                if handler.can_handle(item_path):
                    # print(f"Syncing root item: {item}")
                    try:
                        # Pass module=None
                        handler.sync(item_path, course, module=None, canvas_obj=canvas, content_root=content_root)
                    except Exception as e:
                        print(f"    ERROR syncing root item {item}: {e}")
                        import traceback
                        traceback.print_exc()
                    handled = True
                    break

    # 3. Cleanup Orphans
    prune_orphaned_assets(course)

if __name__ == "__main__":
    main()
