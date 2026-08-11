"""
Guards against documentation drift.

The kit reference is authoritative for authors and the user guide stays complete
for human readers, so the same canvas.* settings are described in two places.
validate_content.CANVAS_SCHEMA is the machine-readable source of truth; these
tests assert both documents agree with it.

If one of these fails you added or renamed a setting. Update all three:
  - validate_content.py            CANVAS_SCHEMA
  - content_kit/.../frontmatter.md the table row
  - Guides/Canvas_Sync_User_Guide.md
"""

import os
import re

import pytest

from validate_content import CANVAS_SCHEMA, PDF_KEYS, RESULT_VIEW_KEYS

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
KIT_REFERENCE = os.path.join(
    PROJECT_ROOT, "content_kit", "skills", "canvas-content", "reference", "frontmatter.md")
USER_GUIDE = os.path.join(PROJECT_ROOT, "Guides", "Canvas_Sync_User_Guide.md")


def _schema_keys():
    """Every canvas.* setting name, including nested blocks."""
    keys = set()
    for type_schema in CANVAS_SCHEMA.values():
        keys |= set(type_schema)
    keys |= set(PDF_KEYS) | set(RESULT_VIEW_KEYS)
    return keys


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _documented_in_kit():
    """Setting names from the leading `| \\`key\\` |` column of every table row."""
    return set(re.findall(r"^\|\s*`([a-z_]+)`", _read(KIT_REFERENCE), re.MULTILINE))


class TestKitReference:

    def test_documents_every_schema_key(self):
        missing = sorted(_schema_keys() - _documented_in_kit())
        assert not missing, (
            f"Settings in CANVAS_SCHEMA with no table row in frontmatter.md: {missing}"
        )

    def test_does_not_invent_keys(self):
        extra = sorted(_documented_in_kit() - _schema_keys())
        assert not extra, (
            f"frontmatter.md documents settings the validator doesn't know: {extra}"
        )


class TestUserGuide:

    def test_mentions_every_schema_key(self):
        """The guide stays complete for humans reading it instead of the kit."""
        text = _read(USER_GUIDE)
        missing = sorted(k for k in _schema_keys()
                         if not re.search(rf"\b{re.escape(k)}\b", text))
        assert not missing, (
            f"Settings missing from Canvas_Sync_User_Guide.md: {missing}"
        )


class TestKitIsShippable:

    def test_skill_has_frontmatter_with_description(self):
        skill = _read(os.path.join(
            PROJECT_ROOT, "content_kit", "skills", "canvas-content", "SKILL.md"))
        assert skill.startswith("---"), "SKILL.md needs YAML frontmatter"
        head = skill.split("---")[1]
        assert "description:" in head, "SKILL.md needs a description for auto-invocation"

    def test_every_referenced_file_exists(self):
        """SKILL.md points at reference files; none may be dangling."""
        skill_dir = os.path.join(PROJECT_ROOT, "content_kit", "skills", "canvas-content")
        skill = _read(os.path.join(skill_dir, "SKILL.md"))
        for rel in set(re.findall(r"`(reference/[\w.-]+\.md)`", skill)):
            assert os.path.exists(os.path.join(skill_dir, rel)), f"missing {rel}"

    @pytest.mark.parametrize("name", [
        "frontmatter.md", "quizzes.md", "recipes.md", "linking.md",
        "study-guide.md", "calendar.md", "gotchas.md",
    ])
    def test_reference_file_is_listed_in_skill(self, name):
        skill = _read(os.path.join(
            PROJECT_ROOT, "content_kit", "skills", "canvas-content", "SKILL.md"))
        assert f"reference/{name}" in skill, f"{name} is not indexed in SKILL.md"

    def test_wrapper_templates_carry_placeholders(self):
        """Un-stamped templates must keep their tokens or scaffolding breaks."""
        kit = os.path.join(PROJECT_ROOT, "content_kit")
        for name in ("check_content.bat", "check_content.sh",
                     "update_kit.bat", "update_kit.sh"):
            text = _read(os.path.join(kit, name))
            assert "@@PYTHON@@" in text and "@@REPO@@" in text, name
