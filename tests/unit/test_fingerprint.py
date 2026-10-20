"""Source fingerprints replace mtime as the "has this changed?" test.

They must be the same on every machine for the same content (so a shared sync
map works), and must change whenever anything that shapes the published
result changes.
"""

import os

from handlers.fingerprint import (
    file_fingerprint, referenced_local_files, source_fingerprint,
)


def _course(tmp_path):
    root = tmp_path / "Course"
    mod = root / "01_Mod"
    (mod / "assets").mkdir(parents=True)
    (mod / "assets" / "fig.png").write_bytes(b"\x89PNG one")
    page = mod / "01_Page.qmd"
    page.write_text("---\ntitle: P\n---\n\n![A figure](assets/fig.png)\n\nText.\n",
                    encoding="utf-8")
    return root, page


class TestLineEndings:

    def test_crlf_and_lf_checkouts_agree(self, tmp_path):
        a = tmp_path / "a.qmd"
        b = tmp_path / "b.qmd"
        a.write_bytes(b"line one\r\nline two\r\n")
        b.write_bytes(b"line one\nline two\n")
        assert file_fingerprint(str(a)) == file_fingerprint(str(b))

    def test_source_fingerprint_agrees_across_line_endings(self, tmp_path):
        root, page = _course(tmp_path)
        lf_bytes = page.read_bytes().replace(b"\r\n", b"\n")   # write_text is CRLF on Windows
        page.write_bytes(lf_bytes)
        lf = source_fingerprint(str(page), str(root))
        page.write_bytes(lf_bytes.replace(b"\n", b"\r\n"))
        assert source_fingerprint(str(page), str(root)) == lf


class TestWhatCounts:

    def test_unchanged_content_same_fingerprint(self, tmp_path):
        root, page = _course(tmp_path)
        assert source_fingerprint(str(page), str(root)) == source_fingerprint(str(page), str(root))

    def test_touching_the_file_changes_nothing(self, tmp_path):
        # The case mtime got wrong: a pull or checkout rewrites the timestamp.
        root, page = _course(tmp_path)
        before = source_fingerprint(str(page), str(root))
        os.utime(page, (1, 1))
        assert source_fingerprint(str(page), str(root)) == before

    def test_editing_the_text_changes_it(self, tmp_path):
        root, page = _course(tmp_path)
        before = source_fingerprint(str(page), str(root))
        page.write_text(page.read_text(encoding="utf-8") + "More.\n", encoding="utf-8")
        assert source_fingerprint(str(page), str(root)) != before

    def test_replacing_a_referenced_image_changes_it(self, tmp_path):
        root, page = _course(tmp_path)
        before = source_fingerprint(str(page), str(root))
        (root / "01_Mod" / "assets" / "fig.png").write_bytes(b"\x89PNG two")
        assert source_fingerprint(str(page), str(root)) != before

    def test_course_wide_render_inputs_count(self, tmp_path):
        # #30: a branding.css change used to sync as a no-op.
        root, page = _course(tmp_path)
        before = source_fingerprint(str(page), str(root))
        (root / "branding.css").write_text("h1 { color: red }", encoding="utf-8")
        assert source_fingerprint(str(page), str(root)) != before

    def test_include_is_a_dependency(self, tmp_path):
        root, page = _course(tmp_path)
        (root / "01_Mod" / "_part.qmd").write_text("Included.\n", encoding="utf-8")
        page.write_text("---\ntitle: P\n---\n\n{{< include _part.qmd >}}\n", encoding="utf-8")
        before = source_fingerprint(str(page), str(root))
        (root / "01_Mod" / "_part.qmd").write_text("Changed.\n", encoding="utf-8")
        assert source_fingerprint(str(page), str(root)) != before

    def test_cross_link_target_is_not_a_dependency(self, tmp_path):
        # A link to another page becomes a Canvas URL; editing that page does
        # not change this one.
        root, page = _course(tmp_path)
        other = root / "01_Mod" / "02_Other.qmd"
        other.write_text("---\ntitle: O\n---\n", encoding="utf-8")
        page.write_text("---\ntitle: P\n---\n\nSee [other](02_Other.qmd).\n", encoding="utf-8")
        before = source_fingerprint(str(page), str(root))
        other.write_text("---\ntitle: O\n---\n\nEdited.\n", encoding="utf-8")
        assert source_fingerprint(str(page), str(root)) == before

    def test_same_content_in_another_clone_same_fingerprint(self, tmp_path):
        # Two clones at different paths: the fingerprint must not contain paths.
        root_a, page_a = _course(tmp_path / "a")
        root_b, page_b = _course(tmp_path / "b")
        assert (source_fingerprint(str(page_a), str(root_a))
                == source_fingerprint(str(page_b), str(root_b)))


class TestReferenceScan:

    def test_ignores_urls_anchors_and_missing_files(self, tmp_path):
        (tmp_path / "x.png").write_bytes(b"x")
        text = ("![a](x.png) [b](https://example.com/y.png) [c](#sec) "
                "![d](missing.png) <img src=\"x.png?v=2\">")
        found = referenced_local_files(text, str(tmp_path))
        assert found == [os.path.normpath(str(tmp_path / "x.png"))]
