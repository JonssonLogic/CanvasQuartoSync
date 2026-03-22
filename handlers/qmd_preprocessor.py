"""
QMD Preprocessor for Study Guides.

Takes a minimal, clean QMD (pure markdown with pipe tables) and produces
a styled dual-format QMD with content-visible blocks for HTML and PDF.

Usage (standalone):
    python qmd_preprocessor.py input.qmd --config config.toml [-o output.qmd]

Usage (from study_guide_handler):
    from handlers.qmd_preprocessor import preprocess_study_guide
    processed = preprocess_study_guide(raw_content, config_dict)
"""

import re
import os
import sys

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

GRADE_COLORS = {
    'Fail': '#c00',
    '3': '#f90',
    '4': '#09f',
    '5': '#090',
}

# Default branding (used when no branding.css is provided)
DEFAULT_BRAND = {
    '--brand-primary': '#333333',
    '--brand-heading': '#222222',
    '--brand-accent': '#666666',
    '--brand-warn': '#cc9900',
    '--brand-grey': '#999999',
}


# ---------------------------------------------------------------------------
# Branding
# ---------------------------------------------------------------------------

def _load_branding(config: dict, config_dir: str = '.') -> dict:
    """Load branding from CSS file specified in config, or use defaults.

    Returns dict with:
        'css_block': str — the raw HTML block to inject (link + style tags)
        'colors': dict — parsed CSS custom properties (--brand-* → hex)
    """
    branding_config = config.get('branding', {})
    css_path = branding_config.get('css', '')
    canvas_css_url = branding_config.get('canvas_css_url', '')

    colors = dict(DEFAULT_BRAND)
    css_content = ''

    if css_path:
        if not os.path.isabs(css_path):
            css_path = os.path.join(config_dir, css_path)
        if os.path.exists(css_path):
            with open(css_path, 'r', encoding='utf-8') as f:
                css_content = f.read()
            # Parse --brand-* variables from :root block
            for match in re.finditer(r'(--brand-[\w-]+)\s*:\s*([^;]+);', css_content):
                colors[match.group(1)] = match.group(2).strip()

    # Build the HTML block
    parts = ['```{=html}']
    if canvas_css_url:
        parts.append(f'<link rel="stylesheet" href="{canvas_css_url}">')
    if css_content:
        parts.append(f'<style>\n{css_content}\n</style>')
    parts.append('```')

    return {
        'css_block': '\n'.join(parts),
        'colors': colors,
    }


def _colors_to_latex(colors: dict) -> str:
    """Convert CSS hex colors to LaTeX \\definecolor commands."""
    lines = []
    name_map = {
        '--brand-primary': 'brand-primary',
        '--brand-heading': 'brand-heading',
        '--brand-accent': 'brand-accent',
        '--brand-warn': 'brand-warn',
        '--brand-grey': 'brand-grey',
    }
    for css_var, latex_name in name_map.items():
        hex_color = colors.get(css_var, '#000000').lstrip('#')
        if len(hex_color) == 3:
            hex_color = ''.join(c * 2 for c in hex_color)
        try:
            r, g, b = int(hex_color[0:2], 16), int(hex_color[2:4], 16), int(hex_color[4:6], 16)
            lines.append(f'\\definecolor{{{latex_name}}}{{RGB}}{{{r},{g},{b}}}')
        except (ValueError, IndexError):
            pass
    return '\n'.join(lines)

LABELS = {
    'english': {
        'course_pm': 'Course PM',
        'course_code': 'Course code',
        'credits': 'Credits',
        'semester': 'Semester',
        'syllabus': 'Syllabus',
        'syllabus_link_text': 'Official Syllabus (PDF)',
    },
    'swedish': {
        'course_pm': 'KursPM',
        'course_code': 'Kurskod',
        'credits': 'Högskolepoäng',
        'semester': 'Termin',
        'syllabus': 'Kursplan',
        'syllabus_link_text': 'Officiell kursplan (PDF)',
    },
}

# Sections with special processing (matched case-insensitively)
# Maps all recognized names (EN + SV) to a canonical key.
SECTION_ALIASES = {
    'grading criteria': 'grading_criteria',
    'betygskriterier': 'grading_criteria',
    'teaching staff': 'teaching_staff',
    'lärare': 'teaching_staff',
    'research connection': 'research_connection',
    'forskningsanknytning': 'research_connection',
}

# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def preprocess_study_guide(qmd_content: str, config: dict, config_dir: str = '.') -> str:
    """Transform a minimal QMD into a styled dual-format QMD.

    Args:
        qmd_content: Raw QMD file content (frontmatter + markdown body).
        config: Dictionary from config.toml with course metadata.
        config_dir: Directory containing config.toml (for resolving branding.css path).

    Returns:
        Processed QMD string ready for Quarto rendering.
    """
    frontmatter, body = _split_frontmatter(qmd_content)

    # Check opt-in flag
    fm_meta = _parse_yaml_lightweight(frontmatter)
    canvas_meta = fm_meta.get('canvas', {})
    if not canvas_meta.get('preprocess'):
        return qmd_content

    lang = config.get('language', 'english')
    labels = LABELS.get(lang, LABELS['english'])

    # Load branding
    branding = _load_branding(config, config_dir)

    sections = _parse_sections(body)

    parts = []

    # 1. Frontmatter (unchanged)
    parts.append(f"---\n{frontmatter}\n---\n")

    # 2. CSS injection from branding
    parts.append(branding['css_block'])
    parts.append("")

    # 3. PDF front page
    parts.append(_generate_front_page(config, labels, branding['colors']))
    parts.append("")

    # 4. HTML syllabus link
    course_code = config.get('course_code', '')
    if course_code:
        parts.append(_generate_syllabus_link(course_code, labels))
        parts.append("")

    # 5. Process each section
    for heading, content in sections:
        heading_lower = heading.lower().strip()
        # Strip Quarto attributes like {#sec-project} for matching
        heading_clean = re.sub(r'\s*\{[^}]*\}', '', heading_lower)

        section_key = SECTION_ALIASES.get(heading_clean)

        if section_key == 'grading_criteria':
            parts.append(f"# {heading}\n")
            parts.append(_process_grading_criteria(content))
        elif section_key == 'teaching_staff':
            parts.append(f"# {heading}\n")
            parts.append(_process_teaching_staff(content))
        elif section_key == 'research_connection':
            parts.append(f"# {heading}\n")
            parts.append(_process_research_connection(content))
        else:
            parts.append(f"# {heading}\n")
            parts.append(_process_generic_section(content))

        parts.append("")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Frontmatter handling
# ---------------------------------------------------------------------------

def _split_frontmatter(qmd_content: str):
    """Split QMD into frontmatter string and body string."""
    content = qmd_content.strip()
    if not content.startswith('---'):
        return '', content

    end = content.find('---', 3)
    if end == -1:
        return '', content

    fm = content[3:end].strip()
    body = content[end + 3:].strip()
    return fm, body


def _parse_yaml_lightweight(fm_text: str) -> dict:
    """Minimal YAML parser — just enough to read canvas.preprocess flag."""
    result = {}
    canvas = {}
    in_canvas = False
    for line in fm_text.split('\n'):
        stripped = line.strip()
        if stripped.startswith('canvas:'):
            in_canvas = True
            continue
        if in_canvas:
            if line.startswith('  ') and ':' in stripped:
                key, _, val = stripped.partition(':')
                val = val.strip().strip('"').strip("'")
                if val.lower() == 'true':
                    val = True
                elif val.lower() == 'false':
                    val = False
                canvas[key.strip()] = val
            elif not line.startswith(' '):
                in_canvas = False
        if not line.startswith(' ') and ':' in stripped and not stripped.startswith('canvas'):
            key, _, val = stripped.partition(':')
            val = val.strip().strip('"').strip("'")
            result[key.strip()] = val

    if canvas:
        result['canvas'] = canvas
    return result


# ---------------------------------------------------------------------------
# Section parsing
# ---------------------------------------------------------------------------

def _parse_sections(body: str):
    """Split body into list of (heading, content) tuples by H1 headings."""
    sections = []
    lines = body.split('\n')
    current_heading = None
    current_lines = []

    for line in lines:
        m = re.match(r'^# (.+)$', line)
        if m:
            if current_heading is not None:
                sections.append((current_heading, '\n'.join(current_lines).strip()))
            current_heading = m.group(1).strip()
            current_lines = []
        else:
            if current_heading is not None:
                current_lines.append(line)

    if current_heading is not None:
        sections.append((current_heading, '\n'.join(current_lines).strip()))

    return sections


# ---------------------------------------------------------------------------
# Image extraction
# ---------------------------------------------------------------------------

def _find_first_image(content: str):
    """Find first markdown image in content. Returns (alt, path) or None."""
    m = re.search(r'!\[([^\]]*)\]\(([^)]+)\)', content)
    if m:
        return (m.group(1), m.group(2))
    return None


# ---------------------------------------------------------------------------
# Front page generation
# ---------------------------------------------------------------------------

def _generate_front_page(config: dict, labels: dict, colors: dict = None) -> str:
    """Generate the PDF-only front page from config values."""
    course_name = config.get('course_name', 'Course')
    course_code = config.get('course_code', '')
    credits = config.get('credits', '')
    semester = config.get('semester', '')
    syllabus_url = f"https://kursinfoweb.hj.se/course_syllabuses/{course_code}.pdf" if course_code else ''

    image_block = "\n\\vspace{2cm}\n"

    info_rows = []
    if course_code:
        info_rows.append(f"{labels['course_code']}: & {course_code} \\\\")
    if credits:
        info_rows.append(f"{labels['credits']}: & {credits} \\\\")
    if semester:
        info_rows.append(f"{labels['semester']}: & {semester} \\\\")
    if syllabus_url:
        info_rows.append(f"{labels['syllabus']}: & \\href{{{syllabus_url}}}{{{course_code}}} \\\\")

    info_table = "\n".join(info_rows)

    return f"""::: {{.content-visible when-format="pdf"}}

\\thispagestyle{{empty}}

```{{=latex}}
\\vspace{{0.5cm}}
{{\\Huge\\bfseries\\color{{brand-primary}} {labels['course_pm']}}}

\\vspace{{0.5cm}}

{{\\LARGE {course_name}}}

\\vspace{{1cm}}
```
{image_block}
```{{=latex}}
\\begin{{tabular}}{{@{{}}ll@{{}}}}
{info_table}
\\end{{tabular}}

\\vfill

{{\\footnotesize \\textcolor{{brand-primary}}{{\\textbf{{BLANK}}}}-JTH-10-002-\\textcolor{{brand-primary}}{{\\textbf{{C}}}}-E verB \\hfill \\LaTeX}}

\\newpage
```

:::"""


# ---------------------------------------------------------------------------
# Syllabus link (HTML-only)
# ---------------------------------------------------------------------------

def _generate_syllabus_link(course_code: str, labels: dict) -> str:
    url = f"https://kursinfoweb.hj.se/course_syllabuses/{course_code}.pdf"
    text = labels['syllabus_link_text']
    return f"""::: {{.content-visible when-format="html"}}

```{{=html}}
<p><a href="{url}" target="_blank">{text}</a></p>
```

:::"""


# ---------------------------------------------------------------------------
# Pipe table parsing
# ---------------------------------------------------------------------------

def _find_pipe_tables(text: str):
    """Find all pipe tables in text.

    Returns list of (start_line, end_line, headers, rows, footnotes).
    """
    lines = text.split('\n')
    tables = []
    i = 0

    while i < len(lines):
        line = lines[i].strip()
        # Detect table start: line with pipes and next line is separator
        if '|' in line and i + 1 < len(lines):
            sep_line = lines[i + 1].strip()
            if re.match(r'^\|[\s:|-]+\|$', sep_line):
                start = i
                headers = _parse_table_row(lines[i])

                # Skip separator
                i += 2
                rows = []
                while i < len(lines) and lines[i].strip().startswith('|'):
                    rows.append(_parse_table_row(lines[i]))
                    i += 1

                # Collect footnotes (lines starting with ^ immediately after table)
                footnotes_lines = []
                while i < len(lines) and re.match(r'^\^', lines[i].strip()):
                    footnotes_lines.append(lines[i])
                    i += 1

                footnotes = '\n'.join(footnotes_lines) if footnotes_lines else ''
                tables.append((start, i, headers, rows, footnotes))
                continue
        i += 1

    return tables


def _parse_table_row(line: str) -> list:
    """Parse a pipe table row into a list of cell strings."""
    line = line.strip()
    if line.startswith('|'):
        line = line[1:]
    if line.endswith('|'):
        line = line[:-1]
    return [cell.strip() for cell in line.split('|')]


# ---------------------------------------------------------------------------
# LaTeX conversion
# ---------------------------------------------------------------------------

def _latex_escape(text: str) -> str:
    """Escape special LaTeX characters in text, preserving markdown formatting."""
    # Don't escape if it looks like it's already LaTeX
    if '\\begin{' in text or '\\end{' in text:
        return text

    text = text.replace('&', '\\&')
    text = text.replace('%', '\\%')
    text = text.replace('#', '\\#')
    # Don't escape _ in URLs
    text = re.sub(r'(?<!\\)_(?![a-zA-Z]*://)', '\\_', text)
    return text


def _markdown_to_latex_inline(text: str) -> str:
    """Convert inline markdown formatting to LaTeX."""
    # Convert <br> / <br/> / <br /> to LaTeX newline before escaping
    text = re.sub(r'<br\s*/?>', r'\\newline ', text)
    text = _latex_escape(text)
    # Bold: **text** → \textbf{text}
    text = re.sub(r'\*\*([^*]+)\*\*', r'\\textbf{\1}', text)
    # Italic: *text* → \textit{text}
    text = re.sub(r'\*([^*]+)\*', r'\\textit{\1}', text)
    # Superscript: ^N^ → \textsuperscript{N}
    text = re.sub(r'\^(\w+)\^', r'\\textsuperscript{\1}', text)
    # Links: [text](url) → \href{url}{text}
    text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'\\href{\2}{\1}', text)
    # Bare URLs: <url> → \url{url}
    text = re.sub(r'<(https?://[^>]+)>', r'\\url{\1}', text)
    return text


def _table_to_latex(headers: list, rows: list, col_widths=None) -> str:
    """Convert parsed table to LaTeX longtable."""
    n_cols = len(headers)
    if col_widths is None:
        w = round(0.9 / n_cols, 2)
        col_widths = [w] * n_cols

    col_spec = '|'.join(f'p{{{w}\\textwidth}}' for w in col_widths)
    col_spec = f'|{col_spec}|'

    header_cells = ' & '.join(f'\\textbf{{{_markdown_to_latex_inline(h)}}}' for h in headers)

    row_lines = []
    for i, row in enumerate(rows):
        cells = [_markdown_to_latex_inline(c) for c in row]
        # Pad or trim to match header count
        while len(cells) < n_cols:
            cells.append('')
        row_line = ' & '.join(cells[:n_cols])
        color = '\\rowcolor{lightgray}\n' if i % 2 == 0 else '\\rowcolor{white}\n'
        row_lines.append(f"{color}{row_line} \\\\\n\\hline")

    rows_block = '\n\n'.join(row_lines)

    return f"""```{{=latex}}
\\rowcolors{{2}}{{lightgray}}{{white}}
\\begin{{small}}
\\begin{{longtable}}{{{col_spec}}}
\\hline
\\rowcolor{{white}}
{header_cells} \\\\
\\hline
\\endfirsthead
\\hline
\\rowcolor{{white}}
{header_cells} \\\\
\\hline
\\endhead

{rows_block}

\\end{{longtable}}
\\end{{small}}
```"""


# ---------------------------------------------------------------------------
# Generic section processing
# ---------------------------------------------------------------------------

def _process_generic_section(content: str) -> str:
    """Process a generic section: dual-format any pipe tables found."""
    tables = _find_pipe_tables(content)
    if not tables:
        return content

    lines = content.split('\n')
    result = []
    last_end = 0

    for start, end, headers, rows, footnotes in tables:
        # Text before the table (shared)
        before = '\n'.join(lines[last_end:start]).strip()
        if before:
            result.append(before)
            result.append("")

        # Original markdown table for HTML
        md_table_lines = lines[start:end]
        # Remove footnote lines from md_table_lines range since we handle them separately
        md_table = '\n'.join(md_table_lines).strip()

        result.append('::: {.content-visible when-format="html"}')
        result.append("")
        result.append(md_table)
        if footnotes:
            result.append(footnotes)
        result.append("")
        result.append(":::")
        result.append("")

        # LaTeX table for PDF
        result.append('::: {.content-visible when-format="pdf"}')
        result.append("")
        result.append(_table_to_latex(headers, rows))
        if footnotes:
            # Convert footnotes to LaTeX
            latex_fn = footnotes
            latex_fn = re.sub(r'\^(\w+)\^', r'\\textsuperscript{\1}', latex_fn)
            result.append(f"\n```{{=latex}}\n{{\\footnotesize {_latex_escape(latex_fn)}}}\n```")
        result.append("")
        result.append(":::")
        result.append("")

        last_end = end

    # Text after the last table
    after = '\n'.join(lines[last_end:]).strip()
    if after:
        result.append(after)

    return '\n'.join(result)


# ---------------------------------------------------------------------------
# Grading Criteria (special)
# ---------------------------------------------------------------------------

def _process_grading_criteria(content: str) -> str:
    """Convert grading criteria table to <details> cards (HTML) + longtable (PDF)."""
    tables = _find_pipe_tables(content)
    if not tables:
        return content

    lines = content.split('\n')
    result = []

    start, end, headers, rows, footnotes = tables[0]

    # Text before the table (shared)
    before = '\n'.join(lines[:start]).strip()
    if before:
        result.append(before)
        result.append("")

    # HTML: collapsible details cards (raw HTML, no fenced block — Quarto passes it through in divs)
    result.append('::: {.content-visible when-format="html"}')

    for row in rows:
        ilo = row[0] if len(row) > 0 else ''
        fail = row[1] if len(row) > 1 else ''
        g3 = row[2] if len(row) > 2 else ''
        g4 = row[3] if len(row) > 3 else ''
        g5 = row[4] if len(row) > 4 else ''

        result.append(f"""
<details style="border: 1px solid #ddd; border-radius: 8px; padding: 10px; margin-bottom: 10px; background-color: #f9f9f9;">
<summary style="cursor: pointer; font-weight: bold; color: var(--ic-brand-font-color-dark);">{ilo}</summary>
<p><strong style="color: {GRADE_COLORS['Fail']};">Fail:</strong> {fail}</p>
<p><strong style="color: {GRADE_COLORS['3']};">3:</strong> {g3}</p>
<p><strong style="color: {GRADE_COLORS['4']};">4:</strong> {g4}</p>
<p><strong style="color: {GRADE_COLORS['5']};">5:</strong> {g5}</p>
</details>""")

    result.append("")
    result.append(":::")
    result.append("")

    # PDF: longtable
    col_widths = [0.18, 0.18, 0.18, 0.18, 0.18]
    result.append('::: {.content-visible when-format="pdf"}')
    result.append("")
    result.append(_table_to_latex(headers, rows, col_widths))
    result.append("")
    result.append(":::")

    # Text after the table
    after = '\n'.join(lines[end:]).strip()
    if after:
        result.append("")
        result.append(after)

    return '\n'.join(result)


# ---------------------------------------------------------------------------
# Teaching Staff (special)
# ---------------------------------------------------------------------------

def _process_teaching_staff(content: str) -> str:
    """Convert teaching staff table to photo cards (HTML) + simple table (PDF)."""
    tables = _find_pipe_tables(content)
    if not tables:
        return content

    lines = content.split('\n')
    result = []

    start, end, headers, rows, _ = tables[0]

    # Text before the table (shared)
    before = '\n'.join(lines[:start]).strip()
    if before:
        result.append(before)
        result.append("")

    # PDF: simple table (Name, Role, Contact)
    result.append('::: {.content-visible when-format="pdf"}')
    result.append("")

    pdf_headers = ['Name', 'Role', 'Contact']
    pdf_rows = []
    for row in rows:
        name = row[0] if len(row) > 0 else ''
        role = row[1] if len(row) > 1 else ''
        pdf_rows.append([name, role, 'Canvas/Teams'])

    result.append(_table_to_latex(pdf_headers, pdf_rows, [0.30, 0.30, 0.30]))
    result.append("")
    result.append(":::")
    result.append("")

    # HTML: photo cards (raw HTML, no fenced block)
    result.append('::: {.content-visible when-format="html"}')

    for row in rows:
        name = row[0] if len(row) > 0 else ''
        role = row[1] if len(row) > 1 else ''
        image = row[2] if len(row) > 2 else ''
        link = row[3] if len(row) > 3 else ''

        if link:
            name_html = f'<a class="inline_disabled" href="{link}" target="_blank" rel="noopener">{name}</a>'
        else:
            name_html = name

        result.append(f"""
<div style="clear: both; overflow: hidden; margin-bottom: 20px;">
<img style="float: left; margin-right: 15px;" role="presentation" src="{image}" alt="" width="136" height="164" />
<p><span style="font-size: 1.5em; color: var(--ic-brand-font-color-dark);">{name_html}</span><br>{role}</p>
</div>""")

    result.append("")
    result.append(":::")

    # Text after the table
    after = '\n'.join(lines[end:]).strip()
    if after:
        result.append("")
        result.append(after)

    return '\n'.join(result)


# ---------------------------------------------------------------------------
# Research Connection (special)
# ---------------------------------------------------------------------------

def _process_research_connection(content: str) -> str:
    """Wrap research connection in collapsible <details> for HTML."""
    return f"""::: {{.content-visible when-format="html"}}

```{{=html}}
<details style="margin-bottom: 10px;">
<summary style="cursor: pointer; font-weight: bold;">Research Connection</summary>
```

:::

{content}

::: {{.content-visible when-format="html"}}

```{{=html}}
</details>
```

:::"""


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    import argparse
    import tomllib

    parser = argparse.ArgumentParser(description='Preprocess a study guide QMD.')
    parser.add_argument('input', help='Input QMD file')
    parser.add_argument('--config', default='config.toml', help='Path to config.toml')
    parser.add_argument('-o', '--output', help='Output file (default: stdout)')
    args = parser.parse_args()

    # Load config
    config_path = args.config
    if not os.path.isabs(config_path):
        config_path = os.path.join(os.path.dirname(args.input) or '.', config_path)

    config = {}
    if os.path.exists(config_path):
        with open(config_path, 'rb') as f:
            config = tomllib.load(f)
    else:
        print(f"Warning: config.toml not found at {config_path}", file=sys.stderr)

    # Read input
    with open(args.input, 'r', encoding='utf-8') as f:
        qmd_content = f.read()

    # Process
    result = preprocess_study_guide(qmd_content, config, config_dir=os.path.dirname(config_path) or '.')

    # Output
    if args.output:
        with open(args.output, 'w', encoding='utf-8') as f:
            f.write(result)
        print(f"Written to {args.output}", file=sys.stderr)
    else:
        print(result)
