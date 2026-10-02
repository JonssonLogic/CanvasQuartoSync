# Study guides (Course PM / syllabus)

`canvas.type: study_guide` renders **one** source file into **two** Canvas artefacts:

1. an HTML page in the module where the file lives, and
2. a PDF uploaded as a file item in a module you nominate.

Use it for a Course PM or syllabus that must also exist as a formatted, downloadable
PDF. Don't use it for ordinary pages: the PDF half needs a LaTeX installation
(`quarto install tinytex`), and without one it fails and only the page syncs.

**Filename trap:** any `.qmd` whose filename contains `studyguide` or `kurspm`
(case-insensitive) is treated as a study guide *even without* `canvas.type`. Avoid those
words in ordinary page filenames.

## Frontmatter

```yaml
---
title: "Course PM - Mechanics of Materials (MECH201)"
canvas:
  type: study_guide
  preprocess: true
  published: true
  pdf:
    target_module: "Course Documents"
    filename: "CoursePM-MECH201.pdf"
    title: "Course PM (PDF)"
    published: true
---
```

`target_module` is created if it doesn't exist; it defaults to the current module.

## Preprocessed mode (`preprocess: true`) - recommended

Write **plain markdown only**: headings, paragraphs, lists, pipe tables. No LaTeX, no raw
HTML, no `content-visible` blocks. The preprocessor generates the dual-format output,
including a PDF cover page built from `config.toml`, brand colours from `branding.css`,
and a markdown table for HTML paired with a styled LaTeX `longtable` for PDF.

Required in the content root's `config.toml`:

```toml
course_name = "Mechanics of Materials"
course_code = "MECH201"
credits     = "7.5 ECTS"
semester    = "Spring 2026"
language    = "english"     # or "swedish"
```

Optional:

```toml
syllabus_url = "https://canvas.example.edu/courses/1/files/42"
```

The study guide links to the official syllabus from both the HTML page and the PDF
cover. Without this key the link is built from the course code as
`https://kursinfoweb.hj.se/course_syllabuses/<course_code>.pdf`, which is Jonkoping's
public syllabus service. Set `syllabus_url` to point somewhere else: a PDF uploaded to
Canvas, another institution's register, or the syllabus kept in the course repo. Set it
to `""` to leave the link out entirely.

`_quarto.yml` must also exist in the content root - it supplies the LaTeX packages and
colour definitions the PDF needs.

### Sections with special handling

Detected by their `#` heading text, in English or Swedish:

| Heading | What happens |
|---|---|
| `# Grading Criteria` / `# Betygskriterier` | A 5-column table (ILO / Fail / 3 / 4 / 5) becomes collapsible cards in HTML and a longtable in the PDF |
| `# Teaching Staff` / `# Lärare` | A 4-column table (Name / Role / Image / Link) becomes photo cards in HTML and a plain table in the PDF |
| `# Research Connection` / `# Forskningsanknytning` | Wrapped in a collapsible block in HTML |
| Any other section containing a table | Markdown table for HTML, LaTeX longtable for PDF |

Expected column orders:

```markdown
# Grading Criteria

| ILO | Fail | 3 | 4 | 5 |
|:----|:-----|:--|:--|:--|
| Analyse stress states | Cannot… | Can… | Can… | Can… |

# Teaching Staff

| Name | Role | Image | Link |
|:-----|:-----|:------|:-----|
| Jane Doe | Course responsible | jane.png | https://example.com/jane |
```

Sections are split on `#` (level-one) headings, so use `#` for top-level sections and
`##` below.

## Manual mode

Without `preprocess: true` the file renders as written, and dual-format content is your
responsibility via Quarto's conditional blocks:

```markdown
::: {.content-visible when-format="html"}
Only in the Canvas page.
:::

::: {.content-visible when-format="pdf"}
Only in the PDF.
:::
```

Prefer preprocessed mode unless you need layout it can't express.
