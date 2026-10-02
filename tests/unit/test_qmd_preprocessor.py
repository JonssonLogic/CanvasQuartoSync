"""Tests for handlers/qmd_preprocessor.py — study guide preprocessing."""

from handlers.qmd_preprocessor import (
    _split_frontmatter,
    _parse_yaml_lightweight,
    _parse_sections,
    _parse_table_row,
    _find_pipe_tables,
    _latex_escape,
    _markdown_to_latex_inline,
    _find_first_image,
    _generate_front_page,
    _generate_syllabus_link,
    LABELS,
    _syllabus_url,
    preprocess_study_guide,
)


# --- _split_frontmatter ---

class TestSplitFrontmatter:

    def test_standard(self):
        fm, body = _split_frontmatter("---\ntitle: Test\n---\nBody")
        assert "title: Test" in fm
        assert body == "Body"

    def test_no_frontmatter(self):
        fm, body = _split_frontmatter("Just body")
        assert fm == ""
        assert body == "Just body"

    def test_only_frontmatter(self):
        fm, body = _split_frontmatter("---\ntitle: T\n---")
        assert "title: T" in fm

    def test_empty_frontmatter(self):
        fm, body = _split_frontmatter("---\n---\nBody")
        assert fm == ""
        assert body == "Body"


# --- _parse_yaml_lightweight ---

class TestParseYamlLightweight:

    def test_canvas_preprocess(self):
        fm = "title: Test\ncanvas:\n  type: study_guide\n  preprocess: true"
        result = _parse_yaml_lightweight(fm)
        assert result["canvas"]["preprocess"] is True

    def test_canvas_preprocess_false(self):
        fm = "canvas:\n  preprocess: false"
        result = _parse_yaml_lightweight(fm)
        assert result["canvas"]["preprocess"] is False

    def test_top_level_keys(self):
        fm = "title: My Title\nauthor: Bob"
        result = _parse_yaml_lightweight(fm)
        assert result["title"] == "My Title"
        assert result["author"] == "Bob"

    def test_quoted_values(self):
        fm = 'title: "Hello World"'
        result = _parse_yaml_lightweight(fm)
        assert result["title"] == "Hello World"


# --- _parse_sections ---

class TestParseSections:

    def test_two_sections(self):
        body = "# Introduction\nSome text.\n\n# Schedule\nMore text."
        sections = _parse_sections(body)
        assert len(sections) == 2
        assert sections[0][0] == "Introduction"
        assert "Some text." in sections[0][1]
        assert sections[1][0] == "Schedule"

    def test_no_sections(self):
        sections = _parse_sections("Just plain text without headings.")
        assert sections == []

    def test_section_with_quarto_attrs(self):
        body = "# Grading Criteria {#sec-grading}\nContent here."
        sections = _parse_sections(body)
        assert len(sections) == 1
        assert sections[0][0] == "Grading Criteria {#sec-grading}"

    def test_h2_not_split(self):
        """Only H1 headings (#) split sections, not H2 (##)."""
        body = "# Section 1\nText\n## Subsection\nMore text"
        sections = _parse_sections(body)
        assert len(sections) == 1
        assert "## Subsection" in sections[0][1]


# --- _parse_table_row ---

class TestParseTableRow:

    def test_standard_row(self):
        assert _parse_table_row("| A | B | C |") == ["A", "B", "C"]

    def test_leading_trailing_pipes(self):
        assert _parse_table_row("| X | Y |") == ["X", "Y"]

    def test_empty_cells(self):
        assert _parse_table_row("|  |  |") == ["", ""]

    def test_whitespace_stripped(self):
        assert _parse_table_row("|  Hello  |  World  |") == ["Hello", "World"]


# --- _find_pipe_tables ---

class TestFindPipeTables:

    def test_single_table(self):
        text = "Some text.\n\n| Col1 | Col2 |\n|:-----|:-----|\n| a    | b    |\n| c    | d    |\n\nMore text."
        tables = _find_pipe_tables(text)
        assert len(tables) == 1
        _, _, headers, rows, _ = tables[0]
        assert headers == ["Col1", "Col2"]
        assert len(rows) == 2
        assert rows[0] == ["a", "b"]

    def test_no_table(self):
        tables = _find_pipe_tables("No tables here.")
        assert tables == []

    def test_table_with_footnotes(self):
        text = "| H |\n|---|\n| R |\n^1^ Note"
        tables = _find_pipe_tables(text)
        assert len(tables) == 1
        assert "^1^ Note" in tables[0][4]  # footnotes


# --- _latex_escape ---

class TestLatexEscape:

    def test_percent(self):
        assert _latex_escape("50%") == "50\\%"

    def test_ampersand(self):
        assert _latex_escape("A & B") == "A \\& B"

    def test_hash(self):
        assert _latex_escape("#1") == "\\#1"

    def test_already_latex_passthrough(self):
        text = "\\begin{tabular} stuff \\end{tabular}"
        assert _latex_escape(text) == text

    def test_underscore_in_text(self):
        result = _latex_escape("my_variable")
        assert "\\_" in result


# --- _markdown_to_latex_inline ---

class TestMarkdownToLatexInline:

    def test_bold(self):
        result = _markdown_to_latex_inline("**bold**")
        assert "\\textbf{bold}" in result

    def test_italic(self):
        result = _markdown_to_latex_inline("*italic*")
        assert "\\textit{italic}" in result

    def test_link(self):
        result = _markdown_to_latex_inline("[text](https://example.com)")
        assert "\\href{https://example.com}{text}" in result

    def test_br_tag(self):
        result = _markdown_to_latex_inline("Line1<br>Line2")
        assert "\\newline" in result

    def test_superscript(self):
        result = _markdown_to_latex_inline("^1^")
        assert "\\textsuperscript{1}" in result


# --- _find_first_image ---

class TestFindFirstImage:

    def test_finds_image(self):
        result = _find_first_image("Text ![Alt](img.jpg) more")
        assert result == ("Alt", "img.jpg")

    def test_no_image(self):
        assert _find_first_image("No images here") is None


# --- preprocess_study_guide (main entry) ---

class TestPreprocessStudyGuide:

    def test_opt_in_required(self):
        """Without preprocess: true, content passes through unchanged."""
        content = "---\ntitle: Test\ncanvas:\n  type: study_guide\n---\n# Section\nBody"
        result = preprocess_study_guide(content, {})
        assert result == content

    def test_with_preprocess_flag(self):
        """With preprocess: true, output is transformed."""
        content = "---\ntitle: Test\ncanvas:\n  type: study_guide\n  preprocess: true\n---\n# Introduction\nHello world."
        config = {"course_name": "Test Course", "course_code": "TC101"}
        result = preprocess_study_guide(content, config)
        assert result != content
        # Should contain content-visible blocks
        assert "content-visible" in result
        # Should preserve the heading
        assert "Introduction" in result

    def test_no_frontmatter_passthrough(self):
        content = "Just plain text"
        result = preprocess_study_guide(content, {})
        assert result == content


# --- Syllabus link ----------------------------------------------------------

class TestSyllabusUrl:
    """Where the official syllabus link points.

    The default was hardcoded to Jonkoping's public syllabus service, derived
    from the course code. That URL is not stable: syllabuses move, get
    withdrawn, or were never published there for a given course, and until now
    a course folder had no way to correct it. Reported from a live course whose
    link 404s.
    """

    def test_defaults_to_the_course_code_pattern(self):
        assert _syllabus_url({'course_code': 'MECH201'}) == (
            'https://kursinfoweb.hj.se/course_syllabuses/MECH201.pdf')

    def test_no_course_code_means_no_link(self):
        assert _syllabus_url({}) == ''

    def test_config_overrides_the_default(self):
        url = _syllabus_url({
            'course_code': 'MECH201',
            'syllabus_url': 'https://canvas.test/courses/1/files/42',
        })
        assert url == 'https://canvas.test/courses/1/files/42'

    def test_empty_string_removes_the_link(self):
        """Explicitly blank is a choice, not a fallback to the default."""
        assert _syllabus_url({'course_code': 'MECH201', 'syllabus_url': ''}) == ''

    def test_the_html_link_and_the_pdf_front_page_agree(self):
        """Two call sites, one resolver: they cannot drift apart."""
        config = {'course_code': 'MECH201', 'course_name': 'Mechanics',
                  'syllabus_url': 'https://example.test/syllabus.pdf'}
        labels = LABELS['english']

        front = _generate_front_page(config, labels)
        link = _generate_syllabus_link(_syllabus_url(config), labels)

        assert 'https://example.test/syllabus.pdf' in front
        assert 'https://example.test/syllabus.pdf' in link
        assert 'kursinfoweb' not in front
        assert 'kursinfoweb' not in link
