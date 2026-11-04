"""cqs --version tells dev builds apart by the BUILD stamp dev-deploy.ps1 writes."""

from handlers import BUILD_FILE, __version__, build_stamp, full_version


def _stamp(tmp_path, text):
    (tmp_path / BUILD_FILE).write_text(text, encoding="utf-8")


def test_release_install_shows_bare_version(tmp_path):
    assert full_version(str(tmp_path)) == __version__


def test_branch_build(tmp_path):
    _stamp(tmp_path, "commit=b6eb056\nbranch=feat/order-reconcile\nmodified=false\n")
    assert full_version(str(tmp_path)) == f"{__version__}+b6eb056 (feat/order-reconcile)"


def test_modified_build(tmp_path):
    _stamp(tmp_path, "commit=a41c9e2\nbranch=main\nmodified=true\n")
    assert build_stamp(str(tmp_path)) == "a41c9e2 (main, modified)"


def test_detached_build_has_no_branch(tmp_path):
    _stamp(tmp_path, "commit=a41c9e2\nbranch=\nmodified=false\n")
    assert build_stamp(str(tmp_path)) == "a41c9e2"


def test_stamp_without_commit_is_ignored(tmp_path):
    _stamp(tmp_path, "branch=main\n")
    assert full_version(str(tmp_path)) == __version__
