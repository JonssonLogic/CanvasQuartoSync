"""Change the course layout: convert from NN_ prefixes, and the board's edits.

Everything here is local. Nothing talks to Canvas; the next sync carries the
new order there.

    course_board.py ROOT migrate [--dry-run] [--json] [--allow-dirty]
    course_board.py ROOT op '<json>'

``migrate`` turns an NN_ course into order files (``handlers/course_layout``):
it strips the prefixes from module folders and the files directly in them,
writes ``_modules.yml`` and one ``_order.yml`` per module in the old order,
rewrites links and paths that pointed at a renamed file, moves the sync map's
keys and drift snapshots along so every Canvas id is kept, and marks the order
files ``merge=union`` in ``.gitattributes``. Canvas sees the same module names
and item titles afterwards, because both were already shown without prefix.

``op`` is what the Course Board sends after a drag or a button. Each prints
``BOARD_RESULT_JSON:{...}`` on stdout::

    {"op": "move-module", "dir": "Labbar", "index": 0}
    {"op": "move-item", "path": "Labbar/L1.qmd", "to_dir": "Projektet", "index": 2}
    {"op": "add-module", "dir": "Extra"}                  # list an existing folder
    {"op": "remove-module", "dir": "Extra"}               # stop syncing it; files stay
    {"op": "include-item", "path": "Labbar/notes.pdf"}    # list an unlisted file
    {"op": "exclude-item", "path": "Labbar/notes.pdf"}    # unlist it again
"""

import argparse
import json
import os
import re
import subprocess
import sys

from handlers import course_layout as cl
from handlers.content_utils import is_valid_name, load_sync_map, save_sync_map

# Text files whose paths may point at a renamed file.
_TEXT_EXT = {'.qmd', '.md', '.yml', '.yaml', '.json', '.html', '.htm', '.css',
             '.bib', '.txt', '.ipynb', '.lua', '.tex'}
_SKIP_DIRS = {'.git', '.quarto', '_site', '_freeze', 'node_modules', '.venv',
              '.canvas_snapshots', '.canvas_drift', '.canvas_diff_temp', '__pycache__'}
_SKIP_FILES = {'.canvas_sync_map.json'}

# A path-like run of characters: what sits between the delimiters markdown,
# HTML, YAML and Quarto shortcodes put around a path.
_TOKEN = re.compile(r"[^\s()\[\]<>\"'`|,{}*]+")
_SNAPSHOT_DIR = '.canvas_snapshots'


class BoardError(Exception):
    pass


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------

def _strip(name):
    return name[3:] if is_valid_name(name) else name


def plan_migration(root):
    """What ``migrate`` would do, without doing it.

    Returns a dict: ``renames`` [(old rel, new rel)] for folders and files,
    ``modules`` [(folder, display)], ``orders`` {folder: [files]}, ``kept``
    [(rel, reason)] for prefixes that could not be stripped, and ``hidden``
    [(old rel, new rel)] for files that were ignored for lacking a prefix but
    would be picked up as content now, so they get a leading underscore.
    """
    root = os.path.abspath(root)
    if cl.is_ordered(root):
        raise BoardError(f"this course already uses order files ({cl.MODULES_FILE}).")
    layout = cl.load_layout(root)
    if not layout.modules and not layout.root_items:
        raise BoardError("found no NN_ module folders to convert.")

    renames, kept, hidden = [], [], []
    modules, orders = [], {}

    root_names = {n.lower() for n in os.listdir(root)}
    used = set()

    def new_name(old, existing, used_here):
        stripped = _strip(old)
        key = stripped.lower()
        if stripped != old and (key in used_here or (key in existing and key != old.lower())):
            return old, f"'{stripped}' already exists"
        used_here.add(key)
        return stripped, None

    for entry in layout.root_items:
        name, why = new_name(entry, root_names, used)
        if why:
            kept.append((entry, why))
        elif name != entry:
            renames.append((entry, name))
        modules.append((name, None))

    for mod in layout.modules:
        new_dir, why = new_name(mod.dir, root_names, used)
        if why:
            kept.append((mod.dir, why))
        elif new_dir != mod.dir:
            renames.append((mod.dir, new_dir))
        # The module keeps the name Canvas already shows.
        modules.append((new_dir, mod.name if mod.name != new_dir else None))

        mod_path = os.path.join(root, mod.dir)
        here = {n.lower() for n in os.listdir(mod_path)}
        used_in = set()
        order = []
        for f in mod.items:
            nf, why = new_name(f, here, used_in)
            if why:
                kept.append((f"{mod.dir}/{f}", why))
            elif nf != f:
                renames.append((f"{mod.dir}/{f}", f"{new_dir}/{nf}"))
            order.append(nf)
        orders[new_dir] = order

        # Unprefixed files were ignored. If a handler claims one it would now
        # join the module, so hide it the new way instead.
        for f in sorted(os.listdir(mod_path)):
            p = os.path.join(mod_path, f)
            if (os.path.isfile(p) and not is_valid_name(f) and cl._candidate(f)
                    and f.lower() not in used_in and cl.default_is_content(p)):
                target = cl.free_name(mod_path, '_' + f)
                hidden.append((f"{mod.dir}/{f}", f"{new_dir}/{target}"))
    return {'renames': renames, 'modules': modules, 'orders': orders,
            'kept': kept, 'hidden': hidden}


def _git_dirty(root):
    """Uncommitted changes in the course repo, or None outside git."""
    try:
        out = subprocess.run(['git', '-C', root, 'status', '--porcelain'],
                             capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return [l for l in out.stdout.splitlines() if l.strip()]


def _walk_text_files(root):
    for d, dirs, files in os.walk(root):
        dirs[:] = [x for x in dirs if x not in _SKIP_DIRS]
        for f in files:
            if f in _SKIP_FILES:
                continue
            if os.path.splitext(f)[1].lower() in _TEXT_EXT:
                yield os.path.join(d, f)


def _path_map(root, renames):
    """{normcased old abs path: new abs path} for every file and folder that moves.

    A renamed folder moves everything inside it, and a file renamed inside a
    renamed folder moves twice; both are folded into one mapping.
    """
    dir_moves = [(os.path.join(root, o), os.path.join(root, n)) for o, n in renames
                 if os.path.isdir(os.path.join(root, o))]
    file_moves = {os.path.normcase(os.path.join(root, o)): os.path.join(root, n)
                  for o, n in renames if not os.path.isdir(os.path.join(root, o))}

    def moved(old_abs):
        for od, nd in dir_moves:
            if os.path.normcase(old_abs).startswith(os.path.normcase(od) + os.sep):
                return nd + old_abs[len(od):]
        return old_abs

    out = {}
    for od, nd in dir_moves:
        out[os.path.normcase(od)] = nd
        for d, _, files in os.walk(od):
            for f in files:
                p = os.path.join(d, f)
                out[os.path.normcase(p)] = moved(p)
    out.update(file_moves)
    return out


def _rewrite_text(text, old_file, new_file, root, pmap):
    """``text`` with every path that resolves to a moved file updated."""
    old_dir, new_dir = os.path.dirname(old_file), os.path.dirname(new_file)
    count = 0

    def fix(m):
        nonlocal count
        tok = m.group(0)
        core = tok.rstrip('.,;:!?')
        trail = tok[len(core):]
        if not core or '://' in core or core.startswith(('mailto:', 'data:', '#')):
            return tok
        frag = ''
        for sep in ('#', '?'):
            if sep in core:
                core, rest = core.split(sep, 1)
                frag = sep + rest + frag
                break
        if not core:
            return tok
        from_root = core.startswith('/')
        base = root if from_root else old_dir
        target = os.path.normpath(os.path.join(base, core.lstrip('/') if from_root else core))
        new_target = pmap.get(os.path.normcase(target))
        if new_target is None:
            return tok
        if from_root:
            new = '/' + os.path.relpath(new_target, root).replace('\\', '/')
        else:
            new = os.path.relpath(new_target, new_dir).replace('\\', '/')
            if core.startswith('./') and not new.startswith('.'):
                new = './' + new
        if new == core:
            return tok
        count += 1
        return new + frag + trail

    return _TOKEN.sub(fix, text), count


def migrate(root, dry_run=False, allow_dirty=False):
    """Convert an NN_ course to order files. Returns a report dict."""
    root = os.path.abspath(root)
    plan = plan_migration(root)
    report = {'dry_run': dry_run, **{k: plan[k] for k in ('renames', 'kept', 'hidden')},
              'modules': [d for d, _ in plan['modules']], 'links': [], 'map_keys': 0}

    dirty = _git_dirty(root)
    report['git'] = dirty is not None
    if dirty and not allow_dirty and not dry_run:
        raise BoardError(
            "the course has uncommitted changes. Commit or stash them first, so the "
            "conversion is one commit of its own that is easy to review or undo. "
            "(--allow-dirty skips this check.)")

    all_moves = plan['renames'] + plan['hidden']
    pmap = _path_map(root, all_moves)

    # Rewrite references in memory first, at the files' old locations.
    rewritten = {}
    for path in _walk_text_files(root):
        try:
            with open(path, 'r', encoding='utf-8', newline='') as f:
                text = f.read()
        except (OSError, UnicodeDecodeError):
            continue
        new_path = pmap.get(os.path.normcase(path), path)
        new_text, n = _rewrite_text(text, path, new_path, root, pmap)
        if n:
            rewritten[new_path] = new_text
            report['links'].append((os.path.relpath(new_path, root).replace('\\', '/'), n))

    if dry_run:
        return report

    # Files inside a module first (still under the old folder name), then the
    # folders themselves.
    def do_rename(old_rel, new_rel_final):
        old_abs = os.path.join(root, old_rel)
        # The folder part of new_rel may not exist yet: rename within the old folder.
        parent_old = os.path.dirname(old_abs)
        target = os.path.join(parent_old, os.path.basename(new_rel_final))
        if os.path.normcase(old_abs) != os.path.normcase(target):
            os.rename(old_abs, target)

    file_moves = [(o, n) for o, n in all_moves if '/' in o]
    top_moves = [(o, n) for o, n in all_moves if '/' not in o]
    try:
        for o, n in file_moves:
            do_rename(o, n)
        for o, n in top_moves:
            do_rename(o, n)

        for path, text in rewritten.items():
            with open(path, 'w', encoding='utf-8', newline='') as f:
                f.write(text)
    except OSError as e:
        # Typically a file held open by another program (Windows, Dropbox).
        if dirty is not None:
            back = ("The course was committed before this started, so 'git status' shows "
                    "what changed and 'git restore' / 'git clean' take it back.")
        else:
            back = "Undo the renames listed by 'cqs migrate --dry-run' by hand."
        raise BoardError(f"stopped partway: {e}. Close programs that have course files open "
                         f"and try again. {back}")

    # Sync map and drift snapshots follow their files.
    rel_map = {}
    for old_norm, new_abs in pmap.items():
        rel_map[os.path.relpath(old_norm, os.path.normcase(root)).replace('\\', '/')] = \
            os.path.relpath(new_abs, root).replace('\\', '/')
    sync_map = load_sync_map(root)
    if sync_map:
        moved = {}
        for k, v in sync_map.items():
            nk = rel_map.get(os.path.normcase(k).replace('\\', '/'), k)
            if nk != k:
                report['map_keys'] += 1
                _move_snapshot(root, k, nk)
            moved[nk] = v
        if report['map_keys']:
            save_sync_map(root, moved)

    cl.write_modules(root, plan['modules'])
    for d, files in plan['orders'].items():
        cl.write_order(root, d, files)
    report['gitattributes'] = cl.ensure_gitattributes(root)
    return report


def _snapshot_name(rel):
    return rel.replace('/', '__').replace(' ', '_') + '.html'


def _move_snapshot(root, old_rel, new_rel):
    src = os.path.join(root, _SNAPSHOT_DIR, _snapshot_name(old_rel))
    if os.path.isfile(src):
        os.replace(src, os.path.join(root, _SNAPSHOT_DIR, _snapshot_name(new_rel)))


def format_report(r):
    verb = 'Would rename' if r['dry_run'] else 'Renamed'
    out = []
    out.append(f"{verb} {len(r['renames'])} folders and files:")
    out += [f"  {o}  ->  {n}" for o, n in r['renames']]
    if r['hidden']:
        out.append("Kept out of the course with a leading _ (they were ignored before "
                   "for lacking a prefix, and have a canvas.type):")
        out += [f"  {o}  ->  {n}" for o, n in r['hidden']]
    if r['kept']:
        out.append("Kept the prefix, because the plain name is taken:")
        out += [f"  {p}  ({why})" for p, why in r['kept']]
    if r['links']:
        out.append(f"{'Would update' if r['dry_run'] else 'Updated'} paths in {len(r['links'])} files:")
        out += [f"  {p}  ({n})" for p, n in r['links']]
    out.append(f"Module order in {cl.MODULES_FILE}: " + ', '.join(r['modules']))
    if not r['dry_run']:
        out.append(f"Sync map entries moved: {r['map_keys']}")
        out.append("Done. Nothing in Canvas changes until the next sync, which shows the same "
                   "module names and titles.")
        if r.get('git'):
            out.append("Review with 'git status' and commit it as one change. "
                       "In a team course, tell the others to pull before they go on.")
    return '\n'.join(out)


# ---------------------------------------------------------------------------
# Board operations
# ---------------------------------------------------------------------------

def _require_ordered(root):
    if not cl.is_ordered(root):
        raise BoardError("this course still uses NN_ prefixes. Convert it first "
                         "(cqs migrate, or Convert in the Course Board).")


def _split(rel):
    parts = rel.replace('\\', '/').strip('/').split('/')
    if len(parts) != 2:
        raise BoardError(f"not a file directly in a module folder: {rel}")
    return parts


def _fix_links_after_move(root, old_rel, new_rel):
    """A file moved to another folder: update links to it, and its own links."""
    pmap = {os.path.normcase(os.path.join(root, old_rel)): os.path.join(root, new_rel)}
    changed = 0
    for path in _walk_text_files(root):
        is_self = os.path.normcase(path) == os.path.normcase(os.path.join(root, new_rel))
        old_loc = os.path.join(root, old_rel) if is_self else path
        try:
            with open(path, 'r', encoding='utf-8', newline='') as f:
                text = f.read()
        except (OSError, UnicodeDecodeError):
            continue
        new_text, n = _rewrite_text(text, old_loc, path, root, pmap)
        if is_self:
            # Its own relative links still point from the old folder.
            new_text, m = _relink_self(new_text, old_loc, path, root)
            n += m
        if n:
            with open(path, 'w', encoding='utf-8', newline='') as f:
                f.write(new_text)
            changed += 1
    return changed


def _relink_self(text, old_file, new_file, root):
    """Relative paths in a moved file, re-pointed from its new folder."""
    old_dir, new_dir = os.path.dirname(old_file), os.path.dirname(new_file)
    if os.path.normcase(old_dir) == os.path.normcase(new_dir):
        return text, 0
    count = 0

    def fix(m):
        nonlocal count
        tok = m.group(0)
        core = tok.rstrip('.,;:!?')
        trail = tok[len(core):]
        if not core or core.startswith(('/', '#', 'mailto:', 'data:')) or '://' in core:
            return tok
        path, frag = core, ''
        for sep in ('#', '?'):
            if sep in path:
                path, rest = path.split(sep, 1)
                frag = sep + rest
                break
        target = os.path.normpath(os.path.join(old_dir, path))
        # Only paths that existed from the old place, and that did not exist
        # from the new one already.
        if not path or not os.path.exists(target) or os.path.exists(os.path.join(new_dir, path)):
            return tok
        count += 1
        return os.path.relpath(target, new_dir).replace('\\', '/') + frag + trail

    return _TOKEN.sub(fix, text), count


def apply_op(root, op):
    root = os.path.abspath(root)
    kind = op.get('op')
    _require_ordered(root)
    layout = cl.load_layout(root)

    if kind == 'move-module':
        dirs = [m.dir for m in layout.modules if m.dir.lower() != op['dir'].lower()]
        if len(dirs) == len(layout.modules):
            raise BoardError(f"no module folder '{op['dir']}' in {cl.MODULES_FILE}")
        idx = max(0, min(int(op.get('index', len(dirs))), len(dirs)))
        dirs.insert(idx, layout.module(op['dir']).dir)
        cl.set_module_order(root, dirs)
        return {'message': f"Moved module {op['dir']}"}

    if kind == 'move-item':
        src_dir, fname = _split(op['path'])
        src = layout.module(src_dir)
        dst = layout.module(op.get('to_dir') or src_dir)
        if src is None or dst is None:
            raise BoardError("both folders must be modules in the course")
        if not os.path.isfile(os.path.join(root, src.dir, fname)):
            raise BoardError(f"no file {src.dir}/{fname}")
        new_name = fname
        moved_folder = src.dir.lower() != dst.dir.lower()
        if moved_folder:
            new_name = cl.free_name(os.path.join(root, dst.dir), fname)
            old_rel, new_rel = f"{src.dir}/{fname}", f"{dst.dir}/{new_name}"
            os.rename(os.path.join(root, old_rel), os.path.join(root, new_rel))
            _rekey(root, old_rel, new_rel)
            _fix_links_after_move(root, old_rel, new_rel)
            cl.set_item_order(root, src.dir, [f for f in src.items if f.lower() != fname.lower()])
        items = [f for f in dst.items if f.lower() != fname.lower()] if not moved_folder else list(dst.items)
        idx = max(0, min(int(op.get('index', len(items))), len(items)))
        items.insert(idx, new_name)
        cl.set_item_order(root, dst.dir, items)
        where = f" to {dst.name}" if moved_folder else ''
        return {'message': f"Moved {fname}{where}", 'path': f"{dst.dir}/{new_name}"}

    if kind == 'add-module':
        d = op['dir']
        if not os.path.isdir(os.path.join(root, d)):
            raise BoardError(f"no folder '{d}'")
        cl.add_module(root, d, op.get('name'), op.get('index'))
        return {'message': f"Added {d} to the course"}

    if kind == 'remove-module':
        cl.remove_module(root, op['dir'])
        return {'message': f"{op['dir']} is no longer synced. Its files are untouched; "
                           f"the module stays in Canvas until you delete it there."}

    if kind in ('include-item', 'exclude-item'):
        d, fname = _split(op['path'])
        mod = layout.module(d)
        if mod is None:
            raise BoardError(f"'{d}' is not a module in the course")
        items = [f for f in mod.items if f.lower() != fname.lower()]
        if kind == 'include-item':
            items.append(fname)
        cl.set_item_order(root, mod.dir, items)
        return {'message': ('Added ' if kind == 'include-item' else 'Removed ') + fname}

    raise BoardError(f"unknown op: {kind}")


def _rekey(root, old_rel, new_rel):
    sync_map = load_sync_map(root)
    if old_rel in sync_map:
        sync_map[new_rel] = sync_map.pop(old_rel)
        save_sync_map(root, sync_map)
        _move_snapshot(root, old_rel, new_rel)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Change the course layout. Local only; never touches Canvas.")
    parser.add_argument('content_path', nargs='?', default='.')
    sub = parser.add_subparsers(dest='cmd', required=True)
    m = sub.add_parser('migrate', help='Convert NN_ prefixes to order files.')
    m.add_argument('--dry-run', action='store_true', help='Show what would change, change nothing.')
    m.add_argument('--json', action='store_true', help='Print the report as JSON.')
    m.add_argument('--allow-dirty', action='store_true', help='Convert even with uncommitted changes.')
    o = sub.add_parser('op', help='Apply one Course Board edit, given as JSON.')
    o.add_argument('json')
    args = parser.parse_args()
    root = os.path.abspath(args.content_path)
    # Course files have å, ä and ö in their names; a cp1252 console must not
    # turn the report into a crash.
    try:
        sys.stdout.reconfigure(errors='replace')
    except (AttributeError, ValueError):
        pass

    if args.cmd == 'migrate':
        try:
            report = migrate(root, dry_run=args.dry_run, allow_dirty=args.allow_dirty)
        except BoardError as e:
            if args.json:
                print(json.dumps({'success': False, 'error': str(e)}))
            else:
                print(f"Not converted: {e}", file=sys.stderr)
            return 1
        if args.json:
            print(json.dumps({'success': True, **report}))
        else:
            print(format_report(report))
        return 0

    try:
        result = {'success': True, **apply_op(root, json.loads(args.json))}
    except (BoardError, KeyError, ValueError, OSError) as e:
        result = {'success': False, 'error': str(e)}
    print('BOARD_RESULT_JSON:' + json.dumps(result))
    return 0 if result['success'] else 1


if __name__ == '__main__':
    sys.exit(main())
