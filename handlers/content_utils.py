import os
import re
import frontmatter
import json
import shutil
from canvasapi import Canvas
from handlers.log import logger

# Global cache for folder names to IDs to avoid redundant API lookups
FOLDER_CACHE = {}

def parse_module_name(text):
    """
    Removes leading digits and underscore from a name (e.g. '01_Intro' -> 'Intro').
    Strictly matches 'NN_' where N is a digit.
    """
    if not text:
        return text
    match = re.match(r'^(\d{2})_(.*)', text)
    if match:
        return match.group(2)
    return text

def clean_title(filename):
    """
    Removes NN_ prefix and file extension.
    Example: '05_Syllabus.pdf' -> 'Syllabus.pdf' or '02_Intro.qmd' -> 'Intro'
    """
    return parse_module_name(filename)

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
    logger.debug("    Checking for folder: %s", folder_path)
    folders = course.get_folders()
    for f in folders:
        FOLDER_CACHE[f.name.lower()] = f

    # 3. Check cache again after populating
    if cache_key in FOLDER_CACHE:
        return FOLDER_CACHE[cache_key]

    # 4. Not found, create
    logger.info("    [green]Creating folder:[/green] %s", folder_path)
    try:
        if parent_folder_id:
            new_folder = course.create_folder(name=folder_path, parent_folder_id=parent_folder_id)
        else:
            new_folder = course.create_folder(name=folder_path)

        FOLDER_CACHE[new_folder.name.lower()] = new_folder
        return new_folder
    except Exception as e:
        logger.error("    Failed to create folder '%s': %s", folder_path, e)
        raise

def upload_file(course, local_path, target_folder_name="course_files", content_root=None):
    """
    Uploads a file (image or asset) to Canvas.
    Returns a tuple (file_url, file_id).
    """
    global ACTIVE_ASSET_IDS
    if not os.path.exists(local_path):
        logger.error("    File not found: %s", local_path)
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

    logger.info("    [yellow]Uploading file:[/yellow] %s", filename)
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
            logger.error("    Upload failed for %s", filename)
            return local_path, None
    except Exception as e:
        logger.error("    Failed to upload file %s: %s", filename, e)
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
        logger.warning("    Link target not found: %s", link_target)
        return link_target

    filename = os.path.basename(abs_target_path)
    ext = os.path.splitext(filename)[1].lower()

    # 2. Determine Title and Type
    target_title = None
    target_type = None

    try:
        if ext == '.qmd':
            post = frontmatter.load(abs_target_path)
            canvas_meta = post.metadata.get('canvas', {})
            if not canvas_meta:
                # No canvas metadata — this is a downloadable file, not a Canvas content item
                return None
            # Title can be under canvas or at root
            target_title = canvas_meta.get('title') or post.metadata.get('title') or parse_module_name(os.path.splitext(filename)[0])
            target_type = canvas_meta.get('type', 'page') # Default to page if unspecified

        elif ext == '.json':
            # Check if New Quiz JSON
            with open(abs_target_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            canvas_meta = data.get('canvas', {})
            target_title = canvas_meta.get('title', parse_module_name(os.path.splitext(filename)[0]))
            if canvas_meta.get('quiz_engine') == 'new':
                target_type = 'new_quiz'
            else:
                target_type = 'quiz'

        else:
            return link_target

    except Exception as e:
        logger.error("    Failed to parse target %s: %s", filename, e)
        return link_target

    # 3. Find or Create Stub in Canvas
    canvas_url = link_target # Fallback

    if target_type == 'page':
        pages = course.get_pages(search_term=target_title)
        target_obj = None
        for p in pages:
            if p.title == target_title:
                target_obj = p
                break

        if not target_obj:
            logger.info("    [green]Creating stub page:[/green] %s", target_title)
            target_obj = course.create_page(wiki_page={'title': target_title, 'published': False, 'body': '<i>Placeholder for future sync.</i>'})

        canvas_url = target_obj.html_url

    elif target_type == 'assignment':
        assignments = course.get_assignments(search_term=target_title)
        target_obj = None
        for a in assignments:
            if a.name == target_title:
                target_obj = a
                break

        if not target_obj:
            logger.info("    [green]Creating stub assignment:[/green] %s", target_title)
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
             logger.info("    [green]Creating stub quiz:[/green] %s", target_title)
             target_obj = course.create_quiz(quiz={'title': target_title, 'published': False, 'quiz_type': 'assignment'})

        canvas_url = target_obj.html_url

    elif target_type == 'new_quiz':
        assignments = course.get_assignments(search_term=target_title)
        target_obj = None
        for a in assignments:
            if a.name == target_title:
                target_obj = a
                break

        if not target_obj:
            logger.info("    [green]Creating stub new quiz:[/green] %s", target_title)
            from handlers.new_quiz_api import NewQuizAPIClient
            api_url = course._requester.original_url
            api_token = course._requester._access_token
            client = NewQuizAPIClient(api_url, api_token)

            quiz_payload = {
                'title': target_title,
                'published': False
            }
            try:
                created_quiz = client.create_quiz(course.id, quiz_payload)
                target_obj = course.get_assignment(created_quiz['id'])
            except Exception as e:
                logger.error("    Failed to create new quiz stub: %s", e)
                return canvas_url

        canvas_url = target_obj.html_url

    return canvas_url

def process_content(content, base_path, course, content_root=None):
    """
    Main entry point. Scans for images AND file/content links.
    Fenced code blocks are protected from processing.
    """

    # --- 0. Protect fenced code blocks from link/image processing ---
    code_blocks = {}
    block_counter = [0]

    def protect_code_block(match):
        placeholder = f"\x00CODE_BLOCK_{block_counter[0]}\x00"
        code_blocks[placeholder] = match.group(0)
        block_counter[0] += 1
        return placeholder

    content = re.sub(r'```[\s\S]*?```', protect_code_block, content)

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
        new_url, file_id = upload_file(course, abs_path, FOLDER_IMAGES, content_root=content_root)

        if file_id and new_url:
            ext = os.path.splitext(abs_path)[1].lower()
            if '?' in new_url:
                base_part, query_part = new_url.split('?', 1)
                if not base_part.endswith(ext):
                    new_url = f"{base_part}{ext}?{query_part}"

            return f"![{alt_text}]({new_url})"

        return f"![{alt_text}]({rel_path})"

    # Regex for images
    content = re.sub(r'!\[(.*?)\]\((.*?)\)', image_replacer, content)

    # --- 1b. Process HTML <img src="..."> tags ---
    def img_tag_replacer(match):
        full_tag = match.group(0)
        src = match.group(1)

        if src.startswith(('http://', 'https://', 'data:')):
            return full_tag

        if os.path.isabs(src):
            abs_path = src
        else:
            abs_path = os.path.normpath(os.path.join(base_path, src))

        new_url, file_id = upload_file(course, abs_path, FOLDER_IMAGES, content_root=content_root)

        if file_id and new_url:
            ext = os.path.splitext(abs_path)[1].lower()
            if '?' in new_url:
                base_part, query_part = new_url.split('?', 1)
                if not base_part.endswith(ext):
                    new_url = f"{base_part}{ext}?{query_part}"
            return full_tag.replace(src, new_url)

        return full_tag

    content = re.sub(r'<img\s[^>]*?src=["\']([^"\']+)["\']', img_tag_replacer, content)

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
            if new_url is not None:
                return f"[{link_text}]({new_url})"
            # resolve_cross_link returned None — no canvas metadata, fall through to file upload

        # Asset Upload (PDF, ZIP, DOCX, PY, IPYNB, QMD templates, etc)
        new_url, file_id = upload_file(course, abs_path, FOLDER_FILES, content_root=content_root)
        if file_id:
            api_url = course._requester.original_url
            preview_url = f"{api_url}/courses/{course.id}/files/{file_id}"
            return f"[{link_text}]({preview_url})"
        return f"[{link_text}]({new_url})"

    pattern_links = r'(?<!\!)\[(.*?)\]\((.*?)\)'
    content = re.sub(pattern_links, link_replacer, content)

    # --- 3. Restore protected code blocks ---
    for placeholder, original in code_blocks.items():
        content = content.replace(placeholder, original)

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
            logger.debug("    Deleted file: %s", os.path.basename(path))
            return
        except PermissionError:
            if i < retries - 1:
                logger.debug("    File locked, retrying %d/%d...", i + 1, retries)
                time.sleep(delay)
            else:
                logger.error("    Could not delete %s after %d attempts", path, retries)
        except Exception as e:
            logger.error("    Failed to delete file %s: %s", path, e)
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
            logger.debug("    Deleted directory: %s", os.path.basename(path))
            return
        except PermissionError:
            if i < retries - 1:
                logger.debug("    Directory locked, retrying %d/%d...", i + 1, retries)
                time.sleep(delay)
            else:
                logger.error("    Could not delete directory %s after %d attempts", path, retries)
        except Exception as e:
            logger.error("    Failed to delete directory %s: %s", path, e)
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
            logger.error("    Failed to load sync map: %s", e)
    return {}

def save_sync_map(content_root, sync_map):
    path = get_sync_map_path(content_root)
    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(sync_map, f, indent=4)
    except Exception as e:
        logger.error("    Failed to save sync map: %s", e)

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
    from datetime import datetime, timezone
    rel_path = os.path.relpath(file_path, content_root).replace('\\', '/')
    sync_map = load_sync_map(content_root)
    now_iso = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

    if mtime is not None:
        sync_map[rel_path] = {
            'id': canvas_id,
            'mtime': mtime,
            'last_synced_at': now_iso,
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
    logger.info("[bold cyan]Cleaning up unused files...[/bold cyan]")
    logger.debug("  Tracking %d active assets", len(ACTIVE_ASSET_IDS))

    deleted_count = 0
    managed_folders = [FOLDER_IMAGES, FOLDER_FILES]

    for folder_name in managed_folders:
        try:
            folder = get_or_create_folder(course, folder_name)
            files = folder.get_files()
            for f in files:
                if f.id not in ACTIVE_ASSET_IDS:
                    logger.info("  [red]Deleting unused file:[/red] %s (ID: %s) from %s", f.filename, f.id, folder_name)
                    f.delete()
                    deleted_count += 1
        except Exception as e:
            logger.error("  Failed to clean up folder %s: %s", folder_name, e)

    if deleted_count > 0:
        logger.info("  [green]Cleanup complete.[/green] Removed %d unused files.", deleted_count)
    else:
        logger.info("  [green]Cleanup complete.[/green] No unused files found.")
