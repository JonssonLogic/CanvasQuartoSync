"""
YH variant of the study guide preprocessor.

Selected with ``yh = true`` in config.toml (the whole course), or
``preprocessor: yh`` under ``canvas:`` in one study guide. A YH course (yrkeshögskola) counts
in Yh-poäng and grades IG/G/VG, so the cover page and the grading criteria
differ from a university course. Everything else is the ordinary
preprocessor: this module reuses its helpers and leaves it untouched.
"""

import re

from handlers.qmd_preprocessor import (
    LABELS,
    SECTION_ALIASES,
    GRADE_COLORS,
    _split_frontmatter,
    _parse_yaml_lightweight,
    _load_branding,
    _parse_sections,
    _generate_front_page,
    _generate_syllabus_link,
    _syllabus_url,
    _find_pipe_tables,
    _table_to_latex,
    _latex_escape,
    _process_research_connection,
)

YH_LABELS = {
    'swedish': {**LABELS['swedish'], 'credits': 'Yh-poäng'},
    'english': {**LABELS['english'], 'credits': 'YH credits'},
}

# Lowest grade first. IG/G/VG by default; the header row of the table wins.
YH_GRADE_COLORS = [GRADE_COLORS['Fail'], GRADE_COLORS['3'], GRADE_COLORS['5']]


def preprocess_study_guide(qmd_content: str, config: dict, config_dir: str = '.') -> str:
    """Same contract as qmd_preprocessor.preprocess_study_guide, for YH courses."""
    frontmatter, body = _split_frontmatter(qmd_content)

    fm_meta = _parse_yaml_lightweight(frontmatter)
    canvas_meta = fm_meta.get('canvas', {})
    if not canvas_meta.get('preprocess'):
        return qmd_content

    lang = config.get('language', 'swedish')
    labels = YH_LABELS.get(lang, YH_LABELS['swedish'])

    branding = _load_branding(config, config_dir)
    sections = _parse_sections(body)

    parts = [f"---\n{frontmatter}\n---\n", branding['css_block'], ""]
    parts.append(_generate_front_page(config, labels, branding['colors']))
    parts.append("")

    syllabus_url = _syllabus_url(config)
    if syllabus_url:
        parts.append(_generate_syllabus_link(syllabus_url, labels))
        parts.append("")

    for heading, content in sections:
        heading_clean = re.sub(r'\s*\{[^}]*\}', '', heading.lower().strip())
        section_key = SECTION_ALIASES.get(heading_clean)

        parts.append(f"# {heading}\n")
        if section_key == 'grading_criteria':
            parts.append(_process_grading_criteria(content))
        elif section_key == 'teaching_staff':
            parts.append(_process_teaching_staff(content))
        elif section_key == 'research_connection':
            parts.append(_process_research_connection(content))
        else:
            parts.append(_process_generic_section(content))
        parts.append("")

    return "\n".join(parts)


def _column_widths(headers: list, rows: list, total: float = 0.9) -> list:
    """PDF column widths in proportion to how much text each column holds,
    so a long description column is not squeezed to the width of a date.
    The square root evens out extremes; every column keeps a minimum."""
    n = len(headers)
    weights = []
    for i in range(n):
        longest = max([len(headers[i])] + [len(r[i]) for r in rows if len(r) > i])
        weights.append(max(longest, 6) ** 0.5)
    widths = [total * w / sum(weights) for w in weights]
    floor = min(0.1, total / n)
    widths = [max(w, floor) for w in widths]
    scale = total / sum(widths)
    return [round(w * scale, 3) for w in widths]


def _process_generic_section(content: str) -> str:
    """Markdown table for HTML and a longtable for PDF, as in the ordinary
    preprocessor, but with column widths that follow the content."""
    tables = _find_pipe_tables(content)
    if not tables:
        return content

    lines = content.split('\n')
    result = []
    last_end = 0

    for start, end, headers, rows, footnotes in tables:
        before = '\n'.join(lines[last_end:start]).strip()
        if before:
            result += [before, ""]

        result += ['::: {.content-visible when-format="html"}', "",
                   '\n'.join(lines[start:end]).strip()]
        if footnotes:
            result.append(footnotes)
        result += ["", ":::", ""]

        result += ['::: {.content-visible when-format="pdf"}', "",
                   _table_to_latex(headers, rows, _column_widths(headers, rows))]
        if footnotes:
            latex_fn = re.sub(r'\^(\w+)\^', r'\\textsuperscript{\1}', footnotes)
            result.append(f"\n```{{=latex}}\n{{\\footnotesize {_latex_escape(latex_fn)}}}\n```")
        result += ["", ":::", ""]

        last_end = end

    after = '\n'.join(lines[last_end:]).strip()
    if after:
        result.append(after)
    return '\n'.join(result)


def _grade_colors(n: int) -> list:
    """Red for the failing grade, green for the top, orange between."""
    if n <= 1:
        return YH_GRADE_COLORS[-1:] * n
    return [YH_GRADE_COLORS[0]] + [YH_GRADE_COLORS[1]] * (n - 2) + [YH_GRADE_COLORS[-1]]


def _process_grading_criteria(content: str) -> str:
    """Collapsible cards (HTML) and a longtable (PDF), one card per
    läranderesultat. Grade labels are taken from the header row, so
    | Läranderesultat | IG | G | VG | gives IG, G and VG."""
    tables = _find_pipe_tables(content)
    if not tables:
        return content

    lines = content.split('\n')
    start, end, headers, rows, _ = tables[0]
    grades = headers[1:]
    colors = _grade_colors(len(grades))

    result = []
    before = '\n'.join(lines[:start]).strip()
    if before:
        result += [before, ""]

    result.append('::: {.content-visible when-format="html"}')
    for row in rows:
        title = row[0] if row else ''
        # A numbered läranderesultat ("1. Beskriva …") would open an ordered
        # list inside the HTML block, and Quarto then drops the rest of the
        # document from the PDF. The entity renders as the same full stop.
        title = re.sub(r'^(\d+)\.', r'\1&#46;', title)
        paras = '\n'.join(
            f'<p><strong style="color: {color};">{grade}:</strong> {row[i] if len(row) > i else ""}</p>'
            for i, (grade, color) in enumerate(zip(grades, colors), start=1)
        )
        result.append(f"""
<details style="border: 1px solid #ddd; border-radius: 8px; padding: 10px; margin-bottom: 10px; background-color: #f9f9f9;">
<summary style="cursor: pointer; font-weight: bold; color: var(--ic-brand-font-color-dark);">{title}</summary>
{paras}
</details>""")
    result += ["", ":::", ""]

    result.append('::: {.content-visible when-format="pdf"}')
    result.append("")
    result.append(_table_to_latex(headers, rows, _column_widths(headers, rows)))
    result.append("")
    result.append(":::")

    after = '\n'.join(lines[end:]).strip()
    if after:
        result += ["", after]
    return '\n'.join(result)


def _process_teaching_staff(content: str) -> str:
    """Contact cards (HTML) and a table (PDF). The table is
    | Namn | Roll | Bild | Länk | Kontakt |: the photo is optional, since
    staff such as the utbildningsledare often have none, and the contact
    column is optional too (defaults to Canvas-meddelande)."""
    tables = _find_pipe_tables(content)
    if not tables:
        return content

    lines = content.split('\n')
    start, end, headers, rows, _ = tables[0]

    def cell(row, i):
        return row[i].strip() if len(row) > i else ''

    result = []
    before = '\n'.join(lines[:start]).strip()
    if before:
        result += [before, ""]

    pdf_headers = [cell(headers, 0) or 'Namn', cell(headers, 1) or 'Roll', cell(headers, 4) or 'Kontakt']
    pdf_rows = [[cell(r, 0), cell(r, 1), cell(r, 4) or 'Canvas-meddelande'] for r in rows]
    result += ['::: {.content-visible when-format="pdf"}', "",
               _table_to_latex(pdf_headers, pdf_rows, [0.30, 0.30, 0.30]), "", ":::", ""]

    result.append('::: {.content-visible when-format="html"}')
    for row in rows:
        name, role, image, link, contact = (cell(row, i) for i in range(5))
        name_html = (f'<a class="inline_disabled" href="{link}" target="_blank" rel="noopener">{name}</a>'
                     if link else name)
        img_html = (f'<img style="float: left; margin-right: 15px;" role="presentation" src="{image}" alt="" width="136" height="164" />\n'
                    if image else '')
        if re.fullmatch(r'[^@\s]+@[^@\s]+\.\w+', contact):
            contact = f'<a href="mailto:{contact}">{contact}</a>'
        contact_html = f'<br>{contact}' if contact else ''
        result.append(f"""
<div style="clear: both; overflow: hidden; margin-bottom: 20px;">
{img_html}<p><span style="font-size: 1.5em; color: var(--ic-brand-font-color-dark);">{name_html}</span><br>{role}{contact_html}</p>
</div>""")
    result += ["", ":::"]

    after = '\n'.join(lines[end:]).strip()
    if after:
        result += ["", after]
    return '\n'.join(result)
