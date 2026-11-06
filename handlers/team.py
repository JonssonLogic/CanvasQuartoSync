"""
Team mode: several people publish one course from one git repository.

Set ``workflow = "team"`` in config.toml. The principle is that GitHub holds
the truth and Canvas is where it is published, so a sync must publish exactly
what is on GitHub, and leave the shared sync map there for the next person.

Before a sync (``begin``):

1. The folder is a git repository, and the sync map is not gitignored. The map
   holds the Canvas ids; a clone without it matches by title and creates
   duplicates of anything renamed.
2. Commit whatever changed in the course folder, so Canvas shows what is in
   the repo. Nobody has to commit by hand before a sync.
3. Take the lock (below).
4. Pull, rebasing the local commits over GitHub. A conflict stops the sync:
   the rebase is undone, the commits stay local, and the message names the
   files to resolve. That is the only case that needs a person.
5. Push, so nothing published is missing from GitHub. This one is easy to
   miss: the map records a fingerprint of what was published. Publish an
   unpushed commit, and the next person's older copy of that file no longer
   matches the fingerprint, so their sync treats it as a new edit and
   publishes it, silently reverting yours.

After a sync (``end``, always, also after a crash): commit and push the map,
rebasing over anything pushed meanwhile, then release the lock. If the map
cannot be pushed, the lock is kept on purpose: without the map the next person
would create duplicates of whatever this sync created.

The lock is a ref on the remote, ``refs/cqs/sync-lock``, pointing at a commit
whose message says who holds it. Creating a ref that already exists is refused
by the server, so whoever pushes first holds it, with no extra service. Two
people syncing at once would otherwise both create any new page, once each.
"""

import json
import os
import socket
import subprocess
from datetime import datetime, timezone

from handlers.log import logger

LOCK_REF = 'refs/cqs/sync-lock'
STALE_AFTER_MINUTES = 30
MAP_NAME = '.canvas_sync_map.json'
# Every course's sync map (a folder synced to two courses has one each, see
# content_utils.get_sync_map_path). A rename touches all of them.
MAP_PATHSPEC = ':(glob).canvas_sync_map*.json'
# Every module's order file, at any depth (git pathspec).
ORDER_PATHSPEC = ':(glob)**/_order.yml'


class TeamModeError(Exception):
    """A team-mode check failed. The message says what to do."""


def _git(cwd, *args, check=True, input=None):
    proc = subprocess.run(['git', *args], cwd=cwd, capture_output=True, input=input,
                          text=True, encoding='utf-8', errors='replace')
    if check and proc.returncode != 0:
        raise TeamModeError(
            f"git {' '.join(args)} failed: {(proc.stderr or proc.stdout).strip()}")
    return proc


def _now():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


class TeamSync:
    def __init__(self, content_root, who='', break_lock=False):
        self.root = content_root
        self.who = who or ''
        self.break_lock = break_lock
        self.remote = 'origin'
        self.lock_sha = None
        from handlers.content_utils import get_sync_map_path
        # The map this sync writes: the active course's, set when it was verified.
        self.map_path = get_sync_map_path(content_root)
        self.map_name = os.path.basename(self.map_path)

    # -- before ------------------------------------------------------------

    def begin(self):
        if _git(self.root, 'rev-parse', '--is-inside-work-tree', check=False).returncode != 0:
            raise TeamModeError(
                'workflow = "team" needs a git repository. Run the sync from a clone '
                'of the course repo, or set workflow = "solo" in config.toml.')

        upstream = _git(self.root, 'rev-parse', '--abbrev-ref', '--symbolic-full-name',
                        '@{u}', check=False)
        if upstream.returncode != 0:
            raise TeamModeError(
                'This branch does not track a remote branch, so there is nothing to '
                'pull from or push to. Push it once with "git push -u origin main".')
        self.remote = upstream.stdout.strip().split('/', 1)[0] or 'origin'

        if _git(self.root, 'check-ignore', '-q', self.map_name, check=False).returncode == 0:
            raise TeamModeError(
                f'{self.map_name} is gitignored. In team mode it must be committed: it holds '
                'the Canvas ids everyone needs. Remove it from .gitignore and commit it.')

        self._commit_changes()

        self._acquire_lock()
        try:
            self._pull()
            ahead = _git(self.root, 'rev-list', '--count', '@{u}..HEAD').stdout.strip()
            if ahead and ahead != '0':
                push = _git(self.root, 'push', self.remote, 'HEAD', check=False)
                if push.returncode != 0:
                    raise TeamModeError(
                        'Could not push your changes to GitHub, so nothing was synced: '
                        'publishing something that is not on GitHub lets the next '
                        'person\'s sync revert it.\n    '
                        + (push.stderr or push.stdout).strip())
                logger.info('[green]Pushed %s commit(s) to GitHub.[/green]', ahead)
        except Exception:
            self._release_lock()
            raise

    def _commit_changes(self, verb='Changes synced'):
        """Commit everything that changed in the course folder. Returns the paths."""
        _git(self.root, 'add', '-A', '--', '.')
        # quotePath off: L2_Strömförsörjning.qmd, not "L2_Str\303\266m..."
        staged = _git(self.root, '-c', 'core.quotePath=false', 'diff', '--cached',
                      '--name-only', '--relative').stdout
        paths = [p for p in staged.splitlines() if p.strip()]
        if not paths:
            return []
        who = self.who or self._git_user() or 'cqs'
        names = ([p.split('/')[-1] for p in paths if not p.split('/')[-1].startswith('.canvas_sync_map')]
                 or [self.map_name])
        subject = f'{verb} by {who}: ' + ', '.join(names[:3])
        if len(names) > 3:
            subject += f' and {len(names) - 3} more'
        _git(self.root, 'commit', '-q', '-m', subject, '-m', '\n'.join(paths))
        logger.info('[green]Committed %d changed file(s).[/green]', len(paths))
        return paths

    def _pull(self):
        """Rebase local commits over GitHub. A conflict is undone and reported."""
        pull = _git(self.root, 'pull', '--rebase', '--autostash', check=False)
        if pull.returncode == 0:
            return
        conflicts = _git(self.root, '-c', 'core.quotePath=false', 'diff', '--name-only',
                         '--diff-filter=U', check=False).stdout.splitlines()
        _git(self.root, 'rebase', '--abort', check=False)
        if conflicts:
            raise TeamModeError(
                'Merge conflict: your changes and GitHub\'s change the same lines in:\n    '
                + '\n    '.join(conflicts)
                + '\nNothing was synced. Your changes are committed on this computer. '
                'Pull in VS Code (Source Control, Sync Changes), choose what to keep in '
                'each file, then sync again.')
        raise TeamModeError('Could not pull from GitHub:\n    '
                            + (pull.stderr or pull.stdout).strip())

    # -- lock --------------------------------------------------------------

    def _lock_info(self):
        return {
            'who': self.who or self._git_user(),
            'host': socket.gethostname(),
            'since': _now(),
        }

    def _git_user(self):
        return _git(self.root, 'config', 'user.name', check=False).stdout.strip()

    def _acquire_lock(self):
        tree = _git(self.root, 'mktree', input='').stdout.strip()   # the empty tree
        msg = json.dumps(self._lock_info(), ensure_ascii=False)
        sha = _git(self.root, 'commit-tree', tree, '-m', msg).stdout.strip()

        push = _git(self.root, 'push', '--porcelain', self.remote,
                    f'{sha}:{LOCK_REF}', check=False)
        if push.returncode == 0:
            self.lock_sha = sha
            return

        holder, holder_sha = self._read_lock()
        if holder_sha is None:
            raise TeamModeError(
                'Could not take the sync lock on GitHub:\n    '
                + (push.stderr or push.stdout).strip())

        desc = _describe(holder)
        if not self.break_lock:
            stale = _age_minutes(holder) >= STALE_AFTER_MINUTES
            hint = (f' The lock is over {STALE_AFTER_MINUTES} minutes old, so that sync '
                    'probably crashed. If nobody is syncing, run again with --break-lock.'
                    if stale else ' Try again in a moment.')
            raise TeamModeError(f'{desc} is syncing right now.{hint}')

        logger.warning('[yellow]Breaking the sync lock held by %s.[/yellow]', desc)
        forced = _git(self.root, 'push', '--porcelain',
                      f'--force-with-lease={LOCK_REF}:{holder_sha}',
                      self.remote, f'{sha}:{LOCK_REF}', check=False)
        if forced.returncode != 0:
            raise TeamModeError('Someone took the lock while it was being broken. Try again.')
        self.lock_sha = sha

    def _read_lock(self):
        """(info dict, sha) of the lock on the remote, or ({}, None) if there is none."""
        ls = _git(self.root, 'ls-remote', self.remote, LOCK_REF, check=False).stdout.strip()
        if not ls:
            return {}, None
        sha = ls.split()[0]
        _git(self.root, 'fetch', '--no-tags', self.remote,
             f'+{LOCK_REF}:refs/cqs/remote-sync-lock', check=False)
        body = _git(self.root, 'log', '-1', '--format=%B', sha, check=False).stdout.strip()
        try:
            return json.loads(body), sha
        except ValueError:
            return {}, sha

    def _release_lock(self):
        if not self.lock_sha:
            return True
        res = _git(self.root, 'push', '--porcelain',
                   f'--force-with-lease={LOCK_REF}:{self.lock_sha}',
                   self.remote, f':{LOCK_REF}', check=False)
        if res.returncode != 0:
            logger.warning('[yellow]Could not release the sync lock:[/yellow] %s',
                           (res.stderr or res.stdout).strip())
            return False
        self.lock_sha = None
        return True

    # -- after -------------------------------------------------------------

    def end(self):
        """Commit and push the map, then release the lock. Returns True on success."""
        try:
            pushed = self._push_map()
        except Exception as e:
            logger.error('[red]Could not push the sync map:[/red] %s', e)
            pushed = False

        if not pushed:
            logger.error(
                '[red]The sync lock is kept[/red] until the sync map is on GitHub. '
                'Without it, the next sync would duplicate what this one created. '
                'Commit and push %s, then run the sync again with --break-lock.', self.map_name)
            return False
        return self._release_lock()

    def _push_map(self):
        # The map, and the order files the sync brought in step with the
        # folders (a new, deleted or renamed file), which are committed with
        # it so they do not wait for the next sync.
        orders = _git(self.root, 'status', '--porcelain', '--', ORDER_PATHSPEC).stdout.strip()
        map_changed = _git(self.root, 'status', '--porcelain', '--', MAP_PATHSPEC).stdout.strip()
        if not map_changed and not orders:
            return True

        paths = ([MAP_PATHSPEC] if map_changed else []) + ([ORDER_PATHSPEC] if orders else [])
        _git(self.root, 'add', '--', *paths)
        who = self.who or self._git_user() or 'cqs'
        what = 'Sync map' if not orders else ('Order files' if not map_changed
                                             else 'Sync map and order files')
        _git(self.root, 'commit', '-m', f'{what} after sync by {who}', '--', *paths)
        if self._push():
            logger.info('[green]%s committed and pushed.[/green]', what)
            return True
        return False

    def _push(self):
        for _ in range(3):
            if _git(self.root, 'push', self.remote, 'HEAD', check=False).returncode == 0:
                return True
            # Someone pushed content meanwhile. Their commits do not touch the
            # map (only syncs do, and we hold the lock), so this rebase is clean.
            if _git(self.root, 'pull', '--rebase', check=False).returncode != 0:
                _git(self.root, 'rebase', '--abort', check=False)
                return False
        return False

    # -- a Course Board action (delete, import, publish, new module) -------

    def end_action(self, verb):
        """Commit and push everything the action changed, then release the lock.

        The action ran between begin() and here, so every change in the folder
        is its own: begin() committed what was there before. ``verb`` starts
        the commit subject ("Deleted", "Imported"). Returns True on success.
        """
        try:
            paths = self._commit_changes(verb)
            pushed = self._push() if paths else True
            if paths and pushed:
                logger.info('[green]Committed and pushed %d changed file(s).[/green]', len(paths))
        except Exception as e:
            logger.error('[red]Could not commit and push the change:[/red] %s', e)
            pushed = False
        if not pushed:
            logger.error(
                '[red]The sync lock is kept[/red] until the change is on GitHub. '
                'Commit and push it, then run the sync again with --break-lock.')
            return False
        return self._release_lock()


def _describe(info):
    who = info.get('who') or 'Someone'
    host = info.get('host')
    since = info.get('since')
    parts = [who]
    if host:
        parts.append(f'on {host}')
    if since:
        parts.append(f'since {since}')
    return ' '.join(parts)


def _age_minutes(info):
    try:
        since = datetime.strptime(info.get('since', ''), '%Y-%m-%dT%H:%M:%SZ')
        since = since.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - since).total_seconds() / 60
    except ValueError:
        return float('inf')     # unreadable lock: treat as stale
