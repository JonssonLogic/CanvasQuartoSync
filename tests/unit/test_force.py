"""--force forgets what was published, so files re-render, and keeps everything else."""

import argparse

from handlers.content_utils import load_sync_map, save_sync_map
from sync_to_canvas import _apply_force


def _map(tmp_path):
    save_sync_map(str(tmp_path), {
        "_course_id": 1,
        "01_M/01_A.qmd": {"id": 1, "source_hash": "a", "canvas_hash": "c1"},
        "01_M/02_B.qmd": {"id": 2, "mtime": 5.0, "canvas_hash": "c2"},   # pre-fingerprint
    })


def test_full_force_clears_fingerprints_and_legacy_mtimes(tmp_path):
    _map(tmp_path)
    _apply_force(argparse.Namespace(force=True, only=None), str(tmp_path))
    m = load_sync_map(str(tmp_path))
    assert m["01_M/01_A.qmd"] == {"id": 1, "canvas_hash": "c1"}
    assert m["01_M/02_B.qmd"] == {"id": 2, "canvas_hash": "c2"}
    assert m["_course_id"] == 1


def test_force_with_only_touches_one_file(tmp_path):
    _map(tmp_path)
    _apply_force(argparse.Namespace(force=True, only="01_M\\01_A.qmd"), str(tmp_path))
    m = load_sync_map(str(tmp_path))
    assert "source_hash" not in m["01_M/01_A.qmd"]
    assert m["01_M/02_B.qmd"]["mtime"] == 5.0
