"""
Reusable single-asset sync.

Provides a programmatic entry point, :func:`sync_single_file`, for syncing one
content file (page, assignment, quiz, subheader, external link, study guide, or
solo asset) to Canvas. The asset is placed in the correct slot within its module
and recorded in ``.canvas_sync_map.json`` so a later full sync recognizes it.

This module is deliberately decoupled from the CLI / argparse so a future GUI
can import and call :func:`sync_single_file` directly. The full-directory sync in
``sync_to_canvas.py`` shares the :func:`build_handlers` and
:func:`find_or_create_module` helpers defined here.
"""

import os
from dataclasses import dataclass

from handlers.log import logger
from handlers.content_utils import (
    parse_module_name,
    expected_canvas_title,
    is_valid_name,
    upload_file,
    FOLDER_FILES,
)
from handlers.study_guide_handler import StudyGuideHandler
from handlers.page_handler import PageHandler
from handlers.assignment_handler import AssignmentHandler
from handlers.quiz_handler import QuizHandler
from handlers.new_quiz_handler import NewQuizHandler
from handlers.subheader_handler import SubHeaderHandler
from handlers.external_link_handler import ExternalLinkHandler


def build_handlers():
    """Return the standard ordered handler chain (shared by full and single sync)."""
    return [
        StudyGuideHandler(),
        PageHandler(),
        AssignmentHandler(),
        NewQuizHandler(),
        QuizHandler(),
        ExternalLinkHandler(),
        SubHeaderHandler(),
    ]


def find_or_create_module(course, module_name):
    """Find a Canvas module by name, creating it if missing. Returns the Module."""
    modules = course.get_modules(search_term=module_name)
    for m in modules:
        if m.name == module_name:
            logger.debug("  Found existing module: %s (ID: %s)", module_name, m.id)
            return m

    logger.info("  [green]Creating new module:[/green] %s", module_name)
    return course.create_module(module={'name': module_name})


def compute_insert_position(module, module_dir, target_filename):
    """
    Compute the 1-based position a single synced item should occupy within its
    module, matching the order a full sync would produce.

    The position is the number of *syncable* sibling files that sort before the
    target AND are currently present as module items, plus one. This is a
    relative insert: Canvas reflows the remaining items when the position is
    applied, so it stays correct even when some earlier siblings aren't synced
    yet.
    """
    siblings = sorted(
        f for f in os.listdir(module_dir)
        if f != target_filename
        and is_valid_name(f)
        and os.path.isfile(os.path.join(module_dir, f))
    )
    before = [f for f in siblings if f < target_filename]
    before_titles = {expected_canvas_title(os.path.join(module_dir, f)) for f in before}
    before_titles.discard(None)

    count = 0
    for item in module.get_module_items():
        if getattr(item, 'title', None) in before_titles:
            count += 1
    return count + 1


@dataclass
class SingleSyncResult:
    """Outcome of a single-asset sync (GUI-friendly: no log parsing required)."""
    success: bool
    message: str
    module_item: object = None
    position: int = None


def sync_single_file(course, content_root, target_abs_path, canvas=None, handlers=None):
    """
    Sync one content file to Canvas, placed in its correct module slot.

    Args:
        course: ``canvasapi`` Course object.
        content_root: absolute path to the content directory root.
        target_abs_path: absolute path to the file to sync (must be inside
            ``content_root``).
        canvas: ``canvasapi`` Canvas object, passed through to handlers as
            ``canvas_obj`` (required by some handlers, e.g. New Quizzes).
        handlers: optional pre-built handler chain; defaults to
            :func:`build_handlers`.

    Returns:
        :class:`SingleSyncResult` describing the outcome.
    """
    content_root = os.path.abspath(content_root)
    target_abs_path = os.path.abspath(target_abs_path)

    if not os.path.exists(target_abs_path):
        return SingleSyncResult(False, f"File not found: {target_abs_path}")
    if not target_abs_path.startswith(content_root):
        return SingleSyncResult(False, f"File is not inside the content directory: {target_abs_path}")

    filename = os.path.basename(target_abs_path)
    if not is_valid_name(filename):
        return SingleSyncResult(
            False,
            f"File has no NN_ prefix and would be ignored by a full sync: {filename}",
        )

    if handlers is None:
        handlers = build_handlers()

    # Resolve the module from the parent directory.
    parent_dir = os.path.dirname(target_abs_path)
    module_obj = None
    if parent_dir != content_root:
        parent_name = os.path.basename(parent_dir)
        if not is_valid_name(parent_name):
            return SingleSyncResult(
                False,
                f"Parent directory is not a valid module (needs an NN_ prefix): {parent_name}",
            )
        module_name = parse_module_name(parent_name)
        logger.info("[cyan]Processing module:[/cyan] [bold]%s[/bold]", module_name)
        module_obj = find_or_create_module(course, module_name)

    rel_display = os.path.relpath(target_abs_path, content_root)
    logger.info("[cyan]Syncing single asset:[/cyan] %s", rel_display)

    # Dispatch through the handler chain (first can_handle() wins).
    mod_item = None
    handled = False
    for handler in handlers:
        # SubHeaders / ExternalLinks make no sense without a module.
        if module_obj is None and isinstance(handler, (SubHeaderHandler, ExternalLinkHandler)):
            continue
        if handler.can_handle(target_abs_path):
            mod_item = handler.sync(
                target_abs_path, course, module_obj,
                canvas_obj=canvas, content_root=content_root,
            )
            handled = True
            break

    if not handled:
        # Solo asset (PDF, ZIP, etc.) — only meaningful inside a module.
        if module_obj is None:
            return SingleSyncResult(False, f"No handler matched and file is not in a module: {filename}")

        logger.info("  [yellow]Uploading file:[/yellow] %s", filename)
        file_url, file_id = upload_file(course, target_abs_path, FOLDER_FILES, content_root=content_root)
        if not file_id:
            return SingleSyncResult(False, f"Upload failed: {filename}")

        mod_item = handlers[0].add_to_module(module_obj, {
            'type': 'File',
            'content_id': file_id,
            'title': parse_module_name(filename),
            'published': True,
        })

    # Place the item in its correct slot within the module.
    position = None
    if module_obj is not None and mod_item is not None:
        position = compute_insert_position(module_obj, parent_dir, filename)
        if getattr(mod_item, 'position', None) != position:
            logger.debug(
                "  Moving '%s' to position %d (was %s)",
                getattr(mod_item, 'title', '?'), position, getattr(mod_item, 'position', '?'),
            )
            try:
                mod_item.edit(module_item={'position': position})
                mod_item.position = position
            except Exception as e:
                logger.error("  Failed to position item: %s", e)

    return SingleSyncResult(True, f"Synced {rel_display}", module_item=mod_item, position=position)
