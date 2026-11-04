import os

__version__ = "0.16.0"

# Written into the install by dev-deploy.ps1: the commit, branch and whether
# the working tree had uncommitted changes. install.ps1 and update.ps1 delete
# it, so a release install reports the bare version.
BUILD_FILE = 'BUILD'

_TOOL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def build_stamp(tool_dir=None):
    """``'b6eb056 (feat/order-reconcile)'`` for a dev build, else ``''``."""
    path = os.path.join(tool_dir or _TOOL_DIR, BUILD_FILE)
    try:
        with open(path, encoding='utf-8') as f:
            fields = dict(line.split('=', 1) for line in f.read().splitlines() if '=' in line)
    except (OSError, ValueError):
        return ''
    commit = fields.get('commit', '').strip()
    if not commit:
        return ''
    notes = [n for n in (fields.get('branch', '').strip(),
                         'modified' if fields.get('modified', '').strip() == 'true' else '') if n]
    return commit + (f" ({', '.join(notes)})" if notes else '')


def full_version(tool_dir=None):
    """The version as people should see it: ``0.16.0+b6eb056 (feat/x)`` for a dev build."""
    stamp = build_stamp(tool_dir)
    return f"{__version__}+{stamp}" if stamp else __version__
