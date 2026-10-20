"""
Team mode: several people publish one course from one git repository.

Set ``workflow = "team"`` in config.toml. The principle is that GitHub holds
the truth and Canvas is where it is published, so a sync must publish exactly
what is on GitHub, and leave the shared sync map there for the next person.

Before a sync (``begin``):

1. The folder is a git repository, and the sync map is not gitignored. The map
   holds the Canvas ids; a clone without it matches by title and creates
   duplicates of anything renamed.
2. No uncommitted changes, so Canvas shows what is in the repo.
3. Take the lock (below).
4. Pull, fast-forward only.
5. Nothing unpushed. This one is easy to miss: the map records a fingerprint
   of what was published. Publish an unpushed commit, and the next person's
   older copy of that file no longer matches the fingerprint, so their sync
   treats it as a new edit and publishes it, silently reverting yours.

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
        self.map_path = os.path.join(content_root, MAP_NAME)

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

        if _git(self.root, 'check-ignore', '-q', MAP_NAME, check=False).returncode == 0:
            raise TeamModeError(
                f'{MAP_NAME} is gitignored. In team mode it must be committed: it holds '
                'the Canvas ids everyone needs. Remove it from .gitignore and commit it.')

        dirty = self._dirty_paths()
        if dirty:
            raise TeamModeError(
                'Uncommitted changes. Canvas should show what is in the repo, so commit '
                '(or discard) them first:\n    ' + '\n    '.join(dirty[:10]))

        self._acquire_lock()
        try:
            pull = _git(self.root, 'pull', '--ff-only', check=False)
            if pull.returncode != 0:
                raise TeamModeError(
                    'Could not pull without merging: your branch and GitHub have both '
                    'moved on. Pull (and resolve) in VS Code first, then sync.\n    '
                    + (pull.stderr or pull.stdout).strip())
            ahead = _git(self.root, 'rev-list', '--count', '@{u}..HEAD').stdout.strip()
            if ahead and ahead != '0':
                raise TeamModeError(
                    f'{ahead} commit(s) not pushed. Push first: publishing something '
                    'that is not on GitHub lets the next person\'s sync revert it.')
        except Exception:
            self._release_lock()
            raise

    def _dirty_paths(self):
        out = _git(self.root, 'status', '--porcelain', '--', '.').stdout
        paths = [line[3:] for line in out.splitlines() if line.strip()]
        # The map itself may be left over from a sync whose push failed; end()
        # commits it, so it does not block.
        return [p for p in paths if p.strip('"').replace('\\', '/').split('/')[-1] != MAP_NAME]

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
                'Commit and push %s, then run the sync again with --break-lock.', MAP_NAME)
            return False
        return self._release_lock()

    def _push_map(self):
        if not os.path.exists(self.map_path):
            return True
        changed = _git(self.root, 'status', '--porcelain', '--', MAP_NAME).stdout.strip()
        if not changed:
            return True

        _git(self.root, 'add', '--', MAP_NAME)
        who = self.who or self._git_user() or 'cqs'
        _git(self.root, 'commit', '-m', f'Sync map after sync by {who}', '--', MAP_NAME)

        for _ in range(3):
            if _git(self.root, 'push', self.remote, 'HEAD', check=False).returncode == 0:
                logger.info('[green]Sync map committed and pushed.[/green]')
                return True
            # Someone pushed content meanwhile. Their commits do not touch the
            # map (only syncs do, and we hold the lock), so this rebase is clean.
            if _git(self.root, 'pull', '--rebase', check=False).returncode != 0:
                _git(self.root, 'rebase', '--abort', check=False)
                return False
        return False


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
