"""
Table styling, inlined at sync time (#47).

Canvas strips ``<style>`` blocks, so a table reaches Canvas as markup only. Its
look then comes from Canvas's own legacy Bootstrap 2 stylesheet, which happens
to style some of the class names Quarto puts on a table (``table-striped``,
``table-bordered``, ...). Those stripes are nearly invisible and none of the
colours can be changed, so a course can have cqs draw its tables instead, with
inline styles. They sit on the elements of one table, so they cannot reach
another table, and they beat Canvas's class rules without ``!important``.

Each layer overrides the one before it, for the tables it reaches:

  0. Canvas        no table settings in the course: the HTML is left untouched
  1. Course        ``[tables] style`` in config.toml, and ``--table-*`` values on
                   ``:root`` in branding.css. Either one makes cqs draw every
                   table Quarto made from Markdown.
  2. Named style   a class on one table whose branding.css rule sets
                   ``--table-*`` values (``.schema { --table-stripe: ... }``)
  3. One table     a ``::: {.cqs-table stripe="..."}`` wrapper around it

A table's own caption classes (``.striped``, ``.bordered``, ...) replace the
course's ``[tables] style``, and ``.plain`` opts a table out entirely.

What there is to style lives in three registries: TABLE_VALUES (what a course
can set), TABLE_FEATURES (what a table can ask for) and TARGETS (which part of
a table a rule styles). Adding a style is adding entries there; the sync, the
validator and tests/unit/test_doc_consistency.py all read them. See
DEVELOPER_GUIDE.md, "Adding a table style".
"""

import os
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

from handlers.config import load_config
from handlers.log import logger


# ---------------------------------------------------------------------------
# Registries
# ---------------------------------------------------------------------------

# How each kind of value is checked before it goes into a style attribute.
# Anything that passes is safe inside style="...".
VALUE_KINDS = {
    'color': (
        re.compile(r'#[0-9a-fA-F]{3,4}|#[0-9a-fA-F]{6}|#[0-9a-fA-F]{8}'
                   r'|(?:rgba?|hsla?)\([0-9.,%\s/+-]*\)|[a-zA-Z]+'),
        'a colour such as #003865, rgb(0, 56, 101) or white',
    ),
}


@dataclass(frozen=True)
class TableValue:
    """Something a course can set: ``--table-<name>`` in branding.css, or
    ``<name>="..."`` on a ``.cqs-table`` wrapper."""
    name: str
    default: str = ''        # what a feature uses when nothing sets it; '' = none
    kind: str = 'color'
    note: str = ''

    @property
    def prop(self):
        return '--table-' + self.name


@dataclass(frozen=True)
class Rule:
    """Declarations for the elements ``target`` names. ``{name}`` in ``css``
    stands for that value, and the rule is skipped when there is none. It is
    also skipped for a table with any feature in ``unless``."""
    target: str
    css: str
    unless: tuple = ()

    @property
    def uses(self):
        return re.findall(r'\{([\w-]+)\}', self.css)


@dataclass(frozen=True)
class TableFeature:
    """Something a table asks for: ``.<name>`` on its caption line, or
    ``"<name>"`` in config.toml's ``[tables] style``."""
    name: str
    quarto_class: str        # the class Quarto puts on <table> for .<name>
    rules: tuple = ()
    # Left to Canvas: the class is kept, or added, for Canvas's stylesheet to
    # act on. Hover needs :hover, which no inline style can express.
    canvas_draws: bool = False
    note: str = ''


@dataclass(frozen=True)
class Element:
    """What a target can know about an element of a table."""
    kind: str                # "table", "row" or "cell"
    section: str = ''        # "head", "body" or "foot"; rows and cells only
    row: int = 0             # 0-based, counted within the section
    col: int = 0             # 0-based, counted in cells, not columns


# Which elements a rule styles. A new target, such as the first column, is a
# new entry here.
TARGETS = {
    'table': lambda e: e.kind == 'table',
    # The 1st, 3rd, 5th... body row: the rows Canvas, Bootstrap and Quarto stripe.
    'odd_row': lambda e: e.kind == 'row' and e.section == 'body' and e.row % 2 == 0,
    'head_cell': lambda e: e.kind == 'cell' and e.section == 'head',
    'body_cell': lambda e: e.kind == 'cell' and e.section != 'head',
    'cell': lambda e: e.kind == 'cell',
}


def _by_name(*entries):
    return {e.name: e for e in entries}


TABLE_VALUES = _by_name(
    TableValue('stripe', '#f0f4f7', note='background of the striped rows'),
    TableValue('border', '#dddddd', note='colour of the lines between rows, and around cells'),
    TableValue('header-bg', note='background of the header row'),
    TableValue('header-fg', note='text colour of the header row'),
)

TABLE_FEATURES = _by_name(
    TableFeature(
        'striped', 'table-striped',
        # On the row rather than its cells, so that a hover rule on the cells
        # (Canvas's .table-hover is one) is not hidden on a striped row.
        rules=(Rule('odd_row', 'background-color: {stripe}'),),
        note='a background on every other body row'),
    TableFeature(
        'bordered', 'table-bordered',
        rules=(Rule('table', 'border-collapse: collapse'),
               Rule('cell', 'border: 1px solid {border}')),
        note='lines around every cell'),
    TableFeature(
        'sm', 'table-sm',
        rules=(Rule('cell', 'padding: 4px 5px'),),
        note='tighter cell padding'),
    TableFeature(
        'hover', 'table-hover', canvas_draws=True,
        note="passed on to Canvas as table-hover, which shows no visible highlight"),
)

# Rules every table cqs draws gets, whatever it asks for, but only where some
# layer sets the values a rule uses: a default never switches one on.
VALUE_RULES = (
    Rule('head_cell', 'background-color: {header-bg}'),
    Rule('head_cell', 'color: {header-fg}'),
    # Canvas draws a #ddd line above each body row; this recolours it.
    Rule('body_cell', 'border-top: 1px solid {border}', unless=('bordered',)),
)

WRAPPER_CLASS = 'cqs-table'
PLAIN_CLASS = 'plain'
# Quarto puts this class on every table it makes from Markdown. Hand-written
# HTML tables lack it, and course-wide styling leaves them alone.
QUARTO_TABLE_CLASS = 'table'


def check_value(name, raw):
    """``raw`` as a value for ``name``, or ValueError saying why it is not one."""
    spec = TABLE_VALUES[name]
    pattern, described = VALUE_KINDS[spec.kind]
    value = (raw or '').strip()
    if not pattern.fullmatch(value):
        raise ValueError(f"{value!r} is not {described}")
    return value


def names(registry):
    """'a, b, c', for messages that list what is valid."""
    return ', '.join(registry)


# ---------------------------------------------------------------------------
# A course's settings
# ---------------------------------------------------------------------------

@dataclass
class TableSettings:
    """A course's table settings, from config.toml and branding.css."""
    on: bool = False         # course-wide styling: [tables], or :root values
    default: tuple = ()      # features from [tables] style
    root: dict = field(default_factory=dict)    # value name -> value, from :root
    named: dict = field(default_factory=dict)   # class -> {value name: value}, file order
    custom: dict = field(default_factory=dict)  # every :root custom property, for var()
    problems: list = field(default_factory=list)  # (file, message): settings ignored
    warned: bool = False


_settings_cache = {}


def load_table_settings(content_root, warn=False):
    """The course's table settings, cached per content root.

    ``warn`` logs the settings that are ignored, once per course; the sync
    asks for that, the validator reports them itself.
    """
    if content_root not in _settings_cache:
        settings = TableSettings()
        if content_root:
            cfg = load_config(content_root)
            _read_config(settings, cfg)
            css = cfg.get('branding', {}).get('css', '')
            if css:
                path = css if os.path.isabs(css) else os.path.join(content_root, css)
                _read_branding(settings, path, css)
            settings.on = settings.on or bool(settings.root)
        _settings_cache[content_root] = settings
    settings = _settings_cache[content_root]
    if warn and not settings.warned:
        settings.warned = True
        for file, message in settings.problems:
            logger.warning("    [yellow]Table styling:[/yellow] %s: %s - ignored", file, message)
    return settings


def _read_config(settings, cfg):
    tables = cfg.get('tables')
    if tables is None:
        return
    if not isinstance(tables, dict):
        settings.problems.append(
            ('config.toml', 'tables must be a [tables] section, e.g. style = ["striped"]'))
        return
    settings.on = True
    for key in tables:
        if key != 'style':
            settings.problems.append(
                ('config.toml', f"[tables] {key}: not a setting; the one setting is 'style'"))
    style = tables.get('style', [])
    if isinstance(style, str):
        style = [style]
    if not isinstance(style, list):
        settings.problems.append(
            ('config.toml', '[tables] style must be a list, e.g. style = ["striped"]'))
        return
    chosen = []
    for name in style:
        if name not in TABLE_FEATURES:
            settings.problems.append(
                ('config.toml', f"[tables] style: {name!r} is not an option; "
                                f"the options are {names(TABLE_FEATURES)}"))
        elif name not in chosen:
            chosen.append(name)
    settings.default = tuple(chosen)


_CSS_COMMENT_RE = re.compile(r'/\*.*?\*/', re.DOTALL)
_CSS_RULE_RE = re.compile(r'([^{}]+)\{([^{}]*)\}')
_CSS_CUSTOM_RE = re.compile(r'(--[\w-]+)\s*:\s*([^;]+)')
_CLASS_SELECTOR_RE = re.compile(r'\.([A-Za-z_][\w-]*)')
_VAR_RE = re.compile(r'var\(\s*(--[\w-]+)\s*(?:,\s*([^()]*))?\)')


def _read_branding(settings, css_path, shown):
    """``--table-*`` values from branding.css: on ``:root`` for the course, on
    a single class for a named style. Nothing else in the file is read, so
    the table rules older branding files carry keep meaning what they meant.
    ``shown`` is the path as config.toml names it, for messages.
    """
    try:
        with open(css_path, 'r', encoding='utf-8') as f:
            css = f.read()
    except OSError:
        return
    css = _CSS_COMMENT_RE.sub('', css)

    found = []      # (selector, property, raw value), in file order
    for selectors, body in _CSS_RULE_RE.findall(css):
        selectors = [s.strip() for s in selectors.split(',')]
        for prop, raw in _CSS_CUSTOM_RE.findall(body):
            raw = raw.strip()
            if ':root' in selectors:
                settings.custom[prop] = raw
            if prop.startswith('--table-'):
                found.extend((s, prop, raw) for s in selectors)

    for selector, prop, raw in found:
        name = prop[len('--table-'):]
        if name not in TABLE_VALUES:
            settings.problems.append(
                (shown, f"{prop} is not a table setting; they are "
                        f"{', '.join(v.prop for v in TABLE_VALUES.values())}"))
            continue
        try:
            value = check_value(name, resolve_vars(raw, settings.custom))
        except ValueError as e:
            settings.problems.append((shown, f"{selector} {prop}: {e}"))
            continue
        m = _CLASS_SELECTOR_RE.fullmatch(selector)
        if selector == ':root':
            settings.root[name] = value
        elif m:
            settings.named.setdefault(m.group(1), {})[name] = value
        else:
            settings.problems.append(
                (shown, f"{selector} {{ {prop} }}: table settings are read from :root, "
                        f"for the course, or from a single class like .schema"))


def resolve_vars(raw, custom, depth=0):
    """Replace ``var(--x)`` with the :root value of ``--x``, or its fallback."""
    if depth > 10:
        return raw

    def replace(m):
        if m.group(1) in custom:
            return resolve_vars(custom[m.group(1)], custom, depth + 1)
        return m.group(2).strip() if m.group(2) is not None else m.group(0)
    return _VAR_RE.sub(replace, raw)


# ---------------------------------------------------------------------------
# The post-render pass
# ---------------------------------------------------------------------------

def inline_table_styles(html, settings=None):
    """Draw tables with inline styles, as the course's settings ask.

    Only the start tags of the tables cqs draws change, plus the wrapper divs
    around them, which are removed. Every other byte is returned as it came, so
    a course without table settings gets exactly the HTML it got before.
    """
    settings = settings or TableSettings()
    has_wrapper = WRAPPER_CLASS in html
    if not has_wrapper and ('<table' not in html or not (settings.on or settings.named)):
        return html
    try:
        scan = _Scanner(html)
    except Exception as e:     # styling must never cost a page its sync
        logger.debug("    Table styling skipped, HTML not understood: %s", e)
        return html

    edits = []
    for table in scan.tables:
        edits.extend(_style_table(table, settings))
    for wrapper in scan.wrappers:
        if wrapper.end:        # the closing tag was found, so both can go
            edits.append((wrapper.tag.start, wrapper.tag.end, ''))
            edits.append((wrapper.end[0], wrapper.end[1], ''))

    for start, end, text in sorted(edits, reverse=True):
        html = html[:start] + text + html[end:]
    return html


def _style_table(table, settings):
    """The edits that draw one table, or none if cqs leaves it to Canvas."""
    classes = _classes(table.tag.attrs)
    if PLAIN_CLASS in classes:
        return []
    named = [n for n in settings.named if n in classes]
    course = settings.on and QUARTO_TABLE_CLASS in classes
    if not (table.wrappers or named or course):
        return []

    own = [f for f in TABLE_FEATURES.values() if f.quarto_class in classes]
    features = own or [TABLE_FEATURES[n] for n in settings.default]

    explicit = dict(settings.root)
    for n in named:
        explicit.update(settings.named[n])
    for wrapper in table.wrappers:     # outermost first, so the innermost wins
        explicit.update(wrapper.values(settings))
    values = {n: v.default for n, v in TABLE_VALUES.items() if v.default}
    values.update(explicit)

    asked = {f.name for f in features}
    rules = [r for r in VALUE_RULES
             if all(u in explicit for u in r.uses) and not asked & set(r.unless)]
    for feature in features:
        rules.extend(r for r in feature.rules
                     if all(u in values for u in r.uses) and not asked & set(r.unless))

    drop = {f.quarto_class for f in features if not f.canvas_draws}
    add = [f.quarto_class for f in features if f.canvas_draws and f.quarto_class not in classes]
    new_classes = None
    if add or drop & set(classes):
        new_classes = [c for c in classes if c not in drop] + add

    edits = [_rewrite(table.tag, Element('table'), rules, values, new_classes)]
    edits += [_rewrite(tag, element, rules, values) for tag, element in table.parts]
    return [e for e in edits if e]


def _rewrite(tag, element, rules, values, classes=None):
    """The edit for one start tag, or None when it stays as it is."""
    decls = [_fill(r.css, values) for r in rules if TARGETS[r.target](element)]
    text = tag.text
    if classes is not None:
        text = _set_attr(text, 'class', ' '.join(classes) or None)
    if decls:
        style = _attrs(text)[0].get('style')
        text = _set_attr(text, 'style', _merge_style(style[2] if style else '', decls))
    return (tag.start, tag.end, text) if text != tag.text else None


def _fill(css, values):
    return re.sub(r'\{([\w-]+)\}', lambda m: values[m.group(1)], css)


def _merge_style(current, decls):
    """Append declarations, leaving what was there exactly as it was."""
    added = '; '.join(decls) + ';'
    current = current.strip()
    if not current:
        return added
    return current + ('' if current.endswith(';') else ';') + ' ' + added


def _classes(attrs):
    return (attrs.get('class') or '').split()


# --- Start tags, edited in place --------------------------------------------

_ATTR_RE = re.compile(r'''\s+([^\s"'>/=]+)(?:\s*=\s*("[^"]*"|'[^']*'|[^\s"'=<>`]+))?''')


def _attrs(text):
    """({name: (start, end, raw value)}, where the attributes end) for a start tag."""
    pos = re.match(r'<[^\s/>]+', text).end()
    found = {}
    while True:
        m = _ATTR_RE.match(text, pos)
        if not m:
            return found, pos
        raw = m.group(2) or ''
        if raw[:1] in ('"', "'"):
            raw = raw[1:-1]
        found.setdefault(m.group(1).lower(), (m.start(), m.end(), raw))
        pos = m.end()


def _set_attr(text, name, value):
    """``text`` with attribute ``name`` set to ``value``, or removed if None."""
    found, end = _attrs(text)
    new = f' {name}="{value}"' if value is not None else ''
    if name in found:
        start, stop, _ = found[name]
        return text[:start] + new + text[stop:]
    return text[:end] + new + text[end:]


# --- Finding the tables ------------------------------------------------------

@dataclass
class _Tag:
    start: int
    end: int
    text: str
    attrs: dict


@dataclass
class _Wrapper:
    tag: _Tag
    end: tuple = None        # (start, end) of the matching </div>
    _values: dict = None

    def values(self, settings):
        """The values this wrapper sets. Quarto writes ``stripe=`` as ``data-stripe``."""
        if self._values is None:
            self._values = {}
            for name in TABLE_VALUES:
                raw = self.tag.attrs.get('data-' + name, self.tag.attrs.get(name))
                if raw is None:
                    continue
                try:
                    self._values[name] = check_value(name, resolve_vars(raw, settings.custom))
                except ValueError as e:
                    logger.warning("    [yellow]Table styling:[/yellow] %s %s=: %s - ignored",
                                   WRAPPER_CLASS, name, e)
        return self._values


@dataclass
class _Table:
    tag: _Tag
    wrappers: list
    parts: list = field(default_factory=list)   # (tag, Element) for rows and cells


class _Open:
    """Where the scan is inside one open table."""

    def __init__(self, table):
        self.table = table
        self.section = None      # 'head', 'body' or 'foot' while inside one
        self.count = 0           # rows so far in the current section
        self.loose = 0           # rows so far outside any section
        self.row_section = 'body'
        self.row = 0
        self.col = -1

    def start_row(self):
        if self.section:
            self.row_section, self.row = self.section, self.count
            self.count += 1
        else:
            self.row_section, self.row = 'body', self.loose
            self.loose += 1
        self.col = -1
        return Element('row', self.row_section, self.row)

    def start_cell(self):
        self.col += 1
        return Element('cell', self.row_section, self.row, self.col)


_SECTIONS = {'thead': 'head', 'tbody': 'body', 'tfoot': 'foot'}


class _Scanner(HTMLParser):
    """Finds tables, their rows and cells, and the wrapper divs around them,
    with the exact source span of every tag that may change."""

    def __init__(self, html):
        super().__init__(convert_charrefs=False)
        self.html = html
        self._line_starts = [0] + [m.end() for m in re.finditer(r'\n', html)]
        self.tables = []
        self.wrappers = []
        self._divs = []          # one entry per open div: its _Wrapper, or None
        self._open = []          # open tables, innermost last
        self.feed(html)
        self.close()

    def _pos(self):
        line, col = self.getpos()
        return self._line_starts[line - 1] + col

    def _tag(self, attrs):
        start = self._pos()
        text = self.get_starttag_text()
        return _Tag(start, start + len(text), text, dict(attrs))

    def handle_starttag(self, tag, attrs):
        if tag == 'div':
            wrapper = None
            if WRAPPER_CLASS in _classes(dict(attrs)):
                wrapper = _Wrapper(self._tag(attrs))
                self.wrappers.append(wrapper)
            self._divs.append(wrapper)
        elif tag == 'table':
            table = _Table(self._tag(attrs), [w for w in self._divs if w])
            self.tables.append(table)
            self._open.append(_Open(table))
        elif self._open:
            current = self._open[-1]
            if tag in _SECTIONS:
                current.section, current.count = _SECTIONS[tag], 0
            elif tag == 'tr':
                current.table.parts.append((self._tag(attrs), current.start_row()))
            elif tag in ('td', 'th'):
                current.table.parts.append((self._tag(attrs), current.start_cell()))

    def handle_startendtag(self, tag, attrs):
        # <div/> and <table/> open nothing; a self-closed cell is still a cell.
        if tag in ('tr', 'td', 'th'):
            self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if tag == 'div' and self._divs:
            wrapper = self._divs.pop()
            if wrapper:
                start = self._pos()
                wrapper.end = (start, self.html.index('>', start) + 1)
        elif tag == 'table' and self._open:
            self._open.pop()
        elif tag in _SECTIONS and self._open:
            self._open[-1].section = None
