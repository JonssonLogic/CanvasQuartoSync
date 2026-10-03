"""cqs: one command for every CanvasQuartoSync tool.

Each verb runs one of the existing scripts, which keep working on their own
(the VS Code extension still calls them directly)::

    cqs check [PATH]        validate_content.py      offline, about a second
    cqs sync [FILE]         sync_to_canvas.py        writes to Canvas
    cqs diff [FILE]         sync_to_canvas.py --check-drift
    cqs rollup              rollup.py                --status reads, --apply writes
    cqs import              import_from_canvas.py
    cqs purge               purge_course.py          deletes in Canvas
    cqs init [DIR]          init_content_project.py
    cqs kit update          init_content_project.py --update
    cqs where               which tool, which Python, which course

The course is found the way git finds a repository: walk up from the current
directory to the nearest config.toml. ``-C DIR`` points somewhere else. File
arguments are taken relative to where you stand, as a shell user expects, and
rewritten to the course-relative form the scripts want.

Bare ``cqs`` prints this help and does nothing else. Writing to Canvas always
takes a verb you typed (#7).

See https://github.com/JonssonLogic/CanvasQuartoSync/issues/39.
"""

import importlib
import os
import sys

TOOL_DIR = os.path.dirname(os.path.abspath(__file__))

# What marks a course folder, best first. course_id.txt and the sync map are
# older courses that may have no config.toml.
_ROOT_MARKERS = ("config.toml", "course_id.txt", ".canvas_sync_map.json")

USAGE = """\
usage: cqs [-C DIR] <command> [options]

  check [PATH]       Check course content offline. No Canvas, about a second.
  sync [FILE]        Sync the course, or one file, to Canvas.
  diff [FILE]        Has anyone edited Canvas since the last sync? Reads only.
  rollup             Grade rollups. --status reads Canvas, --apply writes grades.
  import             Import an existing Canvas course into local files.
  purge              Delete content in Canvas. Try --dry-run first.
  init [DIR]         Set up a new course folder.
  kit update         Refresh the authoring kit in this course folder.
  where              Show which tool, Python and course cqs would use.

  -C DIR             Run as if started in DIR.
  --version          Print the version.

The course is the nearest folder upwards with a config.toml.
'cqs <command> --help' shows that command's own options.
"""


class CqsError(Exception):
    """A user-facing problem: print it, exit 2, no traceback."""


# ---------------------------------------------------------------------------
# Course root and paths
# ---------------------------------------------------------------------------

def find_course_root(start):
    """The nearest folder at or above ``start`` that holds a course, or None."""
    d = os.path.abspath(start)
    while True:
        if any(os.path.exists(os.path.join(d, m)) for m in _ROOT_MARKERS):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent


def _require_root(cwd):
    root = find_course_root(cwd)
    if root is None:
        raise CqsError(f"not inside a course folder: no config.toml in {cwd} or above it.\n"
                       f"cd into the course, or point at it with: cqs -C <course folder> ...")
    return root


def _course_relative(path, cwd, root):
    """A path as typed (relative to cwd) rewritten relative to the course root.

    The scripts' --only takes a course-relative path. Someone standing in
    04_Laborationer/ who types 01_Dragprovning.qmd means the file next to them,
    so resolve against cwd first. A path that does not exist from cwd but does
    from the root is passed through, so the course-relative form keeps working.
    """
    from_cwd = os.path.abspath(os.path.join(cwd, path))
    if not os.path.exists(from_cwd) and os.path.exists(os.path.join(root, path)):
        from_cwd = os.path.abspath(os.path.join(root, path))
    rel = os.path.relpath(from_cwd, root)
    if rel.startswith('..'):
        raise CqsError(f"{path} is outside the course folder {root}")
    return rel.replace('\\', '/')


def _split_file_arg(args):
    """Pull one leading positional FILE out of ``args``, if there is one."""
    if args and not args[0].startswith('-'):
        return args[0], args[1:]
    return None, args


def _rewrite_option_path(args, option, cwd, root):
    """Rewrite the value of ``--option VALUE`` / ``--option=VALUE`` in place."""
    out = list(args)
    for i, a in enumerate(out):
        if a == option and i + 1 < len(out):
            out[i + 1] = _course_relative(out[i + 1], cwd, root)
        elif a.startswith(option + '='):
            out[i] = option + '=' + _course_relative(a.split('=', 1)[1], cwd, root)
    return out


# ---------------------------------------------------------------------------
# Verbs: each returns (module name, argv for that module's main)
# ---------------------------------------------------------------------------

def _check(args, cwd):
    root = _require_root(cwd)
    target, rest = _split_file_arg(args)
    target = os.path.abspath(os.path.join(cwd, target)) if target else root
    return 'validate_content', [target, '--content-root', root, *rest]


def _sync(args, cwd):
    root = _require_root(cwd)
    file, rest = _split_file_arg(args)
    rest = _rewrite_option_path(rest, '--only', cwd, root)
    if file:
        rest = ['--only', _course_relative(file, cwd, root), *rest]
    return 'sync_to_canvas', [root, *rest]


def _diff(args, cwd):
    module, argv = _sync(args, cwd)
    return module, [argv[0], '--check-drift', *argv[1:]]


def _rollup(args, cwd):
    root = _require_root(cwd)
    return 'rollup', [root, *_rewrite_option_path(args, '--only', cwd, root)]


def _import(args, cwd):
    return 'import_from_canvas', [_require_root(cwd), *args]


def _purge(args, cwd):
    return 'purge_course', [_require_root(cwd), *args]


def _init(args, cwd):
    target, rest = _split_file_arg(args)
    return 'init_content_project', [os.path.abspath(os.path.join(cwd, target or '.')), *rest]


def _kit(args, cwd):
    if not args or args[0] != 'update':
        raise CqsError("usage: cqs kit update")
    return 'init_content_project', [_require_root(cwd), '--update', *args[1:]]


VERBS = {
    'check': _check,
    'sync': _sync,
    'diff': _diff,
    'rollup': _rollup,
    'import': _import,
    'purge': _purge,
    'init': _init,
    'kit': _kit,
}


# ---------------------------------------------------------------------------
# where
# ---------------------------------------------------------------------------

def install_dir():
    """Where install.ps1 / install.sh put the tool. Keep in step with venvResolver.ts."""
    if os.name == 'nt':
        base = os.environ.get('LOCALAPPDATA') or os.path.join(os.path.expanduser('~'), 'AppData', 'Local')
        return os.path.join(base, 'CanvasQuartoSync')
    if sys.platform == 'darwin':
        return os.path.join(os.path.expanduser('~'), 'Library', 'Application Support', 'CanvasQuartoSync')
    data = os.environ.get('XDG_DATA_HOME') or os.path.join(os.path.expanduser('~'), '.local', 'share')
    return os.path.join(data, 'canvasquartosync')


def _same(a, b):
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def where(cwd):
    """Which copy runs, with which Python, against which course. Offline."""
    from handlers import __version__

    dev = os.environ.get('CANVAS_QUARTO_SYNC_DIR')
    if dev and _same(dev, TOOL_DIR):
        kind = 'dev clone, from CANVAS_QUARTO_SYNC_DIR'
    elif _same(install_dir(), TOOL_DIR):
        kind = 'installed'
    elif os.path.exists(os.path.join(TOOL_DIR, '.git')):  # a file in a worktree
        kind = 'git clone, not the install location'
    else:
        kind = 'not the install location'

    lines = [
        f"tool:    {TOOL_DIR}  (v{__version__}, {kind})",
        f"python:  {sys.executable}",
    ]
    root = find_course_root(cwd)
    if root is None:
        lines.append(f"course:  none (no config.toml in {cwd} or above)")
    else:
        from handlers.config import load_config
        cfg = load_config(root)
        source = next(m for m in _ROOT_MARKERS if os.path.exists(os.path.join(root, m)))
        lines.append(f"course:  {root}  ({source})")
        cid = cfg.get('course_id')
        if cid:
            name = cfg.get('course_name')
            lines.append(f"canvas:  course {cid}" + (f", {name}" if name else "")
                         + (f" at {cfg['canvas_api_url']}" if cfg.get('canvas_api_url') else ""))
        else:
            lines.append("canvas:  no course_id in config.toml")
    print("\n".join(lines))
    return 0


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _run(module_name, argv, verb):
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError as e:
        if e.name == module_name:
            raise CqsError(f"'cqs {verb}' needs {module_name}.py, which this copy of "
                           f"CanvasQuartoSync does not have. Update the tool.")
        raise
    saved = sys.argv
    # The script's own --help then reads "usage: cqs sync ...".
    sys.argv = [f"cqs {verb}", *argv]
    try:
        rc = module.main()
    except SystemExit as e:
        rc = e.code
    finally:
        sys.argv = saved
    if rc is None:
        return 0
    return rc if isinstance(rc, int) else 1


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    cwd = os.getcwd()

    while args and args[0] == '-C':
        if len(args) < 2:
            print("cqs: -C needs a directory", file=sys.stderr)
            return 2
        cwd = os.path.abspath(os.path.join(cwd, args[1]))
        if not os.path.isdir(cwd):
            print(f"cqs: no such directory: {args[1]}", file=sys.stderr)
            return 2
        args = args[2:]

    if not args or args[0] in ('-h', '--help', 'help'):
        print(USAGE, end='')
        return 0
    if args[0] == '--version':
        from handlers import __version__
        print(f"CanvasQuartoSync {__version__}")
        return 0

    verb, rest = args[0], args[1:]
    try:
        if verb == 'where':
            return where(cwd)
        if verb not in VERBS:
            raise CqsError(f"unknown command '{verb}'. Run 'cqs' for the list.")
        module_name, script_argv = VERBS[verb](rest, cwd)
        return _run(module_name, script_argv, verb)
    except CqsError as e:
        print(f"cqs: {e}", file=sys.stderr)
        return 2


if __name__ == '__main__':
    # Run from anywhere: the scripts import `handlers` relative to the tool.
    if TOOL_DIR not in sys.path:
        sys.path.insert(0, TOOL_DIR)
    sys.exit(main())
