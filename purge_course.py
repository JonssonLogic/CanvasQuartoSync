"""
Purge content from a Canvas course.

Removes modules, pages, assignments, quizzes, and files from the target
Canvas course. Can purge everything or selectively delete specific items.
Requires manual confirmation by typing the course name.

Usage:
    python purge_course.py <content_path> [options]

Examples:
    # List everything (dry run)
    python purge_course.py ./TestCourse --dry-run

    # Purge everything
    python purge_course.py ./TestCourse

    # Delete specific items
    python purge_course.py ./TestCourse --module "Fundamentals" --page "Welcome" \
        --assignment "Lab 0" "Lab 3" --file "CoursePM.pdf"

    # Dry run with selective items
    python purge_course.py ./TestCourse --dry-run --module "Fundamentals"
"""

import os
import sys
import argparse

from canvasapi import Canvas

from handlers import __version__
from handlers.log import logger, setup_logging
from handlers.config import get_api_credentials, get_course_id


def _get_name(obj, category):
    """Get the display name for a Canvas object based on its category."""
    if category == "files":
        return getattr(obj, "display_name", None) or getattr(obj, "filename", str(obj.id))
    return getattr(obj, "name", None) or getattr(obj, "title", None) or str(obj)


def fetch_inventory(course, filters=None):
    """Fetch deletable objects from the course, optionally filtered by name.

    Args:
        course: Canvas course object.
        filters: dict mapping category -> list of names to match, or None for all.
                 Categories not in filters are skipped entirely.
    """
    select_all = filters is None
    inventory = {}

    categories = {
        "modules":     (course.get_modules,     None),
        "pages":       (course.get_pages,       None),
        "assignments": (course.get_assignments, None),
        "quizzes":     (course.get_quizzes,     None),
        "files":       (course.get_files,       None),
    }

    for category, (fetch_fn, _) in categories.items():
        if not select_all and category not in filters:
            inventory[category] = []
            continue

        logger.info("Fetching %s...", category)
        all_items = list(fetch_fn())

        if select_all:
            inventory[category] = all_items
        else:
            names = set(filters[category])
            matched = [item for item in all_items if _get_name(item, category) in names]
            matched_names = {_get_name(item, category) for item in matched}
            for name in names - matched_names:
                logger.warning("  [yellow]Not found %s:[/yellow] %s", category[:-1], name)
            inventory[category] = matched

    return inventory


def print_inventory(inventory):
    """Print a summary of everything that will be deleted."""
    total = 0

    for category, items in inventory.items():
        count = len(items)
        total += count
        if count == 0:
            continue
        logger.info("  [yellow]%s: %d[/yellow]", category, count)
        for item in items:
            logger.info("    - %s", _get_name(item, category))

    logger.info("")
    logger.info("  [bold]Total objects: %d[/bold]", total)
    return total


def purge(course, inventory):
    """Delete all objects in the inventory from the course."""

    for module in inventory["modules"]:
        name = getattr(module, "name", module.id)
        try:
            module.delete()
            logger.info("  [red]Deleted module:[/red] %s", name)
        except Exception as e:
            logger.error("  Failed to delete module %s: %s", name, e)

    for page in inventory["pages"]:
        title = getattr(page, "title", page.url)
        # Canvas won't let you delete/unpublish the front page — unset it first
        try:
            if getattr(page, "front_page", False):
                page.edit(wiki_page={"front_page": False})
            page.delete()
            logger.info("  [red]Deleted page:[/red] %s", title)
        except Exception as e:
            logger.error("  Failed to delete page %s: %s", title, e)

    for assignment in inventory["assignments"]:
        name = getattr(assignment, "name", assignment.id)
        try:
            assignment.delete()
            logger.info("  [red]Deleted assignment:[/red] %s", name)
        except Exception as e:
            logger.error("  Failed to delete assignment %s: %s", name, e)

    for quiz in inventory["quizzes"]:
        title = getattr(quiz, "title", quiz.id)
        try:
            quiz.delete()
            logger.info("  [red]Deleted quiz:[/red] %s", title)
        except Exception as e:
            logger.error("  Failed to delete quiz %s: %s", title, e)

    for f in inventory["files"]:
        name = getattr(f, "display_name", f.id)
        try:
            f.delete()
            logger.info("  [red]Deleted file:[/red] %s", name)
        except Exception as e:
            logger.error("  Failed to delete file %s: %s", name, e)


def main():
    parser = argparse.ArgumentParser(description="Purge content from a Canvas course.")
    parser.add_argument("--version", action="version", version=f"CanvasQuartoSync {__version__}")
    parser.add_argument("content_path", nargs="?", default=".", help="Path to content directory with config.toml (default: current dir).")
    parser.add_argument("--course-id", help="Canvas Course ID (override config.toml).")
    parser.add_argument("--dry-run", action="store_true", help="Only list content, do not delete anything.")

    # Selective deletion
    parser.add_argument("--module", nargs="+", metavar="NAME", help="Delete specific module(s) by name.")
    parser.add_argument("--page", nargs="+", metavar="NAME", help="Delete specific page(s) by title.")
    parser.add_argument("--assignment", nargs="+", metavar="NAME", help="Delete specific assignment(s) by name.")
    parser.add_argument("--quiz", nargs="+", metavar="NAME", help="Delete specific quiz(zes) by title.")
    parser.add_argument("--file", nargs="+", metavar="NAME", help="Delete specific file(s) by display name.")

    verbosity = parser.add_mutually_exclusive_group()
    verbosity.add_argument("--verbose", "-v", action="store_true", help="Show detailed debug output.")
    verbosity.add_argument("--quiet", "-q", action="store_true", help="Only show errors.")
    parser.add_argument("--log-file", help="Write full debug log to a file.")

    args = parser.parse_args()
    setup_logging(verbose=args.verbose, quiet=args.quiet, log_file=args.log_file)

    content_root = os.path.abspath(args.content_path)
    if not os.path.exists(content_root):
        logger.error("[red]Content directory not found:[/red] %s", content_root)
        sys.exit(1)

    # --- Credentials & course ---
    api_url, api_token = get_api_credentials(content_root)
    if not api_url or not api_token:
        logger.error("Canvas credentials not found. Set CANVAS_API_URL / CANVAS_API_TOKEN env vars, "
                      "or provide canvas_api_url / canvas_token_path in config.toml.")
        sys.exit(1)

    course_id = get_course_id(content_root, args.course_id)
    if not course_id:
        logger.error("Course ID not found. Provide --course-id or set course_id in config.toml.")
        sys.exit(1)

    logger.info("Connecting to Canvas...")
    canvas = Canvas(api_url, api_token)
    try:
        course = canvas.get_course(course_id)
    except Exception as e:
        logger.error("[red]Failed to connect to course %s:[/red] %s", course_id, e)
        sys.exit(1)

    course_name = getattr(course, "name", str(course_id))
    logger.info("Connected to course: [bold]%s[/bold] (ID: %s)", course_name, course_id)
    logger.info("")

    # --- Build filters (None = purge all) ---
    selective = any([args.module, args.page, args.assignment, args.quiz, args.file])
    filters = None
    if selective:
        filters = {}
        if args.module:
            filters["modules"] = args.module
        if args.page:
            filters["pages"] = args.page
        if args.assignment:
            filters["assignments"] = args.assignment
        if args.quiz:
            filters["quizzes"] = args.quiz
        if args.file:
            filters["files"] = args.file

    # --- Inventory ---
    inventory = fetch_inventory(course, filters)
    logger.info("")
    logger.info("[bold]Will delete:[/bold]" if selective else "[bold]Course content:[/bold]")
    total = print_inventory(inventory)

    if total == 0:
        logger.info("[green]Nothing to purge.[/green]")
        return

    if args.dry_run:
        logger.info("")
        logger.info("[cyan]Dry run — nothing was deleted.[/cyan]")
        return

    # --- Confirmation ---
    logger.info("")
    if selective:
        logger.info("[bold red]WARNING: This will permanently delete the %d item(s) listed above.[/bold red]", total)
    else:
        logger.info("[bold red]WARNING: This will permanently delete ALL content listed above.[/bold red]")
    logger.info("To confirm, type the course name exactly: [bold]%s[/bold]", course_name)
    logger.info("")

    try:
        confirmation = input("Course name: ").strip()
    except (EOFError, KeyboardInterrupt):
        logger.info("\nAborted.")
        return

    if confirmation != course_name:
        logger.error("[red]Name does not match. Aborting.[/red]")
        sys.exit(1)

    logger.info("")
    logger.info("[bold]Purging...[/bold]")
    purge(course, inventory)
    logger.info("")
    logger.info("[green]Purge complete.[/green]")


if __name__ == "__main__":
    main()
