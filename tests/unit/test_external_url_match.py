"""External links are found in a module by URL, as Canvas stores it.

Canvas saves ``mechanics.ju.se`` as ``http://mechanics.ju.se``. Comparing the
raw strings missed the item, so every sync added the link again.
"""

from types import SimpleNamespace

import pytest

from handlers.base_handler import same_external_url
from handlers.external_link_handler import ExternalLinkHandler


@pytest.mark.parametrize("canvas_url, local_url", [
    ("http://mechanics.ju.se", "mechanics.ju.se"),
    ("http://mechanics.ju.se", "  mechanics.ju.se  "),
    ("https://example.com/a", "https://example.com/a"),
    ("https://example.com/", "https://example.com"),
    ("https://Example.COM/a", "HTTPS://example.com/a"),
])
def test_same(canvas_url, local_url):
    assert same_external_url(canvas_url, local_url)


@pytest.mark.parametrize("canvas_url, local_url", [
    ("http://python.ju.se", "mechanics.ju.se"),
    ("https://example.com/A", "https://example.com/a"),
    ("https://example.com/a?x=1", "https://example.com/a?x=2"),
    (None, "mechanics.ju.se"),
    ("", ""),
])
def test_different(canvas_url, local_url):
    assert not same_external_url(canvas_url, local_url)


class _Item(SimpleNamespace):
    def edit(self, module_item):
        self.edited = module_item


class _Module:
    name = "Testar"

    def __init__(self, items):
        self.items = items
        self.created = []

    def get_module_items(self):
        return list(self.items)

    def create_module_item(self, module_item):
        self.created.append(module_item)
        return _Item(**module_item, id=999, published=False, indent=0)


def test_resync_of_schemeless_link_does_not_duplicate(tmp_path):
    f = tmp_path / "test.qmd"
    f.write_text('---\ntitle: "Ett test"\ncanvas:\n  type: external_url\n'
                 '  url: "mechanics.ju.se"\n  published: false\n---\n', encoding="utf-8")
    existing = _Item(type="ExternalUrl", title="Ett test", external_url="http://mechanics.ju.se",
                     id=612452, indent=0, published=False)
    module = _Module([existing])

    got = ExternalLinkHandler().sync(str(f), course=None, module=module)

    assert module.created == []
    assert got is existing
