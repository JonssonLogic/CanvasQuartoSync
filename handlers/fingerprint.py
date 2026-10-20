"""
Source fingerprints: has this file changed since it was last published?

The sync used to answer that with the file's mtime. An mtime is a fact about
one filesystem, so it cannot be shared: a colleague's clone has different
mtimes for the same content, every comparison fails, and two authors syncing
in turn re-render the whole course every time, each overwriting the other's
mtimes in the shared map. A fresh checkout or a pull also stamps new mtimes on
files whose content did not change.

A fingerprint of the content is the same on every machine. It covers what
actually determines the published result:

- the file itself, with line endings normalised so a Windows checkout (CRLF)
  and a macOS one (LF) agree;
- the local files it references (images, included files, a description file),
  so replacing an image re-publishes the page that shows it;
- the course-wide render inputs (``_quarto.yml``, ``branding.css``,
  ``config.toml``), which mtime never covered at all (#30).

Links to other content files (``.qmd``, ``.md``, ``.json``) are deliberately
not followed: they become Canvas URLs, so editing the target does not change
the page that links to it.
"""

import hashlib
import os
import re

# Course-wide files whose content changes every rendered page.
GLOBAL_RENDER_INPUTS = ("_quarto.yml", "branding.css", "config.toml")

# Cross-links resolve to Canvas URLs, not content, so they are not dependencies.
_CROSS_LINK_EXTS = {".qmd", ".md", ".json"}

_REF_PATTERNS = (
    re.compile(r'\]\(\s*<?([^)\s>]+)'),                    # markdown link/image
    re.compile(r'''(?:src|href)\s*=\s*["']([^"']+)["']'''),  # raw HTML
    re.compile(r'\{\{<\s*include\s+([^\s>]+)\s*>\}\}'),     # quarto include
    re.compile(r'''description_file\s*:\s*["']?([^"'\s]+)'''),  # quiz frontmatter
    re.compile(r'''"description_file"\s*:\s*"([^"]+)"'''),     # quiz JSON
)


def _normalized_bytes(path):
    with open(path, "rb") as f:
        data = f.read()
    # CRLF -> LF for everything. Harmless for binary files: the result is only
    # ever hashed, and it is the same on every machine.
    return data.replace(b"\r\n", b"\n")


def file_fingerprint(path):
    """Fingerprint of one file's content, line endings normalised."""
    return hashlib.sha256(_normalized_bytes(path)).hexdigest()[:16]


def referenced_local_files(text, base_dir, include_cross_links=False):
    """Local files that ``text`` references, resolved against ``base_dir``.

    Only paths that exist as files are returned, sorted and de-duplicated, so
    the result is stable across machines. URLs, anchors and absolute paths are
    ignored.
    """
    found = set()
    for pattern in _REF_PATTERNS:
        for match in pattern.finditer(text):
            target = match.group(1).strip()
            if not target or target.startswith(("#", "/", "\\")):
                continue
            if re.match(r'^[a-zA-Z][a-zA-Z0-9+.-]*:', target):   # http:, mailto:, C:
                continue
            target = re.split(r'[#?]', target, maxsplit=1)[0]
            if not target:
                continue
            ext = os.path.splitext(target)[1].lower()
            if ext in _CROSS_LINK_EXTS and not include_cross_links:
                continue
            path = os.path.normpath(os.path.join(base_dir, target))
            if os.path.isfile(path):
                found.add(path)
    return sorted(found)


def source_fingerprint(file_path, content_root=None, extra_paths=()):
    """Fingerprint of everything that determines what ``file_path`` publishes.

    ``extra_paths`` are dependencies the caller knows about that the reference
    scan cannot see. Missing extras are skipped, but their absence is part of
    the fingerprint, so creating one later counts as a change.
    """
    h = hashlib.sha256()
    h.update(_normalized_bytes(file_path))

    try:
        text = _normalized_bytes(file_path).decode("utf-8", errors="replace")
    except OSError:
        text = ""
    base_dir = os.path.dirname(os.path.abspath(file_path))
    deps = set(referenced_local_files(text, base_dir))
    # Includes are content, even when they are .qmd files.
    for m in _REF_PATTERNS[2].finditer(text):
        p = os.path.normpath(os.path.join(base_dir, m.group(1)))
        if os.path.isfile(p):
            deps.add(p)
    deps.discard(os.path.normpath(os.path.abspath(file_path)))

    named = []
    for p in sorted(deps):
        named.append((_label(p, content_root or base_dir), p))
    for p in extra_paths:
        named.append(("extra:" + _label(p, content_root or base_dir), p))
    if content_root:
        for name in GLOBAL_RENDER_INPUTS:
            named.append(("global:" + name, os.path.join(content_root, name)))

    for label, path in named:
        h.update(b"\0" + label.encode("utf-8") + b"\0")
        if os.path.isfile(path):
            h.update(_normalized_bytes(path))
        else:
            h.update(b"<missing>")
    return h.hexdigest()[:16]


def _label(path, root):
    """A machine-independent name for a dependency: its path relative to root."""
    try:
        rel = os.path.relpath(path, root)
    except ValueError:      # different drive on Windows
        rel = os.path.basename(path)
    return rel.replace("\\", "/")
