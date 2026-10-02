"""Tests for handlers/drift_detector.py — HTML normalization and hashing."""

import json
import os

from handlers.drift_detector import (
    _normalize_html,
    compute_content_hash,
    _html_to_text,
    resolve_stored_html,
    drift_report,
)


# --- _normalize_html ---

class TestNormalizeHtml:

    def test_removes_data_attrs(self):
        html = '<div data-api-endpoint="/api/v1/pages/123">text</div>'
        result = _normalize_html(html)
        assert "data-api-endpoint" not in result

    def test_removes_class(self):
        html = '<p class="some-canvas-class">text</p>'
        result = _normalize_html(html)
        assert "class=" not in result

    def test_removes_style(self):
        html = '<span style="color: red;">text</span>'
        result = _normalize_html(html)
        assert "style=" not in result

    def test_normalizes_whitespace(self):
        html = "<p>  hello   world  </p>"
        result = _normalize_html(html)
        assert "  " not in result  # No double spaces

    def test_empty_string(self):
        assert _normalize_html("") == ""

    def test_none_input(self):
        assert _normalize_html(None) == ""

    def test_preserves_content(self):
        html = "<p>Hello World</p>"
        result = _normalize_html(html)
        assert "Hello World" in result


# --- compute_content_hash ---

class TestComputeContentHash:

    def test_same_content_same_hash(self):
        h1 = compute_content_hash("<p>hello</p>")
        h2 = compute_content_hash("<p>hello</p>")
        assert h1 == h2

    def test_different_content_different_hash(self):
        h1 = compute_content_hash("<p>hello</p>")
        h2 = compute_content_hash("<p>world</p>")
        assert h1 != h2

    def test_whitespace_insensitive(self):
        h1 = compute_content_hash("<p>hello   world</p>")
        h2 = compute_content_hash("<p>hello world</p>")
        assert h1 == h2

    def test_class_attrs_ignored(self):
        h1 = compute_content_hash('<p class="foo">text</p>')
        h2 = compute_content_hash("<p>text</p>")
        assert h1 == h2

    def test_hash_is_hex_string(self):
        h = compute_content_hash("<p>test</p>")
        assert len(h) == 16
        assert all(c in "0123456789abcdef" for c in h)


# --- _html_to_text ---

class TestHtmlToText:

    def test_strips_tags(self):
        html = "<strong>bold</strong> and <em>italic</em>"
        text = _html_to_text(html)
        assert text == "bold and italic"

    def test_block_elements_add_newlines(self):
        html = "<p>Hello</p><p>World</p>"
        text = _html_to_text(html)
        assert "Hello" in text
        assert "World" in text

    def test_empty_string(self):
        assert _html_to_text("") == ""

    def test_none_input(self):
        assert _html_to_text(None) == ""

    def test_html_entities_decoded(self):
        html = "5 &gt; 3 &amp; 2 &lt; 4"
        text = _html_to_text(html)
        assert "5 > 3 & 2 < 4" in text

    def test_list_items(self):
        html = "<ul><li>Item 1</li><li>Item 2</li></ul>"
        text = _html_to_text(html)
        assert "Item 1" in text
        assert "Item 2" in text


# --- resolve_stored_html ---
#
# Canvas rewrites HTML on save, so the drift baseline must be what Canvas says
# it stored, never what we sent. These cover the resolution order offline; the
# round-trip itself is covered in tests/e2e/test_drift.py.

class TestResolveStoredHtml:

    class _Obj:
        def __init__(self, **kw):
            for k, v in kw.items():
                setattr(self, k, v)

    def test_prefers_the_response_body(self):
        obj = self._Obj(body="<p>canvas version</p>")
        assert resolve_stored_html(obj, 'body', "<p>sent</p>") == "<p>canvas version</p>"

    def test_reads_the_named_attribute(self):
        obj = self._Obj(description="<p>assignment body</p>")
        assert resolve_stored_html(obj, 'description', "<p>sent</p>") == "<p>assignment body</p>"

    def test_refetches_when_response_carried_no_body(self):
        obj = self._Obj(body=None)
        fresh = self._Obj(body="<p>refetched</p>")
        assert resolve_stored_html(obj, 'body', "<p>sent</p>", lambda: fresh) == "<p>refetched</p>"

    def test_refetches_when_attribute_is_absent(self):
        obj = self._Obj()
        fresh = self._Obj(body="<p>refetched</p>")
        assert resolve_stored_html(obj, 'body', "<p>sent</p>", lambda: fresh) == "<p>refetched</p>"

    def test_falls_back_to_sent_html_when_refetch_fails(self):
        def boom():
            raise RuntimeError("Canvas unreachable")
        obj = self._Obj(body=None)
        assert resolve_stored_html(obj, 'body', "<p>sent</p>", boom) == "<p>sent</p>"

    def test_falls_back_to_sent_html_when_refetch_is_also_empty(self):
        obj = self._Obj(body=None)
        fresh = self._Obj(body=None)
        assert resolve_stored_html(obj, 'body', "<p>sent</p>", lambda: fresh) == "<p>sent</p>"

    def test_falls_back_to_sent_html_with_no_refetch(self):
        obj = self._Obj(body="")
        assert resolve_stored_html(obj, 'body', "<p>sent</p>") == "<p>sent</p>"

    def test_handles_a_none_object(self):
        assert resolve_stored_html(None, 'body', "<p>sent</p>") == "<p>sent</p>"


# --- the normalizer does not paper over Canvas's rewrites ---
#
# Observed on a live course: Canvas absolutizes course-relative links, injects
# <tbody>, and normalizes void tags. The normalizer strips volatile attributes
# but deliberately does not try to undo these, which is exactly why the baseline
# has to come from Canvas rather than from us.

class TestCanvasRewritesAreNotNormalizedAway:

    def test_absolutized_link_changes_the_hash(self):
        sent = '<a href="/courses/74/pages/intro">x</a>'
        stored = '<a href="https://ju.instructure.com/courses/74/pages/intro">x</a>'
        assert compute_content_hash(sent) != compute_content_hash(stored)

    def test_injected_tbody_changes_the_hash(self):
        sent = '<table><tr><td>a</td></tr></table>'
        stored = '<table><tbody><tr><td>a</td></tr></tbody></table>'
        assert compute_content_hash(sent) != compute_content_hash(stored)

    def test_void_tag_normalization_changes_the_hash(self):
        assert compute_content_hash('<p>a<br/>b</p>') != compute_content_hash('<p>a<br>b</p>')


# --- drift_report -----------------------------------------------------------
#
# The machine-readable shape behind --check-drift --json. A GUI, a cron job or
# CI consumes this, so the field names are a contract: renaming one breaks a
# caller that cannot be seen from here.

class TestDriftReport:

    class _Course:
        id = 74
        name = "Training_cenmir"

    ITEM = {
        'file': '01_Intro/01_Welcome.qmd',
        'type': 'page',
        'title': 'Welcome',
        'stored_hash': 'aaaa',
        'current_hash': 'bbbb',
        'diff': '--- last-synced\n+++ current-canvas\n-old\n+new',
    }

    def _report(self, drifted, include_diff=False, root='/course'):
        return drift_report(self._Course(), drifted, root, include_diff)

    def test_carries_the_canvas_copy_so_a_diff_can_be_opened(self):
        """Without this the extension read undefined and died silently.

        Uri.file(undefined) throws inside an async handler, so the user
        clicked Diff, watched the progress notification, and then nothing
        happened at all. The diff *text* is opt-in because it can be long;
        this is one path, and it is the only way to open a diff editor on the
        Canvas side.
        """
        item = dict(self.ITEM, canvas_qmd_path='/tmp/canvas__01_Welcome.qmd')

        out = self._report([item])

        assert out['drifted'][0]['canvas_qmd_path'] == '/tmp/canvas__01_Welcome.qmd'

    def test_omits_the_canvas_copy_when_none_was_written(self):
        """check_all_drift(include_diff=False) writes no candidate file."""
        out = self._report([dict(self.ITEM)])

        assert 'canvas_qmd_path' not in out['drifted'][0]

    def test_reports_the_course(self):
        out = self._report([])
        assert out['course_id'] == 74
        assert out['course_name'] == "Training_cenmir"

    def test_clean_course_is_an_empty_list_not_a_missing_key(self):
        """A caller should be able to len() it without checking existence."""
        assert self._report([])['drifted'] == []

    def test_carries_the_fields_a_caller_needs(self):
        item = self._report([self.ITEM])['drifted'][0]
        assert item['file'] == '01_Intro/01_Welcome.qmd'
        assert item['type'] == 'page'
        assert item['title'] == 'Welcome'
        assert item['stored_hash'] == 'aaaa'
        assert item['current_hash'] == 'bbbb'

    def test_local_path_is_absolute_and_native(self):
        item = self._report([self.ITEM], root=os.path.join('/course'))['drifted'][0]
        assert item['local_path'].endswith(
            os.path.join('01_Intro', '01_Welcome.qmd'))

    def test_diff_is_omitted_by_default(self):
        """It can be long, so it is opt-in, as in the human report."""
        assert 'diff' not in self._report([self.ITEM])['drifted'][0]

    def test_diff_is_included_when_asked_for(self):
        item = self._report([self.ITEM], include_diff=True)['drifted'][0]
        assert item['diff'].startswith('--- last-synced')

    def test_missing_optional_fields_do_not_raise(self):
        """check_all_drift omits hashes on some paths; the report must not care."""
        bare = {'file': 'a.qmd', 'type': 'page', 'title': 'A'}
        item = self._report([bare], include_diff=True)['drifted'][0]
        assert item['stored_hash'] == ''
        assert item['diff'] == ''

    def test_output_is_json_serialisable(self):
        json.dumps(self._report([self.ITEM], include_diff=True))


def test_include_diff_false_reports_status_without_building_diffs(tmp_path, monkeypatch):
    """The panel wants a status light, not a diff editor.

    Building the diff converts HTML to text per item and writes a candidate
    .qmd into .canvas_diff_temp/. That is wasted work when nobody is going to
    open a diff, and it leaves files behind on every panel refresh.
    """
    from unittest.mock import MagicMock
    import handlers.drift_detector as dd
    from handlers.content_utils import save_sync_map

    (tmp_path / "01_Intro").mkdir()
    (tmp_path / "01_Intro" / "01_Welcome.qmd").write_text(
        '---\ntitle: "Welcome"\ncanvas:\n  type: page\n---\n\nLocal.\n', encoding="utf-8")
    save_sync_map(str(tmp_path), {
        "01_Intro/01_Welcome.qmd": {"id": 5, "canvas_hash": "stale"},
    })

    page = MagicMock()
    page.body = "<p>Changed in Canvas</p>"
    page.title = "Welcome"
    course = MagicMock()
    course.get_page.return_value = page

    called = []
    monkeypatch.setattr(dd, "_write_diff_temp",
                        lambda *a, **k: called.append(a) or "never")

    drifted = dd.check_all_drift(course, str(tmp_path), include_diff=False)

    assert len(drifted) == 1
    assert drifted[0]["file"] == "01_Intro/01_Welcome.qmd"
    assert "diff" not in drifted[0]
    assert "canvas_qmd_path" not in drifted[0]
    assert called == []
    assert not (tmp_path / ".canvas_diff_temp").exists()
