import os
import re
import frontmatter
import json
from canvasapi import Canvas

# Global cache for folder names to IDs to avoid redundant API lookups
FOLDER_CACHE = {}

# System-managed namespaces for orphan cleanup
FOLDER_IMAGES = "synced-images"
FOLDER_FILES = "synced-files"

# Global set of asset IDs currently referenced in synced content
ACTIVE_ASSET_IDS = set()

def get_or_create_folder(course, folder_path, parent_folder_id=None):
    """
    Finds or creates a folder in the Canvas course files.
    """
    global FOLDER_CACHE
    
    # 1. Check cache first (normalized)
    cache_key = folder_path.lower()
    if cache_key in FOLDER_CACHE:
        return FOLDER_CACHE[cache_key]

    # 2. Fetch all folders and populate cache if empty
    # We do this once per run mostly
    print(f"    Checking for folder: {folder_path}...")
    folders = course.get_folders()
    for f in folders:
        FOLDER_CACHE[f.name.lower()] = f
    
    # 3. Check cache again after populating
    if cache_key in FOLDER_CACHE:
        return FOLDER_CACHE[cache_key]
    
    # 4. Not found, create
    print(f"    + Creating folder: {folder_path}")
    try:
        if parent_folder_id:
            new_folder = course.create_folder(name=folder_path, parent_folder_id=parent_folder_id)
        else:
            new_folder = course.create_folder(name=folder_path)
            
        FOLDER_CACHE[new_folder.name.lower()] = new_folder
        return new_folder
    except Exception as e:
        print(f"    ! Error creating folder '{folder_path}': {e}")
        raise

def upload_file(course, local_path, target_folder_name="course_files", content_root=None):
    """
    Uploads a file (image or asset) to Canvas.
    Returns a tuple (file_url, file_id).
    """
    global ACTIVE_ASSET_IDS
    if not os.path.exists(local_path):
        print(f"    ! File not found: {local_path}")
        return local_path, None
    
    filename = os.path.basename(local_path)
    
    # Smart Upload Logic: Check Sync Map for mtime match
    mtime = os.path.getmtime(local_path)
    if content_root:
        sync_map = load_sync_map(content_root)
        rel_path = os.path.relpath(local_path, content_root).replace('\\', '/')
        cached_entry = sync_map.get(rel_path)
        
        if isinstance(cached_entry, dict):
            # We strictly check url too in case it failed previously
            if cached_entry.get('mtime') == mtime and cached_entry.get('url'):
                # Track as active even if cached
                asset_id = cached_entry.get('id')
                if asset_id:
                    ACTIVE_ASSET_IDS.add(asset_id)
                return cached_entry.get('url'), asset_id
    
    folder = get_or_create_folder(course, target_folder_name)
    
    print(f"    -> Uploading file: {filename}")
    try:
        success, json_response = folder.upload(local_path, on_duplicate='overwrite')
        if success:
            file_url = json_response.get('url')
            file_id = json_response.get('id')
            
            # Track as active
            if file_id:
                ACTIVE_ASSET_IDS.add(file_id)

            # Update Sync Map
            if content_root:
                sync_map = load_sync_map(content_root)
                rel_path = os.path.relpath(local_path, content_root).replace('\\', '/')
                sync_map[rel_path] = {
                    'mtime': mtime,
                    'url': file_url,
                    'id': file_id
                }
                save_sync_map(content_root, sync_map)
                
            return file_url, file_id
        else:
            print("    ! Upload failed.")
            return local_path, None
    except Exception as e:
        print(f"    ! Error uploading file: {e}")
        return local_path, None

def resolve_cross_link(course, current_file_path, link_target, base_path):
    """
    Resolves a link to a local content file (.qmd, .json) into a Canvas object URL.
    Implements JIT Stubbing: Creates the target object if it doesn't exist.
    """
    # 1. Resolve absolute local path
    if os.path.isabs(link_target):
        abs_target_path = link_target
    else:
        abs_target_path = os.path.normpath(os.path.join(base_path, link_target))

    if not os.path.exists(abs_target_path):
        print(f"    ! Link target not found: {link_target}")
        return link_target

    filename = os.path.basename(abs_target_path)
    ext = os.path.splitext(filename)[1].lower()

    # 2. Determine Title and Type
    target_title = None
    target_type = None

    try:
        if ext == '.qmd':
            post = frontmatter.load(abs_target_path)
            # Default title from frontmatter or filename
            target_title = post.metadata.get('title', os.path.splitext(filename)[0])
            canvas_meta = post.metadata.get('canvas', {})
            target_type = canvas_meta.get('type', 'page') # Default to page if unspecified

        elif ext == '.json':
            # Assume Quiz
            with open(abs_target_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            target_title = data.get('canvas', {}).get('title', os.path.splitext(filename)[0])
            target_type = 'quiz'
            
        else:
            # Unknown content type, treat as file upload?
            # But we only call this for known extensions usually.
            return link_target

    except Exception as e:
        print(f"    ! Error parsing target {filename}: {e}")
        return link_target

    # 3. Find or Create Stub in Canvas
    # We need to find the object by title.
    
    canvas_url = link_target # Fallback

    if target_type == 'page':
        # Finding pages is tricky because 'url' in API != 'title'. 
        # But get_pages(search_term=title) works mostly.
        pages = course.get_pages(search_term=target_title)
        target_obj = None
        for p in pages:
            if p.title == target_title:
                target_obj = p
                break
        
        if not target_obj:
            print(f"    + [Stub] Creating Page: {target_title}")
            target_obj = course.create_page(wiki_page={'title': target_title, 'published': False, 'body': '<i>Placeholder for future sync.</i>'})
        
        # HTML URL is needed. 'html_url' property usually exists.
        canvas_url = target_obj.html_url
        
    elif target_type == 'assignment':
        assignments = course.get_assignments(search_term=target_title)
        target_obj = None
        for a in assignments:
            if a.name == target_title:
                target_obj = a
                break
        
        if not target_obj:
            print(f"    + [Stub] Creating Assignment: {target_title}")
            target_obj = course.create_assignment(assignment={'name': target_title, 'published': False})
            
        canvas_url = target_obj.html_url

    elif target_type == 'quiz':
        quizzes = course.get_quizzes(search_term=target_title)
        target_obj = None
        for q in quizzes:
            if q.title == target_title:
                target_obj = q
                break
        
        if not target_obj:
             print(f"    + [Stub] Creating Quiz: {target_title}")
             target_obj = course.create_quiz(quiz={'title': target_title, 'published': False, 'quiz_type': 'assignment'})
        
        canvas_url = target_obj.html_url

    return canvas_url

def process_content(content, base_path, course, content_root=None):
    """
    Main entry point. Scans for images AND file/content links.
    """
    
    # --- 1. Process Images (![...](...)) ---
    def image_replacer(match):
        alt_text = match.group(1)
        rel_path = match.group(2)
        
        if rel_path.startswith(('http://', 'https://', 'data:')):
            return match.group(0)
            
        if os.path.isabs(rel_path):
            abs_path = rel_path
        else:
            abs_path = os.path.normpath(os.path.join(base_path, rel_path))
            
        # Upload to namespaced folder
        new_url, _ = upload_file(course, abs_path, FOLDER_IMAGES, content_root=content_root)
        
        return f"![{alt_text}]({new_url})"

    # Regex for images
    content = re.sub(r'!\[(.*?)\]\((.*?)\)', image_replacer, content)

    # --- 2. Process File/Content Links ([...](...)) ---
    def link_replacer(match):
        link_text = match.group(1)
        rel_path = match.group(2)
        
        if rel_path.startswith(('http://', 'https://', 'data:', '#', 'mailto:')):
            return match.group(0)

        if os.path.isabs(rel_path):
            abs_path = rel_path
        else:
            abs_path = os.path.normpath(os.path.join(base_path, rel_path))
            
        ext = os.path.splitext(rel_path)[1].lower()

        content_extensions = ['.qmd', '.json'] 
        
        if ext in content_extensions:
            new_url = resolve_cross_link(course, os.path.join(base_path, "current_context"), rel_path, base_path)
            return f"[{link_text}]({new_url})"
        else:
            # Asset Upload (PDF, ZIP, DOCX, IPYNB, etc)
            new_url, _ = upload_file(course, abs_path, FOLDER_FILES, content_root=content_root)
            return f"[{link_text}]({new_url})"
            
    pattern_links = r'(?<!\!)\[(.*?)\]\((.*?)\)'
    content = re.sub(pattern_links, link_replacer, content)
    
    return content

import time
import shutil

def safe_delete_file(path, retries=5, delay=0.5):
    """
    Attempts to delete a file multiple times if it's locked by another process.
    """
    if not os.path.exists(path):
        return

    for i in range(retries):
        try:
            os.remove(path)
            # print(f"    - Deleted file: {os.path.basename(path)}")
            return
        except PermissionError:
            if i < retries - 1:
                # print(f"    ! File locked, retrying {i+1}/{retries}...")
                time.sleep(delay)
            else:
                print(f"    ! Final Error: Could not delete {path} after {retries} attempts.")
        except Exception as e:
            print(f"    ! Error deleting file {path}: {e}")
            break

def safe_delete_dir(path, retries=5, delay=0.5):
    """
    Attempts to delete a directory and its contents multiple times.
    """
    if not os.path.exists(path):
        return

    for i in range(retries):
        try:
            shutil.rmtree(path)
            # print(f"    - Deleted directory: {os.path.basename(path)}")
            return
        except PermissionError:
            if i < retries - 1:
                # print(f"    ! Directory locked, retrying {i+1}/{retries}...")
                time.sleep(delay)
            else:
                print(f"    ! Final Error: Could not delete directory {path} after {retries} attempts.")
        except Exception as e:
            print(f"    ! Error deleting directory {path}: {e}")
            break

# --- Sync Mapping Utilities ---

def get_sync_map_path(content_root):
    return os.path.join(content_root, ".canvas_sync_map.json")

def load_sync_map(content_root):
    path = get_sync_map_path(content_root)
    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"    ! Error loading sync map: {e}")
    return {}

def save_sync_map(content_root, sync_map):
    path = get_sync_map_path(content_root)
    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(sync_map, f, indent=4)
    except Exception as e:
        print(f"    ! Error saving sync map: {e}")

def get_mapped_id(content_root, file_path):
    """
    Returns the Canvas ID for a local file path.
    Also returns metadata if available (e.g. mtime).
    """
    rel_path = os.path.relpath(file_path, content_root).replace('\\', '/')
    sync_map = load_sync_map(content_root)
    entry = sync_map.get(rel_path)
    
    if isinstance(entry, dict):
        return entry.get('id'), entry
    return entry, None

def save_mapped_id(content_root, file_path, canvas_id, mtime=None):
    """
    Saves the Canvas ID and optionally the mtime for a local file path.
    """
    rel_path = os.path.relpath(file_path, content_root).replace('\\', '/')
    sync_map = load_sync_map(content_root)
    
    if mtime is not None:
        sync_map[rel_path] = {
            'id': canvas_id,
            'mtime': mtime
        }
    else:
        # Backward compatibility / simple ID
        sync_map[rel_path] = canvas_id
    
    save_sync_map(content_root, sync_map)

def prune_orphaned_assets(course):
    """
    Deletes files from namespaced folders that are no longer in ACTIVE_ASSET_IDS.
    """
    global ACTIVE_ASSET_IDS
    print(f">> Starting Asset Orphan Cleanup...")
    print(f"   Active assets tracked: {len(ACTIVE_ASSET_IDS)}")
    
    deleted_count = 0
    managed_folders = [FOLDER_IMAGES, FOLDER_FILES]
    
    for folder_name in managed_folders:
        try:
            # We don't want to create them if they don't exist during pruning?
            # Actually get_or_create_folder is safe and caches.
            folder = get_or_create_folder(course, folder_name)
            files = folder.get_files()
            for f in files:
                if f.id not in ACTIVE_ASSET_IDS:
                    print(f"   - Deleting orphaned asset: {f.filename} (ID: {f.id}) from {folder_name}")
                    f.delete()
                    deleted_count += 1
        except Exception as e:
            print(f"   ! Error pruning folder {folder_name}: {e}")
            
    if deleted_count > 0:
        print(f"   Cleanup complete. Removed {deleted_count} orphaned files.")
    else:
        print(f"   Cleanup complete. No orphans found.")
