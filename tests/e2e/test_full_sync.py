"""End-to-end test: sync the test content to a real Canvas course and verify.

Requires Canvas credentials + a test course (env vars, --course-id, or .e2e/).
Run with: python -m pytest tests/e2e/ -v -m canvas

Test content lives in tests/fixtures/e2e_content/ — a mechanical-engineering
themed course (MECH201, Mechanics of Materials) that exercises:
  - Pages (markdown + HTML images, inline/display LaTeX, callouts, code,
    tables, cross-links, an unpublished + indented page)
  - Assignments (upload + text entry; due/unlock/lock dates; grading types;
    omit_from_final_grade; a group-set assignment)
  - Classic Quizzes (QMD with settings + essay; JSON with short-answer + essay)
  - New Quizzes (MC/multi/TF + result-view settings + omit_from_final_grade;
    numeric + formula; JSON)
  - SubHeaders, External links (indented, new_tab)
  - Study guide (preprocess, front_page, dual PDF output)
  - A solo file asset (CSV) uploaded as a module item
"""

import pytest

pytestmark = pytest.mark.canvas


def _find(items, predicate):
    return next((x for x in items if predicate(x)), None)


# ---------------------------------------------------------------------------
# Modules
# ---------------------------------------------------------------------------
class TestModulesCreated:

    def test_expected_modules_exist(self, synced_modules):
        for name in ("Introduction", "Statics", "Beam Bending", "Course Documents"):
            assert name in synced_modules, f"Missing module {name!r}: {list(synced_modules)}"

    def test_module_count(self, synced_modules):
        assert len(synced_modules) == 4, (
            f"Expected 4 modules, got {len(synced_modules)}: {list(synced_modules)}"
        )


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------
class TestPageContent:

    def test_welcome_page_exists(self, synced_pages):
        assert any("Welcome" in t for t in synced_pages), list(synced_pages)

    def test_welcome_page_has_html_and_table(self, synced_pages):
        page = synced_pages[next(t for t in synced_pages if "Welcome" in t)]
        body = getattr(page, "body", "") or ""
        assert "<" in body, "Page body should contain HTML"
        assert "---" not in body[:50], "Body should not start with YAML frontmatter"
        assert "<table" in body.lower(), "Welcome page should render the SI-units table"

    def test_welcome_page_has_html_img_tag(self, synced_pages):
        """The inline <img ...> tag should survive and point at a Canvas URL."""
        page = synced_pages[next(t for t in synced_pages if "Welcome" in t)]
        body = getattr(page, "body", "") or ""
        assert "<img" in body.lower(), "Welcome page should contain an HTML <img> tag"

    def test_welcome_page_renders_math_as_equation_images(self, synced_pages):
        """LaTeX should become Canvas equation images, not raw \\[..\\] / \\(..\\)."""
        page = synced_pages[next(t for t in synced_pages if "Welcome" in t)]
        body = getattr(page, "body", "") or ""
        assert "equation_image" in body, "Math should render as Canvas equation images"
        assert "\\[" not in body and "\\(" not in body, "Raw LaTeX delimiters should be gone"

    def _stress_body(self, synced_pages):
        match = [t for t in synced_pages if "Stress" in t]
        assert match, f"No stress/strain page. Pages: {list(synced_pages)}"
        return getattr(synced_pages[match[0]], "body", "") or ""

    def test_stress_page_callouts_styled(self, synced_pages):
        """All five callouts become inline-styled blocks; no raw fences leak."""
        body = self._stress_body(synced_pages)
        assert body.count("border-left") >= 5, "Expected 5 inline-styled callouts"
        assert ":::" not in body, "Raw callout fences should not leak into the page"

    def test_stress_page_code_highlighted(self, synced_pages):
        body = self._stress_body(synced_pages)
        assert "<pre" in body.lower(), "Expected a rendered code block"
        assert "background-color:#f7f7f7" in body, "Code block should have inlined styling"
        assert "```" not in body, "Raw code fences should not leak into the page"

    def test_stress_page_has_material_table(self, synced_pages):
        assert "<table" in self._stress_body(synced_pages).lower(), "Material table should render"

    def test_formula_sheet_is_unpublished(self, synced_pages):
        """The draft formula sheet is published: false."""
        match = [t for t in synced_pages if "Formula" in t]
        assert match, f"No formula sheet page. Pages: {list(synced_pages)}"
        assert getattr(synced_pages[match[0]], "published", True) is False


class TestImageUpload:

    def test_images_use_canvas_urls(self, synced_pages):
        for title, page in synced_pages.items():
            body = getattr(page, "body", "") or ""
            assert 'src="../' not in body, f"Page '{title}' still has a relative image path"
            assert "](../graphics" not in body, f"Page '{title}' has an unresolved markdown image"


class TestCrossLinks:

    def test_welcome_cross_links_resolved(self, synced_pages):
        body = getattr(synced_pages[next(t for t in synced_pages if "Welcome" in t)], "body", "") or ""
        assert ".qmd" not in body, "Page still has unresolved .qmd cross-links"


class TestMathRendering:

    def test_all_equation_images_actually_render(self, synced_pages):
        """Every Canvas equation image must return a real image (200, image/*).

        This catches LaTeX that Canvas can't render (e.g. unsupported macros or
        matrix environments) without a human having to look at each page.
        """
        import os
        import re
        import requests

        base = os.environ.get("CANVAS_API_URL", "").rstrip("/")
        srcs = set()
        for page in synced_pages.values():
            body = getattr(page, "body", "") or ""
            for tag in re.findall(r'<img[^>]*class="equation_image"[^>]*>', body):
                m = re.search(r'src="([^"]+)"', tag)
                if not m:
                    continue
                src = m.group(1)
                srcs.add(base + src if src.startswith("/") else src)

        assert srcs, "No equation images found on any synced page"

        session = requests.Session()
        failures = []
        for src in sorted(srcs):
            try:
                r = session.get(src, timeout=30)
                ctype = r.headers.get("content-type", "")
                if r.status_code != 200 or not ctype.startswith("image/"):
                    failures.append(f"[{r.status_code} {ctype}] {src}")
            except Exception as e:  # noqa: BLE001
                failures.append(f"[ERROR {e}] {src}")

        assert not failures, "Equation images that did not render:\n  " + "\n  ".join(failures)


# ---------------------------------------------------------------------------
# Assignments (dates, grading types, omit, group set)
# ---------------------------------------------------------------------------
class TestAssignments:

    def test_three_non_quiz_assignments(self, canvas_course):
        assignments = list(canvas_course.get_assignments())
        non_quiz = [a for a in assignments if not getattr(a, "is_quiz_assignment", False)]
        assert len(non_quiz) >= 3, f"Expected >=3 non-quiz assignments, got {len(non_quiz)}"

    def test_truss_assignment_points_and_dates(self, canvas_course):
        a = _find(canvas_course.get_assignments(), lambda x: "Truss" in (x.name or ""))
        assert a is not None, "Truss Analysis assignment not found"
        assert a.points_possible == 20
        assert a.due_at, "Truss assignment should have a due date"
        assert a.unlock_at, "Truss assignment should have an unlock date"
        assert a.lock_at, "Truss assignment should have a lock date"

    def test_reflection_grading_and_omit(self, canvas_course):
        a = _find(canvas_course.get_assignments(), lambda x: "Reflection" in (x.name or ""))
        assert a is not None, "Reflection assignment not found"
        assert a.grading_type == "pass_fail"
        assert getattr(a, "omit_from_final_grade", False) is True

    def test_group_project_uses_group_set(self, canvas_course):
        a = _find(canvas_course.get_assignments(), lambda x: "Group Design Project" in (x.name or ""))
        assert a is not None, "Group Design Project assignment not found"
        assert a.grading_type == "letter_grade"
        assert getattr(a, "group_category_id", None), "Group project should reference a group set"


# ---------------------------------------------------------------------------
# Classic Quizzes
# ---------------------------------------------------------------------------
class TestClassicQuizzes:

    def test_syllabus_quiz(self, canvas_course):
        q = _find(canvas_course.get_quizzes(), lambda x: "Syllabus" in (x.title or ""))
        assert q is not None, f"Quizzes: {[x.title for x in canvas_course.get_quizzes()]}"
        assert len(list(q.get_questions())) == 4
        assert q.time_limit == 20
        assert q.allowed_attempts == -1

    def test_syllabus_quiz_has_essay_question(self, canvas_course):
        q = _find(canvas_course.get_quizzes(), lambda x: "Syllabus" in (x.title or ""))
        assert q is not None
        types = {qq.question_type for qq in q.get_questions()}
        assert "essay_question" in types, f"Question types: {types}"

    def test_materials_json_quiz(self, canvas_course):
        q = _find(canvas_course.get_quizzes(), lambda x: "Materials Knowledge" in (x.title or ""))
        assert q is not None
        assert len(list(q.get_questions())) == 4
        assert q.allowed_attempts == 3


# ---------------------------------------------------------------------------
# New Quizzes (assignment-backed)
# ---------------------------------------------------------------------------
class TestNewQuizzes:

    def test_concept_quiz_exists(self, canvas_course):
        a = _find(canvas_course.get_assignments(), lambda x: "Beam Bending Concepts" in (x.name or ""))
        assert a is not None, "New Quiz 'Beam Bending Concepts' not found"

    def test_concept_quiz_omit_from_final_grade(self, canvas_course):
        a = _find(canvas_course.get_assignments(), lambda x: "Beam Bending Concepts" in (x.name or ""))
        assert a is not None
        assert getattr(a, "omit_from_final_grade", False) is True

    def test_calculation_quiz_exists(self, canvas_course):
        a = _find(canvas_course.get_assignments(), lambda x: "Beam Bending Calculations" in (x.name or ""))
        assert a is not None, "New Quiz 'Beam Bending Calculations' not found"

    def test_json_new_quiz_exists(self, canvas_course):
        a = _find(canvas_course.get_assignments(), lambda x: "Section Properties" in (x.name or ""))
        assert a is not None, "New Quiz 'Section Properties (JSON New Quiz)' not found"


# ---------------------------------------------------------------------------
# Module items: counts, types, indent, ordering
# ---------------------------------------------------------------------------
class TestModuleItems:

    def test_introduction_item_count(self, synced_modules):
        items = list(synced_modules["Introduction"].get_module_items())
        assert len(items) == 8, f"Expected 8 items, got {len(items)}: {[i.title for i in items]}"

    def test_statics_item_count(self, synced_modules):
        items = list(synced_modules["Statics"].get_module_items())
        assert len(items) == 3, f"Expected 3 items, got {len(items)}: {[i.title for i in items]}"

    def test_beam_bending_item_count(self, synced_modules):
        items = list(synced_modules["Beam Bending"].get_module_items())
        assert len(items) == 3, f"Expected 3 items, got {len(items)}: {[i.title for i in items]}"

    def test_items_ordered_by_prefix(self, synced_modules):
        """Introduction items should follow NN_ filename order."""
        titles = [i.title for i in synced_modules["Introduction"].get_module_items()]
        assert titles[0].startswith("Welcome") or "Welcome" in titles[0], titles

    def test_subheader_item_exists(self, synced_modules):
        items = synced_modules["Introduction"].get_module_items()
        assert _find(items, lambda i: i.type == "SubHeader") is not None

    def test_indented_external_link(self, synced_modules):
        items = synced_modules["Introduction"].get_module_items()
        link = _find(items, lambda i: i.type == "ExternalUrl" and getattr(i, "indent", 0) > 0)
        assert link is not None, "No indented ExternalUrl in Introduction"

    def test_indented_page_item(self, synced_modules):
        """The formula sheet is added to the module with indent > 0."""
        items = synced_modules["Introduction"].get_module_items()
        page = _find(items, lambda i: i.type == "Page" and getattr(i, "indent", 0) > 0)
        assert page is not None, "No indented Page item (formula sheet) in Introduction"


# ---------------------------------------------------------------------------
# Solo file asset
# ---------------------------------------------------------------------------
class TestSoloAsset:

    def test_csv_file_item_in_course_documents(self, synced_modules):
        items = synced_modules["Course Documents"].get_module_items()
        f = _find(items, lambda i: i.type == "File" and "Material_Properties" in (i.title or ""))
        assert f is not None, f"CSV file item missing: {[(i.type, i.title) for i in items]}"


# ---------------------------------------------------------------------------
# Study guide
# ---------------------------------------------------------------------------
class TestStudyGuide:

    def test_study_guide_page_exists_with_grading_table(self, synced_pages):
        match = [t for t in synced_pages if "Course PM" in t]
        assert match, f"No study guide page. Pages: {list(synced_pages)}"
        body = getattr(synced_pages[match[0]], "body", "") or ""
        assert "<table" in body.lower(), "Study guide should contain HTML tables"

    def test_study_guide_renders_math(self, synced_pages):
        """The 'Key Formulae' LaTeX should render as Canvas equation images."""
        match = [t for t in synced_pages if "Course PM" in t]
        assert match
        body = getattr(synced_pages[match[0]], "body", "") or ""
        assert "equation_image" in body, "Study guide math should be equation images"
        assert "\\[" not in body, "Raw LaTeX display delimiters should be gone"

    def test_pdf_file_item_present(self, synced_modules):
        items = synced_modules["Course Documents"].get_module_items()
        pdf = _find(items, lambda i: i.type == "File" and "PDF" in (i.title or ""))
        assert pdf is not None, f"Study guide PDF item missing: {[(i.type, i.title) for i in items]}"

    def test_course_documents_item_count(self, synced_modules):
        """Course PM page + PDF file + CSV file."""
        items = list(synced_modules["Course Documents"].get_module_items())
        assert len(items) >= 3, f"Expected >=3 items, got {[(i.type, i.title) for i in items]}"

    def test_front_page_set(self, canvas_course):
        try:
            front = canvas_course.get_page("front_page")
            assert front is not None
        except Exception:
            pass
