"""Report on, and apply, grade rollups declared in a course's frontmatter.

A rollup marks one assignment on the strength of several others. See
``handlers/rollup.py`` for what a declaration looks like and why it lives in
the target's frontmatter.

This is a separate entry point from ``sync_to_canvas.py`` on purpose. Content
sync is routine and safe to run on a whim; writing grades is neither. Keeping
them apart means no one ever pushes a page and moves a grade in the same
breath.

Usage::

    python rollup.py .                    # what is declared, no Canvas calls
    python rollup.py . --status           # + who qualifies       (reads Canvas)
    python rollup.py . --status --json    # the same, for a GUI
    python rollup.py . --apply            # mark them            (writes)

``--apply`` is the only mode that changes anything, and it only ever raises a
grade. A student holding a pass without qualifying is reported and left alone.
"""

import argparse
import json
import os
import sys

from canvasapi import Canvas

from handlers.log import logger, setup_logging
from handlers.config import get_api_credentials, get_course_id
from handlers.content_utils import verify_sync_map_course
from handlers.rollup import (RollupConfigError, apply_rollup, discover_rollups,
                             evaluate_rollup, restrict_to_students)


def _connect(content_root, course_id_arg):
    """Resolve credentials and course the same way the sync does."""
    api_url, api_token = get_api_credentials(content_root)
    course_id = get_course_id(content_root, course_id_arg)

    if not api_url or not api_token:
        logger.error("[red]Canvas credentials not found.[/red] Set CANVAS_API_URL / "
                     "CANVAS_API_TOKEN env vars, or provide canvas_api_url / "
                     "canvas_token_path in config.toml.")
        return None
    if not course_id:
        logger.error("[red]Course ID not specified.[/red] Provide it via --course-id, "
                     "config.toml, or a 'course_id.txt' file in the content directory.")
        return None

    try:
        course = Canvas(api_url, api_token).get_course(course_id)
    except Exception as e:
        logger.error("[red]Connection failed:[/red] %s", e)
        return None

    # A sync map's Canvas ids only mean anything in the course they came from,
    # and this tool writes grades using those ids. Refuse before touching one.
    if not verify_sync_map_course(content_root, course_id, getattr(course, 'name', None)):
        return None

    logger.info("[green]Connected to course:[/green] [bold]%s[/bold] (ID: %s)",
                course.name, course.id)
    return course


def _print_declaration(r):
    """The offline half of the report: what is declared and whether it holds."""
    logger.info("")
    logger.info("[bold]%s[/bold]", r['name'])
    logger.info("  target:   %s%s", r['target'],
                f" (id {r['target_id']})" if r['target_id'] else " [yellow](never synced)[/yellow]")
    logger.info("  requires: %d, pass at score >= %s", len(r['requires']), r['pass_at'])
    for req in r['requires']:
        if not req['exists']:
            mark, note = "[red]x[/red]", "[red]file not found[/red]"
        elif req['target_id'] is None:
            mark, note = "[yellow]?[/yellow]", "[yellow]never synced[/yellow]"
        else:
            mark, note = "[green]+[/green]", f"id {req['target_id']}"
        logger.info("    %s %-50s %s", mark, req['declared_as'], note)
    for p in r['problems']:
        logger.error("  [red]problem:[/red] %s", p)


def _print_status(r):
    """The online half: who qualifies, who is already marked, what is stuck."""
    st = r.get('status')
    if st is None:
        logger.warning("  [yellow]not checked: fix the problems above first[/yellow]")
        return
    logger.info("  [bold]%d of %d students[/bold] satisfy every requirement "
                "(%d already marked, %d to mark)",
                st['complete'], st['students'], st['already'], len(st['to_mark']))
    outstanding = {p: n for p, n in st['missing'].items() if n}
    if outstanding:
        logger.info("  still waiting on:")
        for path, n in sorted(outstanding.items(), key=lambda kv: -kv[1]):
            logger.info("    %-50s %d student(s)", path, n)
    for s in st['to_mark']:
        logger.info("    [green]+[/green] %s", s['sortable_name'])
    for s in st['conflicts']:
        logger.warning("    [yellow]![/yellow] %s holds a pass but no longer qualifies, "
                       "leaving untouched", s['sortable_name'])


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("content_path", help="Course content directory")
    parser.add_argument("--status", action="store_true",
                        help="Check Canvas for who qualifies. Reads only.")
    parser.add_argument("--apply", action="store_true",
                        help="Mark the qualifying students. The only mode that writes.")
    parser.add_argument("--only", metavar="PATH",
                        help="Act on the single rollup declared by this file")
    parser.add_argument("--students", metavar="IDS",
                        help="With --apply: mark only these Canvas user ids "
                             "(comma-separated), the ones a GUI showed and had confirmed")
    parser.add_argument("--json", action="store_true",
                        help="Print the result as JSON on stdout")
    parser.add_argument("--course-id", help="Override the course id")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    student_ids = None
    if args.students is not None:
        try:
            student_ids = [int(s) for s in args.students.split(',') if s.strip()]
        except ValueError:
            parser.error("--students takes comma-separated Canvas user ids")
        if not args.apply:
            parser.error("--students only means something with --apply")

    # Machine output implies quiet, and goes to stderr: stdout is the JSON
    # document, and an error printed in front of it would make it unparseable
    # exactly when the caller most needs to read it.
    setup_logging(verbose=args.verbose, quiet=args.quiet or args.json,
                  to_stderr=args.json)

    content_root = os.path.abspath(args.content_path)
    if not os.path.exists(content_root):
        logger.error("[red]Content directory not found:[/red] %s", content_root)
        return 1

    rollups = discover_rollups(content_root)
    if args.only:
        wanted = args.only.replace('\\', '/')
        rollups = [r for r in rollups if r['target'] == wanted]
        if not rollups:
            logger.error("[red]No rollup declared by:[/red] %s", args.only)
            return 1

    if not rollups:
        if args.json:
            print(json.dumps({'rollups': []}, ensure_ascii=False))
        else:
            logger.info("No rollups declared in %s", content_root)
            logger.info("A rollup is declared in the frontmatter of the assignment "
                        "it grades. See Guides/Canvas_Sync_User_Guide.md.")
        return 0

    # Offline report always comes first: a broken path is the common failure
    # and finding it should not need credentials.
    if not args.json:
        for r in rollups:
            _print_declaration(r)

    if not (args.status or args.apply):
        if args.json:
            print(json.dumps({'rollups': rollups}, ensure_ascii=False))
        else:
            logger.info("")
            logger.info("Add --status to check Canvas for who qualifies.")
        return 0 if all(not r['problems'] for r in rollups) else 1

    course = _connect(content_root, args.course_id)
    if course is None:
        return 1

    evaluated = []
    for r in rollups:
        if r['problems']:
            evaluated.append({**r, 'status': None})
            continue
        try:
            evaluated.append(evaluate_rollup(course, r))
        except RollupConfigError as e:
            evaluated.append({**r, 'status': None,
                              'problems': r['problems'] + [str(e)]})
        except Exception as e:
            logger.error("[red]%s: could not evaluate:[/red] %s", r['name'], e)
            evaluated.append({**r, 'status': None,
                              'problems': r['problems'] + [str(e)]})

    if not args.apply:
        if args.json:
            print(json.dumps({'rollups': evaluated}, ensure_ascii=False))
        else:
            for r in evaluated:
                logger.info("")
                logger.info("[bold]%s[/bold]", r['name'])
                _print_status(r)
            logger.info("")
            logger.info("Nothing was changed. Add --apply to mark them.")
        return 0

    results = []
    for r in evaluated:
        if r.get('status') is None:
            continue
        skipped = []
        if student_ids is not None:
            r, skipped = restrict_to_students(r, student_ids)
            if skipped:
                logger.warning("[yellow]%s: %d confirmed student(s) no longer to mark, "
                               "skipped[/yellow]", r['name'], len(skipped))
        if not r['status']['to_mark']:
            logger.info("[dim]%s: nothing to mark[/dim]", r['name'])
            # Still reported when ids were confirmed: "you approved 29 and
            # none were marked" is something the caller has to be able to say.
            if skipped:
                results.append({'target': r['target'], 'name': r['name'], 'grade': None,
                                'marked': [], 'failed': [],
                                'conflicts': r['status']['conflicts'], 'skipped': skipped})
            continue
        logger.info("[cyan]%s: marking %d student(s)...[/cyan]",
                    r['name'], len(r['status']['to_mark']))
        results.append({**apply_rollup(course, r), 'skipped': skipped})

    if args.json:
        print(json.dumps({'applied': results}, ensure_ascii=False))
    else:
        total = sum(len(x['marked']) for x in results)
        failed = sum(len(x['failed']) for x in results)
        logger.info("")
        logger.info("[green]Done:[/green] %d marked%s", total,
                    f", [red]{failed} failed[/red]" if failed else "")
    return 1 if any(x['failed'] for x in results) else 0


if __name__ == "__main__":
    sys.exit(main())
