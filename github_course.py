"""cqs github: put a course on GitHub as a team course, and invite teachers.

Doing this by hand is a dozen steps, and the ones that matter are the easiest
to get wrong: the repo ends up under someone's personal account, or public,
or the sync map stays in .gitignore so a colleague's clone has no Canvas ids.
This does all of it in one go, and checks everything that can stop it before
it changes anything.

GitHub is reached through its REST API with a token, so the GitHub CLI is
optional. The token comes from, in order: GH_TOKEN or GITHUB_TOKEN (the VS
Code extension passes its own GitHub sign-in this way), then `gh auth token`.

The organisation is never guessed. It is given with --org, or chosen from a
list that remembers the last choice. After the repo exists, everything else
(sync, lock, invite) reads it from the git remote, so it is chosen once per
course.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time

API = 'https://api.github.com'
MAP_NAME = '.canvas_sync_map.json'
NAME_RE = re.compile(r'^[A-Za-z0-9._-]+$')

EPILOG = """\
examples:
  cqs github --org jth-crunch
      Put the course you stand in on GitHub as jth-crunch/<folder name>,
      private, and switch it to team mode. Shows the plan and asks first.

  cqs github --org jth-crunch --name T1H2UO-26
      The same, with the repository name given.

  cqs github --org jth-crunch --dry-run
      Only show what would be done.

  cqs github invite fredrik-erling
      Invite a teacher (GitHub username) to the course's organisation, so
      they can clone the course. The organisation is read from the course's
      git remote.

  cqs github orgs
      List the organisations you can create repositories in.

Afterwards each teacher clones the repo (VS Code: Clone Repository), sets
CANVAS_API_TOKEN to their own Canvas token, and needs a teacher role in the
Canvas course.

The token: GH_TOKEN or GITHUB_TOKEN, else the GitHub CLI (`gh auth login`
once). In VS Code, the "Create GitHub Repository" button signs in for you.
"""

# Lines .gitignore must have in a team course. The sync map is the one thing
# that must NOT be ignored, so it is removed instead.
GITIGNORE_REQUIRED = [
    '.canvas_snapshots/',
    '.canvas_drift/',
    '.canvas_diff_temp/',
    'config.local.toml',
    'token.txt',
    '.env',
    '*CanvasToken*',
    'submissions/',
    'students.*',
    'studenter.*',
]
STUDENT_DATA = re.compile(r'(^|/)(students?\.[^/]*|studenter\.[^/]*|submissions/.*)$', re.IGNORECASE)


class GithubError(Exception):
    """A user-facing problem: print it, exit 2, no traceback."""


# ---------------------------------------------------------------------------
# Token and API
# ---------------------------------------------------------------------------

def find_token():
    """A GitHub token from the environment or the GitHub CLI, or None."""
    for var in ('GH_TOKEN', 'GITHUB_TOKEN'):
        if os.environ.get(var):
            return os.environ[var].strip()
    if shutil.which('gh'):
        res = subprocess.run(['gh', 'auth', 'token'], capture_output=True, text=True)
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip()
    return None


NO_TOKEN = (
    "not signed in to GitHub.\n"
    "  In VS Code: use the \"Create GitHub Repository\" button, it signs you in.\n"
    "  In a terminal: install the GitHub CLI (Windows: winget install GitHub.cli,\n"
    "  macOS: brew install gh) and run 'gh auth login' once.")


def _forbidden(response, org, scope, action):
    """Why GitHub said 403: a missing token scope, or a missing role.

    A classic or OAuth token lists its scopes in X-OAuth-Scopes. If the scope
    the call needs is not there, that is the reason, whatever the person's role.
    """
    scopes = response.headers.get('X-OAuth-Scopes')
    if scopes is not None:
        have = {x.strip() for x in scopes.split(',') if x.strip()}
        if scope not in have:
            return (f"your GitHub sign-in may not {action}: it lacks the '{scope}' permission.\n"
                    f"  In VS Code: run the command again and allow it when asked.\n"
                    f"  In a terminal: gh auth refresh -h github.com -s {scope}")
    return f"you may not {action} in {org}: that takes an owner of the organisation."


class GitHub:
    def __init__(self, token):
        import requests
        self.token = token
        self.s = requests.Session()
        self.s.headers.update({
            'Authorization': f'Bearer {token}',
            'Accept': 'application/vnd.github+json',
            'X-GitHub-Api-Version': '2022-11-28',
        })

    def _call(self, method, path, **kw):
        r = self.s.request(method, API + path, timeout=30, **kw)
        if r.status_code == 401:
            raise GithubError("GitHub rejected the token. Sign in again ('gh auth login', "
                              "or sign out and in to GitHub in VS Code).")
        return r

    def _ok(self, r, what):
        if r.status_code >= 400:
            try:
                msg = r.json().get('message', '')
                errors = r.json().get('errors') or []
                detail = '; '.join(e.get('message', '') for e in errors if isinstance(e, dict))
            except ValueError:
                msg, detail = r.text[:200], ''
            raise GithubError(f"{what} failed ({r.status_code}): {msg}"
                              + (f" ({detail})" if detail else ''))
        return r.json() if r.content else {}

    def user(self):
        return self._ok(self._call('GET', '/user'), 'Reading your GitHub account')

    def orgs_you_can_create_in(self):
        """[(org, role)] for active memberships where you may create repositories."""
        r = self._call('GET', '/user/memberships/orgs', params={'state': 'active', 'per_page': 100})
        if r.status_code == 403:
            raise GithubError("the token cannot list your organisations: it needs the "
                              "'read:org' scope ('gh auth refresh -s read:org').")
        out = []
        for m in self._ok(r, 'Listing your organisations'):
            org = m['organization']['login']
            if m.get('role') == 'admin':
                out.append((org, 'owner'))
                continue
            info = self._ok(self._call('GET', f'/orgs/{org}'), f'Reading {org}')
            if info.get('members_can_create_private_repositories',
                        info.get('members_can_create_repositories')):
                out.append((org, 'member'))
        return sorted(out, key=lambda t: t[0].lower())

    def repo(self, org, name):
        r = self._call('GET', f'/repos/{org}/{name}')
        if r.status_code == 404:
            return None
        return self._ok(r, f'Reading {org}/{name}')

    def repo_is_empty(self, org, name):
        r = self._call('GET', f'/repos/{org}/{name}/commits', params={'per_page': 1})
        return r.status_code == 409   # "Git Repository is empty."

    def create_repo(self, org, name, description):
        r = self._call('POST', f'/orgs/{org}/repos', json={
            'name': name, 'private': True, 'description': description,
            'has_wiki': False, 'has_projects': False,
        })
        if r.status_code == 403:
            raise GithubError(_forbidden(r, org, 'repo', 'create repositories'))
        return self._ok(r, f'Creating {org}/{name}')

    def make_private(self, org, name):
        return self._ok(self._call('PATCH', f'/repos/{org}/{name}', json={'private': True}),
                        f'Making {org}/{name} private')

    def org(self, org):
        return self._ok(self._call('GET', f'/orgs/{org}'), f'Reading {org}')

    def user_exists(self, login):
        return self._call('GET', f'/users/{login}').status_code == 200

    def membership(self, org, login):
        """{'state': 'active'|'pending', 'role': ...} if ``login`` is in or invited to ``org``, else None."""
        r = self._call('GET', f'/orgs/{org}/memberships/{login}')
        if r.status_code == 404:
            return None
        return self._ok(r, f'Reading the membership of {login} in {org}')

    def invite_to_org(self, org, login):
        # Only ever called for people who are not members yet: this endpoint
        # also *sets* the role, so calling it for an owner demotes them.
        r = self._call('PUT', f'/orgs/{org}/memberships/{login}', json={'role': 'member'})
        if r.status_code == 403:
            raise GithubError(_forbidden(r, org, 'admin:org', 'invite people'))
        return self._ok(r, f'Inviting {login} to {org}')

    def add_collaborator(self, org, name, login):
        r = self._call('PUT', f'/repos/{org}/{name}/collaborators/{login}',
                       json={'permission': 'push'})
        return self._ok(r, f'Giving {login} write access to {org}/{name}')

    @staticmethod
    def clone_url(org, name):
        return f'https://github.com/{org}/{name}.git'


# ---------------------------------------------------------------------------
# Git
# ---------------------------------------------------------------------------

def git(root, *args, check=True, env=None, retries=1):
    """Run git in the course folder.

    ``retries`` > 1 retries on "Permission denied": Dropbox briefly locks files
    in a new .git folder while it indexes them.
    """
    for attempt in range(retries):
        res = subprocess.run(['git', *args], cwd=root, capture_output=True, text=True,
                             encoding='utf-8', errors='replace', env=env)
        if res.returncode == 0 or 'Permission denied' not in (res.stderr or ''):
            break
        time.sleep(2)
    if check and res.returncode != 0:
        raise GithubError(f"git {' '.join(args)} failed: {(res.stderr or res.stdout).strip()}")
    return res


def is_repo(root):
    return git(root, 'rev-parse', '--is-inside-work-tree', check=False).returncode == 0


def origin_url(root):
    if not is_repo(root):
        return None
    res = git(root, 'remote', 'get-url', 'origin', check=False)
    return res.stdout.strip() if res.returncode == 0 else None


def org_and_repo_from_url(url):
    """('jth-crunch', 'T1H2UO-26') from a GitHub https or ssh URL, else None."""
    m = re.search(r'github\.com[/:]([^/]+)/([^/]+?)(?:\.git)?/?$', url or '')
    return (m.group(1), m.group(2)) if m else None


def student_files_in_history(root):
    if not is_repo(root):
        return []
    res = git(root, 'log', '--all', '--name-only', '--format=', check=False)
    names = {n.strip() for n in res.stdout.splitlines() if n.strip()}
    return sorted(n for n in names if STUDENT_DATA.search(n))


def push_with_token(root, branch, token):
    """git push using the token for this one command, without touching git config.

    The credential helper reads the token from an environment variable, so it
    never appears on a command line. Inherited helpers (e.g. Git Credential
    Manager) are switched off for the call, so nobody is asked to sign in again.
    """
    helper = '!f() { echo username=x-access-token; echo "password=$CQS_GITHUB_TOKEN"; }; f'
    env = {**os.environ, 'CQS_GITHUB_TOKEN': token, 'GIT_TERMINAL_PROMPT': '0'}
    return git(root, '-c', 'credential.helper=', '-c', 'credential.https://github.com.helper=',
               '-c', f'credential.helper={helper}', 'push', '-u', 'origin', branch,
               env=env, check=False)


# ---------------------------------------------------------------------------
# Course files
# ---------------------------------------------------------------------------

def fix_gitignore(text):
    """The .gitignore of a team course: map not ignored, local and student data ignored."""
    lines = text.splitlines()
    kept = [l for l in lines if l.strip() not in (MAP_NAME, '/' + MAP_NAME)]
    present = {l.strip() for l in kept}
    missing = [p for p in GITIGNORE_REQUIRED if p not in present]
    if missing:
        if kept and kept[-1].strip():
            kept.append('')
        kept.append('# Team course (cqs github): local state and student data never go in the repo.')
        kept.append('# The sync map is committed: it holds the Canvas ids everyone needs.')
        kept.extend(missing)
    return '\n'.join(kept) + '\n'


def set_team_workflow(text):
    """config.toml with workflow = "team" at the top level."""
    pat = re.compile(r'^[ \t]*#?[ \t]*workflow[ \t]*=.*$', re.MULTILINE)
    if pat.search(text):
        return pat.sub('workflow = "team"', text, count=1)
    table = re.search(r'^\[', text, re.MULTILINE)
    line = 'workflow = "team"\n\n'
    if table:
        return text[:table.start()] + line + text[table.start():]
    return text.rstrip('\n') + '\n\n' + line.rstrip('\n') + '\n'


GITATTRIBUTES = """\
# Line endings stored as LF for everyone; the .bat wrappers must stay CRLF.
* text=auto
*.bat text eol=crlf

*.pdf binary
*.png binary
*.jpg binary
*.gif binary
*.stl binary
*.step binary
"""


def _read(path):
    try:
        with open(path, encoding='utf-8') as f:
            return f.read()
    except FileNotFoundError:
        return ''


def _write(path, text):
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        f.write(text)


# ---------------------------------------------------------------------------
# Remembered organisation (per user, for the interactive list)
# ---------------------------------------------------------------------------

def _prefs_path():
    if os.name == 'nt':
        base = os.environ.get('APPDATA') or os.path.expanduser('~')
    else:
        base = os.environ.get('XDG_CONFIG_HOME') or os.path.join(os.path.expanduser('~'), '.config')
    return os.path.join(base, 'CanvasQuartoSync', 'github.json')


def last_org():
    try:
        with open(_prefs_path(), encoding='utf-8') as f:
            return json.load(f).get('last_org')
    except (OSError, ValueError):
        return None


def remember_org(org):
    try:
        os.makedirs(os.path.dirname(_prefs_path()), exist_ok=True)
        with open(_prefs_path(), 'w', encoding='utf-8') as f:
            json.dump({'last_org': org}, f)
    except OSError:
        pass


def choose_org(orgs, interactive):
    """Ask which organisation, last choice first. Non-interactive: refuse."""
    names = [o for o, _ in orgs]
    listing = '\n'.join(f'  {o}  ({role})' for o, role in orgs)
    if not interactive:
        raise GithubError("which organisation? Give it with --org. You can create "
                          f"repositories in:\n{listing}")
    last = last_org()
    if last in names:
        names.remove(last)
        names.insert(0, last)
    print('Which organisation should the course repository be in?')
    for i, o in enumerate(names, 1):
        print(f'  {i}. {o}' + ('   (last used)' if o == last else ''))
    answer = input(f'Number [1]: ').strip() or '1'
    if not answer.isdigit() or not 1 <= int(answer) <= len(names):
        raise GithubError('no organisation chosen.')
    return names[int(answer) - 1]


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------

def create(root, org=None, name=None, yes=False, dry_run=False, gh=None, interactive=None):
    """Put the course on GitHub. Every check runs before anything changes."""
    interactive = sys.stdin.isatty() if interactive is None else interactive
    if not shutil.which('git'):
        raise GithubError("git is not installed: https://git-scm.com/downloads")
    if not os.path.isfile(os.path.join(root, 'config.toml')):
        raise GithubError(f"{root} has no config.toml, so it is not a course folder.")

    existing = origin_url(root)
    if existing:
        raise GithubError(f"this course is already connected to {existing}. Nothing to do.")

    if gh is None:
        token = find_token()
        if not token:
            raise GithubError(NO_TOKEN)
        gh = GitHub(token)
    me = gh.user()
    login = me['login']

    orgs = gh.orgs_you_can_create_in()
    if org is None:
        if not orgs:
            raise GithubError("you are not in any GitHub organisation where you may create "
                              "repositories. Ask an owner to invite you (cqs github invite).")
        org = choose_org(orgs, interactive)

    name = name or os.path.basename(os.path.abspath(root))
    if not NAME_RE.match(name):
        raise GithubError(f"'{name}' cannot be a repository name. Use letters, digits, "
                          "'.', '-' and '_', for example: --name T1H2UO-26")

    resume = False
    found = gh.repo(org, name)
    if found is not None:
        if not gh.repo_is_empty(org, name):
            raise GithubError(f"{org}/{name} already exists and has content. If it is this "
                              "course, clone it instead. Otherwise choose another --name.")
        resume = True       # created by an earlier run that stopped before the push
    elif org not in [o for o, _ in orgs]:
        raise GithubError(f"you may not create repositories in {org}. You can in: "
                          + (', '.join(o for o, _ in orgs) or 'none'))

    leaked = student_files_in_history(root)
    if leaked:
        raise GithubError("student data is already in this folder's git history, and it "
                          "would be pushed with it:\n  " + '\n  '.join(leaked)
                          + "\nRemove it from the history first.")

    from handlers.config import load_config
    cfg = load_config(root)
    description = ' '.join(str(x) for x in (cfg.get('course_name'), cfg.get('course_code'),
                                            cfg.get('semester')) if x)

    print(f"Signed in to GitHub as {login}.")
    print(f"This will:")
    steps = []
    if not is_repo(root):
        steps.append("make the course folder a git repository")
    steps.append("update .gitignore: commit the sync map, never student data or tokens")
    steps.append('set workflow = "team" in config.toml (team mode)')
    steps.append("commit everything in the course folder")
    steps.append(f"{'use the empty' if resume else 'create the'} PRIVATE repository "
                 f"{org}/{name}" + (" (left by an earlier run)" if resume else ""))
    steps.append("push the course to it")
    for i, s in enumerate(steps, 1):
        print(f"  {i}. {s}")

    if dry_run:
        print("Dry run: nothing changed.")
        return 0
    if not yes:
        if not interactive:
            raise GithubError("add --yes to go ahead without a question.")
        if input("Go ahead? [y/N]: ").strip().lower() not in ('y', 'yes', 'j', 'ja'):
            print("Nothing changed.")
            return 1

    # -- local changes ------------------------------------------------------
    if not is_repo(root):
        git(root, 'init', '-b', 'main', retries=5)
    gi = os.path.join(root, '.gitignore')
    _write(gi, fix_gitignore(_read(gi)))
    ga = os.path.join(root, '.gitattributes')
    if not os.path.exists(ga):
        _write(ga, GITATTRIBUTES)
    cf = os.path.join(root, 'config.toml')
    _write(cf, set_team_workflow(_read(cf)))

    if not git(root, 'config', 'user.name', check=False).stdout.strip():
        git(root, 'config', 'user.name', me.get('name') or login, retries=5)
    if not git(root, 'config', 'user.email', check=False).stdout.strip():
        git(root, 'config', 'user.email', f"{me['id']}+{login}@users.noreply.github.com", retries=5)

    git(root, 'add', '-A')
    has_head = git(root, 'rev-parse', '--verify', 'HEAD', check=False).returncode == 0
    staged = git(root, 'diff', '--cached', '--quiet', check=False).returncode != 0
    if staged or not has_head:
        git(root, 'commit', '-q', '-m', f'Course on GitHub as {org}/{name} (team mode)')
    branch = git(root, 'rev-parse', '--abbrev-ref', 'HEAD').stdout.strip()

    # -- GitHub -------------------------------------------------------------
    if not resume:
        gh.create_repo(org, name, description)
    info = gh.repo(org, name) or {}
    if not info.get('private'):
        gh.make_private(org, name)
        raise GithubError(f"{org}/{name} was created public. It has been made private, "
                          "but check its settings on github.com before going on.")

    git(root, 'remote', 'add', 'origin', gh.clone_url(org, name), retries=5)
    res = push_with_token(root, branch, gh.token)
    if res.returncode != 0:
        raise GithubError(f"the repository exists but the push failed:\n{res.stderr.strip()}\n"
                          "Run 'cqs github' again to retry, or 'git push -u origin "
                          f"{branch}' yourself.")

    remember_org(org)
    print(f"\nDone: https://github.com/{org}/{name} (private, team mode).")
    print("Next:")
    print(f"  - Invite the other teachers: cqs github invite <github-username>")
    print("  - Each teacher: VS Code > Clone Repository > this repository; set")
    print("    CANVAS_API_TOKEN to their own Canvas token; a teacher role in Canvas.")
    return 0


# ---------------------------------------------------------------------------
# invite / orgs
# ---------------------------------------------------------------------------

def invite(root, logins, gh=None):
    target = org_and_repo_from_url(origin_url(root))
    if not target:
        raise GithubError("this course is not on GitHub yet. Run 'cqs github --org <org>' first.")
    org, name = target
    if gh is None:
        token = find_token()
        if not token:
            raise GithubError(NO_TOKEN)
        gh = GitHub(token)
    base = gh.org(org).get('default_repository_permission')
    for login in logins:
        if not gh.user_exists(login):
            raise GithubError(f"there is no GitHub user '{login}'.")
        current = gh.membership(org, login)
        if current is None:
            gh.invite_to_org(org, login)
            print(f"{login}: invitation sent by e-mail ({org}).")
        elif current.get('state') == 'pending':
            print(f"{login}: already invited to {org}, waiting for them to accept.")
        else:
            print(f"{login}: already a member of {org} ({current.get('role', 'member')}).")
        # Members get write access to every repo when the org's base permission
        # is write. Otherwise give them this repository explicitly.
        if base not in ('write', 'admin'):
            gh.add_collaborator(org, name, login)
            print(f"{login}: write access to {org}/{name}.")
    return 0


def list_orgs(as_json=False, gh=None):
    if gh is None:
        token = find_token()
        if not token:
            raise GithubError(NO_TOKEN)
        gh = GitHub(token)
    orgs = gh.orgs_you_can_create_in()
    if as_json:
        print(json.dumps({'login': gh.user()['login'], 'last_org': last_org(),
                          'orgs': [{'login': o, 'role': r} for o, r in orgs]}))
    else:
        for o, r in orgs:
            print(f"{o}  ({r})")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    root, rest = (argv[0], argv[1:]) if argv and not argv[0].startswith('-') else ('.', argv)
    root = os.path.abspath(root)

    if rest and rest[0] == 'invite':
        p = argparse.ArgumentParser(prog='cqs github invite',
                                    description='Invite teachers to the course\'s GitHub organisation.')
        p.add_argument('users', nargs='+', metavar='USERNAME', help='GitHub username(s).')
        a = p.parse_args(rest[1:])
        fn = lambda: invite(root, a.users)
    elif rest and rest[0] == 'orgs':
        p = argparse.ArgumentParser(prog='cqs github orgs',
                                    description='List the organisations you can create repositories in.')
        p.add_argument('--json', action='store_true')
        a = p.parse_args(rest[1:])
        fn = lambda: list_orgs(a.json)
    else:
        p = argparse.ArgumentParser(
            prog='cqs github', epilog=EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter,
            description='Put this course on GitHub as a private team course, in one step.')
        p.add_argument('--org', help='GitHub organisation, e.g. jth-crunch. Without it you choose from a list.')
        p.add_argument('--name', help='Repository name. Default: the course folder\'s name.')
        p.add_argument('--yes', '-y', action='store_true', help='Do not ask before going ahead.')
        p.add_argument('--dry-run', action='store_true', help='Only show what would be done.')
        a = p.parse_args(rest)
        fn = lambda: create(root, a.org, a.name, a.yes, a.dry_run)

    try:
        return fn()
    except GithubError as e:
        print(f"cqs github: {e}", file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
