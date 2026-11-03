"""Quarto renders in a mirror of the course outside the content folder.

Rendering next to the source left tmp-* files in the course folder while
Quarto ran. Dropbox, OneDrive and VS Code pick them up and hold them open, and
Quarto's own cleanup then fails on the lock (os error 32), which made a
finished PDF count as a failed render.

So a render runs in a mirror of the course under the system temp folder. The
mirror has the same tree, so relative image paths, _quarto.yml, branding and
filters resolve exactly as before. Files are hard links: instant, no extra
space, and Quarto only reads them. Where a link is not possible (another
drive), the file is copied.

One mirror serves a whole sync run::

    with render_workspace(content_root):
        ...every handler.sync(...)...

Inside the run it is made on the first render and removed at the end. A
render outside any run gets a mirror of its own, removed when it finishes.
If the mirror cannot be made, the render happens in place, as before.
"""

import contextlib
import os
import shutil
import tempfile

from handlers.log import logger

# Never mirrored. Dot folders cover .git, .quarto, .venv and the sync's own
# .canvas_* folders; the rest are build output Quarto makes fresh.
_SKIP_DIRS = {'_site', '_book', 'node_modules', 'venv', '__pycache__'}
# Leftovers of an earlier in-place render: the render writes new ones.
_SKIP_FILE_PREFIXES = ('tmp-', '_temp_quiz_render')
# Quarto rewrites files in here, and a hard link would carry that write back
# into the course. These are copied instead.
_COPY_DIRS = {'_freeze'}

# Mirrors of the sync runs in progress: normalised course root -> state.
_runs = {}


def _key(root):
    return os.path.normcase(os.path.abspath(root))


def _skip_dir(name):
    return name.startswith('.') or name in _SKIP_DIRS


def mirror_course(content_root):
    """Mirror ``content_root`` into a new temp folder and return its path.

    Raises OSError if the folder cannot be made. A single file that cannot be
    linked or copied is skipped with a warning, so one locked file does not
    stop the render.
    """
    dest_root = tempfile.mkdtemp(prefix='cqs-render-')
    use_links = True
    try:
        for dirpath, dirnames, filenames in os.walk(content_root):
            dirnames[:] = [d for d in dirnames if not _skip_dir(d)]
            rel = os.path.relpath(dirpath, content_root)
            target_dir = dest_root if rel == '.' else os.path.join(dest_root, rel)
            os.makedirs(target_dir, exist_ok=True)
            copy_here = any(part in _COPY_DIRS for part in rel.split(os.sep))
            for name in filenames:
                if name.startswith(_SKIP_FILE_PREFIXES):
                    continue
                src = os.path.join(dirpath, name)
                dst = os.path.join(target_dir, name)
                if use_links and not copy_here:
                    try:
                        os.link(src, dst)
                        continue
                    except OSError as e:
                        # Another drive, or a file system without links: the
                        # rest will fail the same way, so stop trying.
                        logger.debug("    Hard link failed (%s), copying instead", e)
                        use_links = False
                try:
                    shutil.copy2(src, dst)
                except OSError as e:
                    logger.warning("    [yellow]Not mirrored for rendering:[/yellow] %s (%s)",
                                   os.path.join(rel, name), e)
    except Exception:
        shutil.rmtree(dest_root, ignore_errors=True)
        raise
    logger.debug("    Render mirror: %s", dest_root)
    return dest_root


def _remove(mirror):
    if mirror:
        shutil.rmtree(mirror, ignore_errors=True)


def _try_mirror(content_root):
    try:
        return mirror_course(content_root)
    except Exception as e:
        logger.warning("    [yellow]Could not set up a render folder outside the course "
                       "(%s). Rendering in place.[/yellow]", e)
        return None


@contextlib.contextmanager
def render_workspace(content_root):
    """One mirror for every render of a sync run, removed when the run ends.

    The mirror is made on the first render, so a run that renders nothing
    costs nothing. Nested runs on the same course share the outer one.
    """
    if not content_root:
        yield
        return
    key = _key(content_root)
    if key in _runs:
        yield
        return
    _runs[key] = {'root': os.path.abspath(content_root), 'mirror': None, 'tried': False}
    try:
        yield
    finally:
        _remove(_runs.pop(key)['mirror'])


def _active_mirror(content_root):
    """(root, mirror) of the run on this course, making the mirror if needed."""
    state = _runs.get(_key(content_root))
    if state is None:
        return None
    if not state['tried']:
        state['tried'] = True
        state['mirror'] = _try_mirror(state['root'])
    return state['root'], state['mirror']


def _mirrored_dir(root, mirror, base_path):
    rel = os.path.relpath(os.path.abspath(base_path), root)
    if rel.startswith('..') or os.path.isabs(rel):
        return None
    work = mirror if rel == '.' else os.path.join(mirror, rel)
    os.makedirs(work, exist_ok=True)
    return work


@contextlib.contextmanager
def render_dir(base_path, content_root):
    """The folder a render of a file in ``base_path`` should run in.

    Inside a run: the matching folder of the run's mirror. Outside one: a
    mirror made for this render and removed afterwards. ``base_path`` itself
    when there is no course root, the folder is outside it, or no mirror could
    be made.
    """
    if not content_root:
        yield base_path
        return
    run = _active_mirror(content_root)
    if run is not None:
        root, mirror = run
        work = _mirrored_dir(root, mirror, base_path) if mirror else None
        yield work or base_path
        return
    root = os.path.abspath(content_root)
    mirror = _try_mirror(root)
    try:
        work = _mirrored_dir(root, mirror, base_path) if mirror else None
        yield work or base_path
    finally:
        _remove(mirror)


def real_path(path):
    """A path inside a run's mirror, as the same path in the course."""
    if not path:
        return path
    full = os.path.normcase(os.path.abspath(path))
    for state in _runs.values():
        mirror = state['mirror']
        if not mirror:
            continue
        m = os.path.normcase(os.path.abspath(mirror))
        if full == m or full.startswith(m + os.sep):
            return os.path.join(state['root'], os.path.relpath(os.path.abspath(path), mirror))
    return path


def real_text(text, work_dir=None, base_path=None):
    """``text`` (Quarto's stderr, say) with mirror paths shown as course paths.

    ``work_dir``/``base_path`` map a per-render mirror folder back to the
    source folder; paths in an active run's mirror are mapped as well.
    """
    if not text:
        return text
    pairs = []
    if work_dir and base_path and os.path.abspath(work_dir) != os.path.abspath(base_path):
        pairs.append((os.path.abspath(work_dir), os.path.abspath(base_path)))
    for state in _runs.values():
        if state['mirror']:
            pairs.append((os.path.abspath(state['mirror']), state['root']))
    for src, dst in pairs:
        for a, b in ((src, dst), (src.replace('\\', '/'), dst.replace('\\', '/'))):
            text = text.replace(a, b)
    return text
