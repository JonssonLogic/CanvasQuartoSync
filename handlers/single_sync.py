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
from handlers.course_layout import load_layout
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


def arrange_modules(course, names):
    """Put the course's own modules in ``names`` order, leaving the rest alone.

    Only the slots the local modules already occupy are reassigned, so a
    module that exists only in Canvas keeps its place. Canvas order [Info, B,
    A] with local order [A, B] becomes [Info, A, B]. Returns how many moved.
    """
    wanted = {n: i for i, n in enumerate(names)}
    ours = [m for m in course.get_modules() if m.name in wanted]
    slots = sorted(getattr(m, 'position', 0) or 0 for m in ours)
    moved = 0
    for slot, m in zip(slots, sorted(ours, key=lambda m: wanted[m.name])):
        if getattr(m, 'position', None) != slot:
            logger.debug("  Moving module '%s' to position %d", m.name, slot)
            try:
                m.edit(module={'position': slot})
                moved += 1
            except Exception as e:
                logger.error("  Failed to move module %s: %s", m.name, e)
    return moved


def _placement_key(item):
    """What a module item points at, for items that can only be in one place.

    SubHeaders and external links are matched by title and URL, which may
    legitimately repeat, so they are never treated as moved.
    """
    t = getattr(item, 'type', None)
    if t == 'Page':
        return (t, getattr(item, 'page_url', None))
    if t in ('Assignment', 'Quiz', 'File'):
        try:
            return (t, int(getattr(item, 'content_id', None)))
        except (TypeError, ValueError):
            return None
    return None


def remove_stale_placements(course, placed, own_names):
    """Take moved items out of the module they used to be in.

    ``placed`` is ``[(module_id, module_item)]`` for what this sync put in
    place. A file moved to another module folder is added to its new module,
    and the item in the old one would otherwise stay behind. Only the
    course's own modules (``own_names``) are touched: an item someone placed
    by hand in a Canvas-only module is theirs. Returns how many were removed.
    """
    home = {}
    for module_id, item in placed:
        key = _placement_key(item)
        if key and key[1] is not None:
            home[key] = module_id
    if not home:
        return 0
    removed = 0
    for module in course.get_modules():
        if module.name not in own_names:
            continue
        for item in module.get_module_items():
            key = _placement_key(item)
            if key in home and home[key] != module.id:
                logger.info("  [yellow]Removing[/yellow] '%s' from module %s: it has moved.",
                            getattr(item, 'title', '?'), module.name)
                try:
                    item.delete()
                    removed += 1
                except Exception as e:
                    logger.error("  Failed to remove %s from %s: %s", getattr(item, 'title', '?'), module.name, e)
    return removed


def compute_insert_position(module, module_dir, target_filename, order=None):
    """
    The 1-based position a single synced item should occupy within its
    module, matching the order a full sync would produce.

    ``order`` is the module's files in layout order (``_order.yml``, or NN_
    prefixes). Without it the syncable siblings are taken in sorted filename
    order, which is the NN_ rule. The position is how many of the files
    before the target are already module items, plus one. This is a relative
    insert: Canvas reflows the remaining items when the position is applied,
    so it stays correct even when some earlier siblings aren't synced yet.
    """
    if order is None:
        order = sorted(f for f in os.listdir(module_dir)
                       if is_valid_name(f) and os.path.isfile(os.path.join(module_dir, f)))
        if target_filename not in order:
            order = sorted(order + [target_filename])
    keys = [f.lower() for f in order]
    before = order[:keys.index(target_filename.lower())] if target_filename.lower() in keys else order
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
    layout = load_layout(content_root)
    mod, included = layout.locate(target_abs_path)
    if not included:
        return SingleSyncResult(
            False,
            f"{filename} would be skipped by a full sync: {layout.why_excluded(target_abs_path)}.",
        )

    if handlers is None:
        handlers = build_handlers()

    parent_dir = os.path.dirname(target_abs_path)
    module_obj = None
    if mod is not None:
        logger.info("[cyan]Processing module:[/cyan] [bold]%s[/bold]", mod.name)
        known = {m.name for m in course.get_modules(search_term=mod.name)}
        module_obj = find_or_create_module(course, mod.name)
        if mod.name not in known:
            # New in Canvas: it was appended, so move it among its siblings.
            arrange_modules(course, [m.name for m in layout.modules])

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
        position = compute_insert_position(module_obj, parent_dir, filename, order=mod.items)
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

        try:
            remove_stale_placements(course, [(module_obj.id, mod_item)], {m.name for m in layout.modules})
        except Exception as e:
            logger.error("  Could not check other modules for an old copy: %s", e)

    return SingleSyncResult(True, f"Synced {rel_display}", module_item=mod_item, position=position)
