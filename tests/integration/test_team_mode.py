"""Team mode against real git: a bare repo plays GitHub, two clones play two teachers.

Covers the guards before a sync, the lock, and pushing the map afterwards.
No Canvas is involved; TeamSync only ever talks to git.
"""

import json
import os
import shutil
import subprocess

import pytest

from handlers.team import LOCK_REF, MAP_NAME, TeamModeError, TeamSync

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                          check=True).stdout.strip()


def _clone(remote, path, name):
    # Line-ending conversion off from the start, whatever the machine's global
    # setting: switching it after checkout makes every file look modified.
    subprocess.run(["git", "-c", "core.autocrlf=false", "clone", "-q", str(remote), str(path)],
                   check=True, capture_output=True)
    git(path, "config", "core.autocrlf", "false")
    git(path, "config", "user.name", name)
    git(path, "config", "user.email", f"{name.lower()}@example.com")
    return path


@pytest.fixture
def repos(tmp_path):
    remote = tmp_path / "github.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)

    seed = tmp_path / "seed"
    subprocess.run(["git", "init", "-q", "-b", "main", str(seed)], check=True)
    git(seed, "config", "user.name", "Seed")
    git(seed, "config", "user.email", "seed@example.com")
    git(seed, "config", "core.autocrlf", "false")
    (seed / "01_Mod").mkdir()
    (seed / "01_Mod" / "01_Page.qmd").write_text("---\ntitle: P\n---\n", encoding="utf-8")
    (seed / "config.toml").write_text('course_id = 1\nworkflow = "team"\n', encoding="utf-8")
    (seed / MAP_NAME).write_text('{"_course_id": 1}\n', encoding="utf-8")
    git(seed, "add", "-A")
    git(seed, "commit", "-q", "-m", "seed")
    git(seed, "remote", "add", "origin", str(remote))
    git(seed, "push", "-q", "-u", "origin", "main")

    a = _clone(remote, tmp_path / "mirza", "Mirza")
    b = _clone(remote, tmp_path / "kalle", "Kalle")
    return remote, a, b


def _lock_on_remote(remote):
    return subprocess.run(["git", "ls-remote", str(remote), LOCK_REF], capture_output=True,
                          text=True).stdout.strip()


class TestGuards:

    def test_not_a_repo(self, tmp_path):
        (tmp_path / "plain").mkdir()
        with pytest.raises(TeamModeError, match="git repository"):
            TeamSync(str(tmp_path / "plain")).begin()

    def test_gitignored_map_is_refused(self, repos):
        # The solo default: map untracked and ignored. (A tracked map listed in
        # .gitignore is still committed normally, so that case is fine.)
        remote, a, _ = repos
        (a / ".gitignore").write_text(MAP_NAME + "\n", encoding="utf-8")
        git(a, "rm", "-q", "--cached", MAP_NAME)
        git(a, "add", ".gitignore")
        git(a, "commit", "-q", "-m", "ignore map")
        git(a, "push", "-q")
        with pytest.raises(TeamModeError, match="gitignored"):
            TeamSync(str(a)).begin()

    def test_uncommitted_changes_are_refused(self, repos):
        remote, a, _ = repos
        (a / "01_Mod" / "01_Page.qmd").write_text("edited\n", encoding="utf-8")
        with pytest.raises(TeamModeError, match="Uncommitted"):
            TeamSync(str(a)).begin()
        assert _lock_on_remote(remote) == ""

    def test_untracked_content_is_refused(self, repos):
        _, a, _ = repos
        (a / "01_Mod" / "02_New.qmd").write_text("new\n", encoding="utf-8")
        with pytest.raises(TeamModeError, match="Uncommitted"):
            TeamSync(str(a)).begin()

    def test_leftover_map_change_does_not_block(self, repos):
        _, a, _ = repos
        (a / MAP_NAME).write_text('{"_course_id": 1, "x": 1}\n', encoding="utf-8")
        t = TeamSync(str(a))
        t.begin()
        assert t.end()

    def test_unpushed_commit_is_refused_and_the_lock_released(self, repos):
        remote, a, _ = repos
        (a / "01_Mod" / "01_Page.qmd").write_text("edited\n", encoding="utf-8")
        git(a, "commit", "-q", "-am", "not pushed")
        with pytest.raises(TeamModeError, match="not pushed"):
            TeamSync(str(a)).begin()
        assert _lock_on_remote(remote) == "", "a refused sync left the lock behind"

    def test_pulls_a_colleagues_work_first(self, repos):
        _, a, b = repos
        (b / "01_Mod" / "02_Kalle.qmd").write_text("from Kalle\n", encoding="utf-8")
        git(b, "add", "-A")
        git(b, "commit", "-q", "-m", "Kalle's page")
        git(b, "push", "-q")

        t = TeamSync(str(a))
        t.begin()
        try:
            assert (a / "01_Mod" / "02_Kalle.qmd").exists()
        finally:
            t.end()


class TestLock:

    def test_second_sync_is_refused_while_the_first_runs(self, repos):
        remote, a, b = repos
        first = TeamSync(str(a), who="Mirza")
        first.begin()
        try:
            with pytest.raises(TeamModeError, match="Mirza.*is syncing"):
                TeamSync(str(b), who="Kalle").begin()
        finally:
            assert first.end()
        assert _lock_on_remote(remote) == ""

        second = TeamSync(str(b), who="Kalle")
        second.begin()
        assert second.end()

    def test_break_lock_takes_over_a_crashed_sync(self, repos):
        remote, a, b = repos
        TeamSync(str(a), who="Mirza").begin()     # crashes: never calls end()
        t = TeamSync(str(b), who="Kalle", break_lock=True)
        t.begin()
        assert t.end()
        assert _lock_on_remote(remote) == ""

    def test_lock_says_who_holds_it(self, repos):
        remote, a, b = repos
        t = TeamSync(str(a), who="Mirza")
        t.begin()
        try:
            info, sha = TeamSync(str(b))._read_lock()
            assert info["who"] == "Mirza"
            assert "since" in info and "host" in info
        finally:
            t.end()


class TestMainWiring:
    """sync_to_canvas.main() runs the guards, the sync, the report and the push in order."""

    def _run(self, clone, monkeypatch, sync_body, *extra):
        import sys
        from unittest.mock import MagicMock, patch
        import handlers.config as config
        import sync_to_canvas

        config._config_cache.clear()
        canvas = MagicMock()
        canvas.get_course.return_value.id = 1
        canvas.get_course.return_value.name = "Test course"
        canvas.get_current_user.return_value.name = "Mirza"
        monkeypatch.setattr(sys, "argv", ["sync_to_canvas", str(clone), *extra])
        with patch.object(sync_to_canvas, "Canvas", return_value=canvas), \
             patch.object(sync_to_canvas, "get_api_credentials", return_value=("u", "t")), \
             patch.object(sync_to_canvas, "_sync_content", side_effect=sync_body):
            return sync_to_canvas.main()

    def test_refused_conflict_exits_2_and_still_pushes_the_map(self, repos, monkeypatch):
        from handlers.sync_policy import DriftEvent, get_policy
        remote, a, b = repos

        def body(args, course, canvas, root):
            m = json.loads((a / MAP_NAME).read_text(encoding="utf-8"))
            m["01_Mod/01_Page.qmd"] = {"id": 9}
            (a / MAP_NAME).write_text(json.dumps(m) + "\n", encoding="utf-8")
            get_policy().events.append(DriftEvent("01_Mod/01_Page.qmd", "P", "refused"))
            return 0

        assert self._run(a, monkeypatch, body) == 2
        git(b, "pull", "-q")
        assert '"id": 9' in (b / MAP_NAME).read_text(encoding="utf-8")
        assert _lock_on_remote(remote) == ""

    def test_guard_failure_syncs_nothing(self, repos, monkeypatch):
        remote, a, _ = repos
        (a / "01_Mod" / "01_Page.qmd").write_text("uncommitted\n", encoding="utf-8")
        ran = []
        assert self._run(a, monkeypatch, lambda *x: ran.append(1) or 0) == 1
        assert ran == []
        assert _lock_on_remote(remote) == ""


class TestPushMap:

    def _sync_writes_map(self, clone, entry):
        m = json.loads((clone / MAP_NAME).read_text(encoding="utf-8"))
        m.update(entry)
        (clone / MAP_NAME).write_text(json.dumps(m) + "\n", encoding="utf-8")

    def test_map_is_committed_and_pushed(self, repos):
        remote, a, b = repos
        t = TeamSync(str(a), who="Mirza")
        t.begin()
        self._sync_writes_map(a, {"01_Mod/01_Page.qmd": {"id": 1}})
        assert t.end()

        git(b, "pull", "-q")
        assert "01_Mod/01_Page.qmd" in (b / MAP_NAME).read_text(encoding="utf-8")
        assert "Sync map after sync by Mirza" in git(b, "log", "-1", "--format=%s")
        assert _lock_on_remote(remote) == ""

    def test_push_rebases_over_content_pushed_during_the_sync(self, repos):
        remote, a, b = repos
        t = TeamSync(str(a), who="Mirza")
        t.begin()
        # Kalle is not syncing, just pushing content. That is allowed meanwhile.
        (b / "01_Mod" / "03_Meanwhile.qmd").write_text("x\n", encoding="utf-8")
        git(b, "add", "-A")
        git(b, "commit", "-q", "-m", "meanwhile")
        git(b, "push", "-q")

        self._sync_writes_map(a, {"01_Mod/01_Page.qmd": {"id": 1}})
        assert t.end()
        log = git(remote, "log", "--format=%s", "main")
        assert "meanwhile" in log and "Sync map after sync by Mirza" in log

    def test_nothing_to_commit_when_the_map_did_not_change(self, repos):
        remote, a, _ = repos
        before = git(remote, "rev-parse", "main")
        t = TeamSync(str(a))
        t.begin()
        assert t.end()
        assert git(remote, "rev-parse", "main") == before
