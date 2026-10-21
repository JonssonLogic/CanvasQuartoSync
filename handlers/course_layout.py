"""Which modules a course has, in what order, and which files are in each.

Every part of the tool that walks the course asks this module instead of
listing directories itself: the sync, single-file sync, the validator, the
module panel and rollups. There are two ways a course can say it:

**Order files** (``_modules.yml`` at the course root). The module list::

    - Kursdokumentation
    - Elektroniklabbar: Elektronik - labbar     # folder: Canvas name
    - Startsida.qmd                             # a file: synced outside any module

and in each module folder an optional ``_order.yml``::

    - KursPM.qmd
    - Kursplan.pdf

A folder is a module only if ``_modules.yml`` lists it, so ``graphics/`` and
drafts stay out. Inside a module, a file is included if ``_order.yml`` lists it
or if it declares itself as content (a handler claims it: it has a
``canvas.type``, or it is a quiz). Unlisted content is added at the end, so a
new page needs no bookkeeping. Plain notes, PDFs and images are included only
when listed. A name starting with ``_`` or ``.`` is never picked up on its own.

**NN_ prefixes** (no ``_modules.yml``). The original layout: ``NN_`` folders
are modules, ``NN_`` files are items, and the prefix sets the order.

The order files are written one entry per line so git can merge them line by
line, and ``.gitattributes`` marks them ``merge=union`` so a merge never
stops on them. Reading is therefore tolerant: a duplicate keeps its first
position, an entry whose file is gone is skipped with a warning, and nothing
here raises on a malformed file.
"""

import os
import re
from dataclasses import dataclass, field

import yaml

from handlers.content_utils import is_valid_name, parse_module_name

MODULES_FILE = '_modules.yml'
ORDER_FILE = '_order.yml'

ORDER_FILES = 'order-files'
NN_PREFIX = 'nn-prefix'

# Written next to the file being rendered and deleted afterwards; never content.
_TRANSIENT = ('_temp_', 'tmp-')

_MODULES_HEADER = (
    "# Modules in Canvas order, top to bottom. One folder per line.\n"
    "# A different name in Canvas: \"- Folder: Name in Canvas\".\n"
    "# A folder that is not listed here is not synced.\n"
)
_ORDER_HEADER = (
    "# Items in this module, top to bottom. One file per line.\n"
    "# Pages, assignments and quizzes that are not listed are added at the end.\n"
    "# Other files (PDFs, notes) are synced only when listed.\n"
)

GITATTRIBUTES_LINES = (
    f"{MODULES_FILE} merge=union",
    f"{ORDER_FILE} merge=union",
)


@dataclass
class Problem:
    level: str      # 'error' | 'warning' | 'info'
    message: str
    path: str = ''  # course-relative, '/' separated


@dataclass
class Module:
    dir: str                                       # folder, relative to the root
    name: str                                      # Canvas module name
    items: list = field(default_factory=list)      # filenames, in order
    unlisted: list = field(default_factory=list)   # files present but not included
    auto_added: list = field(default_factory=list)  # included without being listed

    def item_paths(self):
        return [f"{self.dir}/{f}" for f in self.items]


@dataclass
class Layout:
    root: str
    mode: str
    modules: list = field(default_factory=list)
    root_items: list = field(default_factory=list)   # files synced outside any module
    unlisted_dirs: list = field(default_factory=list)
    problems: list = field(default_factory=list)

    @property
    def ordered(self):
        return self.mode == ORDER_FILES

    def module(self, dir_name):
        key = dir_name.lower()
        return next((m for m in self.modules if m.dir.lower() == key), None)

    def locate(self, path):
        """``(module or None, included)`` for a file, absolute or course-relative.

        ``module`` is None for a root item. ``included`` is False for a file
        the sync would skip.
        """
        rel = _rel(self.root, path)
        parts = rel.split('/')
        if len(parts) == 1:
            return None, any(f.lower() == rel.lower() for f in self.root_items)
        if len(parts) != 2:
            return None, False
        mod = self.module(parts[0])
        if mod is None:
            return None, False
        return mod, any(f.lower() == parts[1].lower() for f in mod.items)

    def why_excluded(self, path):
        """One sentence on why the sync skips ``path``, or None if it does not."""
        rel = _rel(self.root, path)
        parts = rel.split('/')
        mod, included = self.locate(rel)
        if included:
            return None
        name = parts[-1]
        if not self.ordered:
            if len(parts) == 2 and not is_valid_name(parts[0]):
                return f"its folder '{parts[0]}' has no NN_ prefix, so the sync does not walk into it"
            if not is_valid_name(name):
                return f"'{name}' has no NN_ prefix, so the sync ignores it"
            return "it is not directly inside a module folder"
        if len(parts) == 1:
            return f"'{name}' is at the course root and not listed in {MODULES_FILE}"
        if len(parts) > 2:
            return "it is not directly inside a module folder"
        if mod is None:
            return f"its folder '{parts[0]}' is not listed in {MODULES_FILE}"
        if name.startswith(('_', '.')):
            return f"names starting with '_' are kept out unless listed in {parts[0]}/{ORDER_FILE}"
        return (f"it has no canvas.type, so it is included only when listed in "
                f"{parts[0]}/{ORDER_FILE}")

    def to_dict(self):
        return {
            'mode': self.mode,
            'modules': [{'dir': m.dir, 'name': m.name, 'items': m.items,
                         'unlisted': m.unlisted, 'auto_added': m.auto_added}
                        for m in self.modules],
            'root_items': self.root_items,
            'unlisted_dirs': self.unlisted_dirs,
            'problems': [p.__dict__ for p in self.problems],
        }


def _rel(root, path):
    if os.path.isabs(path):
        path = os.path.relpath(path, root)
    path = path.replace('\\', '/')
    while path.startswith('./'):
        path = path[2:]
    return path


def is_ordered(root):
    return os.path.isfile(os.path.join(root, MODULES_FILE))


def default_is_content(path):
    """Does a handler claim this file? The same chain the sync dispatches to."""
    from handlers.single_sync import build_handlers  # imports every handler
    global _HANDLERS
    if _HANDLERS is None:
        _HANDLERS = build_handlers()
    return any(h.can_handle(path) for h in _HANDLERS)


_HANDLERS = None


def _listdir(path):
    try:
        return sorted(os.listdir(path))
    except OSError:
        return []


def _candidate(name):
    """Could this file be an item at all? Excludes our own and Quarto's files."""
    return not name.startswith(('.', '_')) and not name.startswith(_TRANSIENT)


# ---------------------------------------------------------------------------
# Reading and writing the order files
# ---------------------------------------------------------------------------

def _parse_entry(value):
    """``'Folder'`` or ``{'Folder': 'Name'}`` -> (name, display or None)."""
    if isinstance(value, dict) and len(value) == 1:
        (k, v), = value.items()
        return str(k).strip(), (str(v).strip() if v is not None else None)
    if isinstance(value, (str, int, float)):
        return str(value).strip(), None
    return None, None


def read_list(path, problems=None, rel=''):
    """Entries of an order file as ``[(name, display or None)]``, in file order.

    YAML first. Anything else (a merge gone wrong, conflict markers, a typo)
    falls back to reading ``- name`` lines, so a damaged file still yields as
    much order as it holds instead of failing the sync.
    """
    if problems is None:
        problems = []
    try:
        with open(path, 'r', encoding='utf-8') as f:
            text = f.read()
    except OSError:
        return []

    if re.search(r'^(<{7}|={7}|>{7})', text, re.M):
        problems.append(Problem('warning', 'has git conflict markers. The lines between '
                                'them were read as ordinary entries; tidy it up.', rel))
    else:
        try:
            data = yaml.safe_load(text)
            if data is None:
                return []
            if isinstance(data, list):
                out = []
                for v in data:
                    name, disp = _parse_entry(v)
                    if name:
                        out.append((name, disp))
                    else:
                        problems.append(Problem('warning', f'ignored an entry that is not a name: {v!r}', rel))
                return out
            problems.append(Problem('error', 'must be a list, one "- name" per line.', rel))
            return []
        except yaml.YAMLError:
            problems.append(Problem('warning', 'is not valid YAML. Read it line by line instead.', rel))

    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith('- '):
            continue
        value = line[2:].split(' #', 1)[0].strip()
        disp = None
        if ': ' in value:
            value, disp = (s.strip() for s in value.split(': ', 1))
            disp = disp.strip('"\'') or None
        value = value.strip('"\'')
        if value:
            out.append((value, disp))
    return out


def _quote(s):
    """A scalar YAML will read back as the same string."""
    if re.match(r'^[^\s\'"#:\-?\[\]{},&*!|>%@`][^:#]*$', s) and s == s.strip() and \
            not re.match(r'^(true|false|yes|no|on|off|null|~|[-+]?\d[\d._]*)$', s, re.I):
        return s
    return '"' + s.replace('\\', '\\\\').replace('"', '\\"') + '"'


def write_modules(root, entries):
    """Write ``_modules.yml``. ``entries`` is ``[(folder or file, display or None)]``."""
    lines = [_MODULES_HEADER]
    for name, disp in entries:
        lines.append(f"- {_quote(name)}: {_quote(disp)}\n" if disp and disp != name
                     else f"- {_quote(name)}\n")
    _write(os.path.join(root, MODULES_FILE), ''.join(lines))


def write_order(root, dir_name, filenames):
    lines = [_ORDER_HEADER] + [f"- {_quote(f)}\n" for f in filenames]
    _write(os.path.join(root, dir_name, ORDER_FILE), ''.join(lines))


def _write(path, text):
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        f.write(text)


def ensure_gitattributes(root):
    """Mark the order files merge=union. Returns True if the file changed."""
    path = os.path.join(root, '.gitattributes')
    try:
        with open(path, 'r', encoding='utf-8') as f:
            text = f.read()
    except OSError:
        text = ''
    missing = [l for l in GITATTRIBUTES_LINES if l not in text.splitlines()]
    if not missing:
        return False
    block = ("# Course Board order files: keep both sides of a merge. The tool\n"
             "# drops duplicate lines when it reads them.\n" + '\n'.join(missing) + '\n')
    if text and not text.endswith('\n'):
        text += '\n'
    _write(path, text + ('\n' if text else '') + block)
    return True


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def load_layout(root, is_content=None):
    """The course's modules and items. Never raises on bad content."""
    root = os.path.abspath(root)
    if is_ordered(root):
        return _load_ordered(root, is_content or default_is_content)
    return _load_nn(root)


def _load_nn(root):
    layout = Layout(root=root, mode=NN_PREFIX)
    for entry in _listdir(root):
        path = os.path.join(root, entry)
        if not is_valid_name(entry):
            continue
        if os.path.isdir(path):
            mod = Module(dir=entry, name=parse_module_name(entry))
            for f in _listdir(path):
                if os.path.isfile(os.path.join(path, f)) and is_valid_name(f):
                    mod.items.append(f)
            layout.modules.append(mod)
        elif os.path.isfile(path):
            layout.root_items.append(entry)
    return layout


def _actual_names(directory):
    """``{lowercase name: name on disk}``: order files match case-insensitively,
    as the file systems most teachers use do."""
    return {n.lower(): n for n in _listdir(directory)}


def _load_ordered(root, is_content):
    layout = Layout(root=root, mode=ORDER_FILES)
    on_disk = _actual_names(root)
    seen = set()

    for name, disp in read_list(os.path.join(root, MODULES_FILE), layout.problems, MODULES_FILE):
        key = name.strip('/').lower()
        if key in seen:
            continue
        seen.add(key)
        actual = on_disk.get(key)
        if actual is None:
            layout.problems.append(Problem(
                'warning', f"lists '{name}', which does not exist. Skipped.", MODULES_FILE))
            continue
        path = os.path.join(root, actual)
        if os.path.isdir(path):
            layout.modules.append(_load_module(root, actual, disp or actual, is_content, layout.problems))
        else:
            layout.root_items.append(actual)

    for entry in _listdir(root):
        path = os.path.join(root, entry)
        if entry.lower() in seen or not os.path.isdir(path) or not _candidate(entry):
            continue
        if any(_candidate(f) and os.path.isfile(os.path.join(path, f)) and is_content(os.path.join(path, f))
               for f in _listdir(path)):
            layout.unlisted_dirs.append(entry)
    return layout


def _load_module(root, dir_name, display, is_content, problems):
    mod = Module(dir=dir_name, name=display)
    mod_path = os.path.join(root, dir_name)
    order_rel = f"{dir_name}/{ORDER_FILE}"
    files = {n.lower(): n for n in _listdir(mod_path) if os.path.isfile(os.path.join(mod_path, n))}
    files.pop(ORDER_FILE.lower(), None)

    taken = set()
    if os.path.isfile(os.path.join(mod_path, ORDER_FILE)):
        for name, _ in read_list(os.path.join(mod_path, ORDER_FILE), problems, order_rel):
            key = name.lower()
            if key in taken:
                continue
            actual = files.get(key)
            if actual is None:
                problems.append(Problem('warning', f"lists '{name}', which does not exist. Skipped.", order_rel))
                continue
            taken.add(key)
            mod.items.append(actual)

    for key, actual in sorted(files.items(), key=lambda kv: kv[1]):
        if key in taken or not _candidate(actual):
            continue
        if is_content(os.path.join(mod_path, actual)):
            mod.items.append(actual)
            mod.auto_added.append(actual)
        else:
            mod.unlisted.append(actual)
    return mod


# ---------------------------------------------------------------------------
# Changing the order (the Course Board's drag and drop)
# ---------------------------------------------------------------------------

def module_entries(root):
    """``_modules.yml`` as written, duplicates and missing folders dropped."""
    entries, seen = [], set()
    on_disk = _actual_names(root)
    for name, disp in read_list(os.path.join(root, MODULES_FILE)):
        actual = on_disk.get(name.strip('/').lower())
        if actual and actual.lower() not in seen:
            seen.add(actual.lower())
            entries.append((actual, disp))
    return entries


def set_module_order(root, dirs):
    """Rewrite ``_modules.yml`` in the order of ``dirs``, keeping display names.

    Entries not in ``dirs`` (root items, say) keep their place relative to
    each other and go after the ones that were given.
    """
    current = module_entries(root)
    by_key = {n.lower(): (n, d) for n, d in current}
    out, used = [], set()
    for d in dirs:
        hit = by_key.get(d.lower())
        if hit and d.lower() not in used:
            out.append(hit)
            used.add(d.lower())
    out += [e for e in current if e[0].lower() not in used]
    write_modules(root, out)


def add_module(root, dir_name, display=None, index=None):
    entries = module_entries(root)
    if any(n.lower() == dir_name.lower() for n, _ in entries):
        return False
    entry = (dir_name, display if display and display != dir_name else None)
    entries.insert(len(entries) if index is None else max(0, min(index, len(entries))), entry)
    write_modules(root, entries)
    return True


def remove_module(root, dir_name):
    entries = [e for e in module_entries(root) if e[0].lower() != dir_name.lower()]
    write_modules(root, entries)


def free_name(directory, filename):
    """``filename``, or ``stem_2.ext``, ``stem_3.ext``... if it is taken."""
    taken = {n.lower() for n in _listdir(directory)}
    if filename.lower() not in taken:
        return filename
    stem, ext = os.path.splitext(filename)
    n = 2
    while f"{stem}_{n}{ext}".lower() in taken:
        n += 1
    return f"{stem}_{n}{ext}"


def append_item(root, dir_name, filename, module_name=None):
    """Put ``filename`` last in its module, listing the module if it is new."""
    if not any(n.lower() == dir_name.lower() for n, _ in module_entries(root)):
        add_module(root, dir_name, module_name)
    mod = load_layout(root).module(dir_name)
    items = [f for f in (mod.items if mod else []) if f.lower() != filename.lower()]
    set_item_order(root, dir_name, items + [filename])


def set_item_order(root, dir_name, filenames):
    """Write a module's ``_order.yml`` as exactly ``filenames``."""
    seen, out = set(), []
    for f in filenames:
        if f.lower() not in seen:
            seen.add(f.lower())
            out.append(f)
    write_order(root, dir_name, out)
