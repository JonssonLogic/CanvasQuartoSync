"""The Course Board's publish toggle writes the new state into the file (#48)."""

import json
import frontmatter
from handlers.content_utils import write_published


def _write(tmp_path, text, name='a.qmd'):
    p = tmp_path / name
    p.write_bytes(text.encode('utf-8'))
    return p


def test_flips_existing_value_and_keeps_the_rest(tmp_path):
    p = _write(tmp_path, '---\ntitle: "X"  # keep\ncanvas:\n  type: assignment\n  published: true  # note\n  points: 5\n---\n\nBody\n')
    assert write_published(str(p), False) is None
    assert p.read_text(encoding='utf-8') == (
        '---\ntitle: "X"  # keep\ncanvas:\n  type: assignment\n  published: false  # note\n  points: 5\n---\n\nBody\n')


def test_adds_key_to_canvas_block(tmp_path):
    p = _write(tmp_path, '---\ntitle: X\ncanvas:\n    type: page\n\nformat: html\n---\nBody\n')
    assert write_published(str(p), True) is None
    meta = frontmatter.load(str(p)).metadata
    assert meta['canvas'] == {'type': 'page', 'published': True}
    assert meta['format'] == 'html'
    assert '    published: true' in p.read_text(encoding='utf-8')


def test_adds_canvas_block(tmp_path):
    p = _write(tmp_path, '---\ntitle: X\n---\nBody\n')
    assert write_published(str(p), True) is None
    assert frontmatter.load(str(p)).metadata == {'title': 'X', 'canvas': {'published': True}}


def test_adds_frontmatter(tmp_path):
    p = _write(tmp_path, 'Body\n')
    assert write_published(str(p), False) is None
    post = frontmatter.load(str(p))
    assert post.metadata == {'canvas': {'published': False}}
    assert post.content == 'Body'


def test_does_not_touch_nested_published(tmp_path):
    p = _write(tmp_path, '---\ncanvas:\n  type: study_guide\n  pdf:\n    published: true\n  published: true\n---\n')
    assert write_published(str(p), False) is None
    canvas = frontmatter.load(str(p)).metadata['canvas']
    assert canvas['published'] is False
    assert canvas['pdf']['published'] is True


def test_study_guide_pdf_path(tmp_path):
    p = _write(tmp_path, '---\ncanvas:\n  published: true\n  pdf:\n    title: T\n---\n')
    assert write_published(str(p), False, ('canvas', 'pdf', 'published')) is None
    canvas = frontmatter.load(str(p)).metadata['canvas']
    assert canvas['published'] is True
    assert canvas['pdf'] == {'title': 'T', 'published': False}


def test_keeps_crlf(tmp_path):
    p = _write(tmp_path, '---\r\ncanvas:\r\n  published: false\r\n---\r\nBody\r\n')
    assert write_published(str(p), True) is None
    assert p.read_bytes() == b'---\r\ncanvas:\r\n  published: true\r\n---\r\nBody\r\n'


def test_refuses_flow_style(tmp_path):
    text = '---\ncanvas: {type: page}\n---\n'
    p = _write(tmp_path, text)
    assert write_published(str(p), True)
    assert p.read_text(encoding='utf-8') == text


def test_json_quiz(tmp_path):
    p = _write(tmp_path, json.dumps({'canvas': {'title': 'Q', 'published': True}, 'questions': []}), 'q.json')
    assert write_published(str(p), False) is None
    data = json.loads(p.read_text(encoding='utf-8'))
    assert data['canvas'] == {'title': 'Q', 'published': False}


def test_pdf_has_nowhere_to_write(tmp_path):
    p = _write(tmp_path, 'x', 'handout.pdf')
    assert write_published(str(p), True)
