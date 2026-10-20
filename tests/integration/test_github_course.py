"""cqs github: one step from a course folder to a private team-course repo.

GitHub is replaced by a fake whose repositories are local bare git repos, so
the git side (init, commit, remote, push) is real.
"""

import os
import shutil
import subprocess

import pytest

import github_course as gc

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                          check=True).stdout.strip()


class FakeGitHub:
    token = "tok"

    def __init__(self, tmp, orgs=(("jth-crunch", "owner"), ("JUSimOpt", "owner")),
                 creates_public=False):
        self.tmp = tmp
        self.orgs = list(orgs)
        self.repos = {}            # (org, name) -> {"private": bool}
        self.creates_public = creates_public
        self.invited, self.collaborators = [], []
        self.base_permission = "write"
        self.members = {}          # login -> {"state": ..., "role": ...}

    def user(self):
        return {"login": "cenmir", "id": 1, "name": "Mirza Cenanovic"}

    def orgs_you_can_create_in(self):
        return self.orgs

    def _path(self, org, name):
        return os.path.join(self.tmp, "github", org, name + ".git")

    def clone_url(self, org, name):
        return self._path(org, name)

    def repo(self, org, name):
        return self.repos.get((org, name))

    def repo_is_empty(self, org, name):
        out = subprocess.run(["git", "for-each-ref"], cwd=self._path(org, name),
                             capture_output=True, text=True).stdout
        return out.strip() == ""

    def create_repo(self, org, name, description):
        os.makedirs(self._path(org, name))
        subprocess.run(["git", "init", "-q", "--bare", self._path(org, name)], check=True)
        self.repos[(org, name)] = {"private": not self.creates_public, "description": description}
        return self.repos[(org, name)]

    def make_private(self, org, name):
        self.repos[(org, name)]["private"] = True

    def org(self, org):
        return {"default_repository_permission": self.base_permission}

    def user_exists(self, login):
        return login != "nobody"

    def membership(self, org, login):
        return self.members.get(login)

    def invite_to_org(self, org, login):
        self.invited.append((org, login))
        return {"state": "pending"}

    def add_collaborator(self, org, name, login):
        self.collaborators.append((org, name, login))


@pytest.fixture
def course(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "appdata"))
    root = tmp_path / "T1H2UO-26"
    (root / "01_Mod").mkdir(parents=True)
    (root / "01_Mod" / "01_Page.qmd").write_text("---\ntitle: P\n---\n", encoding="utf-8")
    (root / "config.toml").write_text(
        'course_id = 19155\ncourse_name = "Konstruktion och Mekatronik 1"\n'
        'course_code = "T1H2UO"\n# workflow = "team"\n\n[branding]\ncss = "branding.css"\n',
        encoding="utf-8")
    (root / ".gitignore").write_text(".canvas_sync_map.json\n.canvas_snapshots/\n", encoding="utf-8")
    (root / ".canvas_sync_map.json").write_text('{"_course_id": 19155}\n', encoding="utf-8")
    (root / "studenter.csv").write_text("Namn\nNågon\n", encoding="utf-8")
    import handlers.config as config
    config._config_cache.clear()
    return root


class TestCreate:

    def test_one_step_to_a_private_team_course(self, course, tmp_path):
        fake = FakeGitHub(str(tmp_path))
        assert gc.create(str(course), org="jth-crunch", yes=True, gh=fake, interactive=False) == 0

        assert fake.repos[("jth-crunch", "T1H2UO-26")]["private"] is True
        remote = fake._path("jth-crunch", "T1H2UO-26")
        files = git(remote, "ls-tree", "-r", "--name-only", "main").splitlines()
        assert ".canvas_sync_map.json" in files, "the sync map must be committed"
        assert "studenter.csv" not in files, "student data was pushed"
        assert 'workflow = "team"' in (course / "config.toml").read_text(encoding="utf-8")
        assert ".canvas_sync_map.json" not in (course / ".gitignore").read_text(encoding="utf-8").split()
        assert git(course, "remote", "get-url", "origin") == remote
        assert gc.last_org() == "jth-crunch"

    def test_dry_run_changes_nothing(self, course, tmp_path):
        before = (course / ".gitignore").read_text(encoding="utf-8")
        fake = FakeGitHub(str(tmp_path))
        assert gc.create(str(course), org="jth-crunch", dry_run=True, gh=fake, interactive=False) == 0
        assert not (course / ".git").exists()
        assert fake.repos == {}
        assert (course / ".gitignore").read_text(encoding="utf-8") == before

    def test_organisation_is_never_guessed_without_a_terminal(self, course, tmp_path):
        with pytest.raises(gc.GithubError, match="(?s)--org.*jth-crunch.*JUSimOpt"):
            gc.create(str(course), yes=True, gh=FakeGitHub(str(tmp_path)), interactive=False)

    def test_already_connected_is_left_alone(self, course, tmp_path):
        git(course, "init", "-q", "-b", "main")
        git(course, "remote", "add", "origin", "https://github.com/x/y.git")
        with pytest.raises(gc.GithubError, match="already connected"):
            gc.create(str(course), org="jth-crunch", yes=True, gh=FakeGitHub(str(tmp_path)),
                      interactive=False)

    def test_existing_repo_with_content_is_refused(self, course, tmp_path):
        fake = FakeGitHub(str(tmp_path))
        fake.create_repo("jth-crunch", "T1H2UO-26", "")
        other = tmp_path / "other"
        subprocess.run(["git", "clone", "-q", fake._path("jth-crunch", "T1H2UO-26"), str(other)], check=True)
        git(other, "-c", "user.name=x", "-c", "user.email=x@x", "commit", "-q", "--allow-empty", "-m", "x")
        git(other, "push", "-q", "origin", "HEAD:main")
        with pytest.raises(gc.GithubError, match="already exists and has content"):
            gc.create(str(course), org="jth-crunch", yes=True, gh=fake, interactive=False)
        assert not (course / ".git").exists(), "changed the folder before refusing"

    def test_an_interrupted_run_resumes_into_the_empty_repo(self, course, tmp_path):
        fake = FakeGitHub(str(tmp_path))
        fake.create_repo("jth-crunch", "T1H2UO-26", "")     # the run stopped after this
        assert gc.create(str(course), org="jth-crunch", yes=True, gh=fake, interactive=False) == 0
        assert git(fake._path("jth-crunch", "T1H2UO-26"), "rev-parse", "main")

    def test_org_you_cannot_create_in_is_refused_before_any_change(self, course, tmp_path):
        with pytest.raises(gc.GithubError, match="may not create repositories in JTH-PPD"):
            gc.create(str(course), org="JTH-PPD", yes=True, gh=FakeGitHub(str(tmp_path)),
                      interactive=False)
        assert not (course / ".git").exists()

    def test_public_repo_is_made_private_and_reported(self, course, tmp_path):
        fake = FakeGitHub(str(tmp_path), creates_public=True)
        with pytest.raises(gc.GithubError, match="created public"):
            gc.create(str(course), org="jth-crunch", yes=True, gh=fake, interactive=False)
        assert fake.repos[("jth-crunch", "T1H2UO-26")]["private"] is True
        assert subprocess.run(["git", "remote", "get-url", "origin"], cwd=course,
                              capture_output=True).returncode != 0, "pushed to a public repo"

    def test_student_data_in_history_stops_it(self, course, tmp_path):
        git(course, "init", "-q", "-b", "main")
        git(course, "add", "-f", "studenter.csv")
        git(course, "-c", "user.name=x", "-c", "user.email=x@x", "commit", "-q", "-m", "oops")
        with pytest.raises(gc.GithubError, match="studenter.csv"):
            gc.create(str(course), org="jth-crunch", yes=True, gh=FakeGitHub(str(tmp_path)),
                      interactive=False)

    def test_bad_repository_name(self, course, tmp_path):
        with pytest.raises(gc.GithubError, match="cannot be a repository name"):
            gc.create(str(course), org="jth-crunch", name="T1H2UO 26", yes=True,
                      gh=FakeGitHub(str(tmp_path)), interactive=False)


class TestInvite:

    def _connected(self, course):
        git(course, "init", "-q", "-b", "main")
        git(course, "remote", "add", "origin", "https://github.com/jth-crunch/T1H2UO-26.git")

    def test_invites_to_the_courses_org(self, course, tmp_path):
        self._connected(course)
        fake = FakeGitHub(str(tmp_path))
        gc.invite(str(course), ["fredrik"], gh=fake)
        assert fake.invited == [("jth-crunch", "fredrik")]
        assert fake.collaborators == [], "org base permission is write, no per-repo access needed"

    def test_an_owner_is_never_demoted(self, course, tmp_path, capsys):
        # The membership endpoint also sets the role. Calling it for an owner
        # with role=member would demote them, so members are left alone.
        self._connected(course)
        fake = FakeGitHub(str(tmp_path))
        fake.members["cenmir"] = {"state": "active", "role": "admin"}
        gc.invite(str(course), ["cenmir"], gh=fake)
        assert fake.invited == []
        assert "already a member of jth-crunch (admin)" in capsys.readouterr().out

    def test_a_pending_invitation_is_not_sent_again(self, course, tmp_path, capsys):
        self._connected(course)
        fake = FakeGitHub(str(tmp_path))
        fake.members["fredrik"] = {"state": "pending", "role": "member"}
        gc.invite(str(course), ["fredrik"], gh=fake)
        assert fake.invited == []
        assert "waiting for them to accept" in capsys.readouterr().out

    def test_gives_repo_access_when_the_org_does_not(self, course, tmp_path):
        self._connected(course)
        fake = FakeGitHub(str(tmp_path))
        fake.base_permission = "read"
        gc.invite(str(course), ["fredrik"], gh=fake)
        assert fake.collaborators == [("jth-crunch", "T1H2UO-26", "fredrik")]

    def test_unknown_user(self, course, tmp_path):
        self._connected(course)
        with pytest.raises(gc.GithubError, match="no GitHub user 'nobody'"):
            gc.invite(str(course), ["nobody"], gh=FakeGitHub(str(tmp_path)))

    def test_course_not_on_github(self, course, tmp_path):
        with pytest.raises(gc.GithubError, match="not on GitHub yet"):
            gc.invite(str(course), ["fredrik"], gh=FakeGitHub(str(tmp_path)))


class TestFiles:

    def test_gitignore_keeps_other_lines_and_unignores_the_map(self):
        out = gc.fix_gitignore("*.log\n.canvas_sync_map.json\n/.canvas_sync_map.json\n")
        assert ".canvas_sync_map.json" not in out.split()
        assert "*.log" in out and "studenter.*" in out and "*CanvasToken*" in out

    def test_gitignore_is_idempotent(self):
        once = gc.fix_gitignore("")
        assert gc.fix_gitignore(once) == once

    def test_workflow_uncomments_existing_line(self):
        out = gc.set_team_workflow('a = 1\n# workflow = "team"   # default solo\n[branding]\n')
        assert out.splitlines()[1] == 'workflow = "team"'

    def test_workflow_goes_above_the_first_table(self):
        out = gc.set_team_workflow('a = 1\n\n[branding]\ncss = "b"\n')
        assert out.index('workflow = "team"') < out.index("[branding]")

    def test_org_from_remote_url(self):
        assert gc.org_and_repo_from_url("https://github.com/jth-crunch/T1H2UO-26.git") == ("jth-crunch", "T1H2UO-26")
        assert gc.org_and_repo_from_url("git@github.com:jth-crunch/T1H2UO-26.git") == ("jth-crunch", "T1H2UO-26")
        assert gc.org_and_repo_from_url("/some/local/path") is None


class TestForbidden:
    """A 403 is explained by what is actually missing."""

    class Resp:
        def __init__(self, scopes):
            self.headers = {} if scopes is None else {"X-OAuth-Scopes": scopes}

    def test_missing_scope_is_named_even_for_an_owner(self):
        msg = gc._forbidden(self.Resp("gist, read:org, repo"), "jth-crunch", "admin:org", "invite people")
        assert "lacks the 'admin:org' permission" in msg
        assert "gh auth refresh -h github.com -s admin:org" in msg

    def test_with_the_scope_it_is_the_role(self):
        msg = gc._forbidden(self.Resp("admin:org, repo"), "jth-crunch", "admin:org", "invite people")
        assert "owner of the organisation" in msg

    def test_fine_grained_token_without_scope_header(self):
        assert "owner" in gc._forbidden(self.Resp(None), "jth-crunch", "admin:org", "invite people")


class TestToken:

    def test_no_token_says_how_to_get_one(self, course, monkeypatch):
        monkeypatch.delenv("GH_TOKEN", raising=False)
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        monkeypatch.setattr(gc.shutil, "which", lambda name: "git" if name == "git" else None)
        with pytest.raises(gc.GithubError, match="gh auth login"):
            gc.create(str(course), org="jth-crunch", yes=True, interactive=False)

    def test_environment_token_wins(self, monkeypatch):
        monkeypatch.setenv("GH_TOKEN", "from-env")
        assert gc.find_token() == "from-env"
