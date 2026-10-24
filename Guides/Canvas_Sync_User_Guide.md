# Canvas Sync System User Guide

## Table of Contents

- [1. Getting Started](#1-getting-started)
  - [Prerequisites](#prerequisites)
  - [Configuration](#configuration)
  - [Dates and time zones](#dates-and-time-zones)
  - [Cross-references](#cross-references)
  - [Table styling](#table-styling)
  - [Usage](#usage)
- [2. File Organization & Module Order](#2-file-organization--module-order)
  - [Order files](#order-files-_modulesyml-_orderyml)
  - [NN_ prefixes (older courses)](#nn_-prefixes-older-courses)
  - [Converting an NN_ course](#converting-an-nn_-course)
- [3. Content Types & Metadata](#3-content-types--metadata)
  - [Quarto Pages (.qmd)](#quarto-pages-qmd)
  - [Study Guides (.qmd)](#study-guides-qmd)
  - [Quarto Assignments (.qmd)](#quarto-assignments-qmd)
  - [Text Headers (.qmd)](#text-headers-qmd)
  - [External Links (.qmd)](#external-links-qmd)
  - [Quizzes (.json)](#quizzes-json)
  - [QMD Quizzes (.qmd)](#qmd-quizzes-qmd)
  - [New Quizzes (.qmd and .json)](#new-quizzes-qmd-and-json)
  - [Solo Files (PDFs, ZIPs, etc.)](#solo-files-pdfs-zips-etc)
- [4. Calendar Synchronization](#4-calendar-synchronization)
- [5. Linking & Asset Handling (Power Feature)](#5-linking--asset-handling-power-feature)
  - [A. Local Files (Downloads)](#a-local-files-downloads)
  - [B. Images](#b-images)
  - [C. Cross-Linking (Smart Navigation)](#c-cross-linking-smart-navigation)
  - [D. Asset Namespacing & Optimization](#d-asset-namespacing--optimization)
  - [E. Orphan Asset Cleanup (Pruning)](#e-orphan-asset-cleanup-pruning)
- [6. Three Ways to Run the Tool](#6-three-ways-to-run-the-tool)
  - [Usage](#usage-1)
- [7. Synchronization Strategy & Tracking](#7-synchronization-strategy--tracking)
  - [The Sync Map (.canvas_sync_map.json)](#the-sync-map-canvas_sync_mapjson)
  - [When someone edits Canvas directly](#when-someone-edits-canvas-directly)
  - [Team mode: several teachers, one course](#team-mode-several-teachers-one-course)
- [8. Authoring with an AI Assistant](#8-authoring-with-an-ai-assistant)
  - [Setting up a content folder](#setting-up-a-content-folder)
  - [The workflow](#the-workflow)
  - [Checking content yourself](#checking-content-yourself)
  - [Keeping the kit up to date](#keeping-the-kit-up-to-date)

This system automates the synchronization of local course content to a Canvas course. It supports pages, assignments, quizzes, module headers, and calendar events.

## 1. Getting Started

### Prerequisites
1.  **Python 3.8+**
2.  **Quarto CLI**: Must be installed and available in your system PATH.
3.  **Python Packages**:
    ```bash
    pip install canvasapi python-frontmatter PyYAML asteval rich
    ```
4.  **Environment Variables**:
    *   `CANVAS_API_URL` (e.g., `https://canvas.instructure.com`)
    *   `CANVAS_API_TOKEN` (Your generated API Access Token)

### Configuration
The **Course ID** is set in `config.toml` in the content folder:

```toml
course_id = 12345
```

`--course-id 12345` on the command line overrides it for one run.

A `course_id.txt` holding only the id still works but is **deprecated**: the
sync and `check_content` warn about it, and support will be removed in a future
version. Move the id into `config.toml` and delete the file.

### Dates and time zones

Write the time students should see, with no `Z` and no offset:

```yaml
due_at: "2026-11-17T09:00:00"     # 09:00 course-local, whatever the season
```

Such a time is read as **course-local wall clock** and converted to the correct UTC
instant at sync time, so daylight saving is handled for you. This is the recommended
form: a fixed `09:00Z` displays as 11:00 local in summer but 10:00 once the clocks go
back, so a weekly deadline silently drifts an hour mid-semester. A hardcoded `+02:00`
is worse — it is simply wrong for the half of the year Sweden is on `+01:00`.

Values that **do** carry a `Z` or an offset are passed through untouched and keep
meaning exactly the instant they state, so existing content is unaffected.

The zone is taken from `config.toml` if set, otherwise from the Canvas course's own
timezone setting:

```toml
timezone = "Europe/Stockholm"     # optional; IANA name
```

Setting it locally is worthwhile if you want the meaning of your files pinned in the
repo rather than depending on a Canvas setting someone could change. If both are set
and they disagree, the sync warns: times are still stored as the correct instant, but
Canvas *displays* them in the course's zone, so students would see a different clock
time than your files state.

`validate_content.py` warns about the two local times daylight saving makes strange —
an hour that never happens (clocks jump forward) and one that happens twice (clocks go
back). Both still sync; move the time by an hour if the warning matters.

> On Windows the `tzdata` package is required (it is in `requirements.txt`), because
> Windows ships no system timezone database.

### Cross-references

Quarto's `@fig-x`, `@tbl-x`, `@eq-x` and `@sec-x` references work on Canvas: the
text "Figure 1" and the caption are plain HTML by the time the page is uploaded.
An unresolved reference is a different story. Quarto renders it as literal
`?@fig-x`, prints a warning and exits 0, so the sync reads that warning back and
reports it:

```
Unresolved cross-reference(s): @fig-geometry, @fig-load - students will see '?@...' in Canvas.
```

The page is still uploaded. To block it instead, so Canvas keeps the previous
version until the reference is fixed, set `strict_crossrefs = true` in
`config.toml`. The offline validator (`check_content`) flags the same problems
before a sync, including the most common cause: images on adjacent lines with no
blank line between them, which are inline images to Quarto, not figures.

### Table styling

Out of the box a table looks the way Canvas draws it. Quarto turns the caption
options `: {.striped .hover .bordered}` into class names that Canvas's own legacy
stylesheet happens to style, with stripes so faint they look like nothing, and
colours nobody can change. A course can have the sync draw its tables instead. The
sync writes the styling onto each table it draws, so a setting on one table never
reaches another.

The settings form a hierarchy. Each level overrides the ones above it, for the tables
it reaches:

| Level | Set in | Reaches |
|:--|:--|:--|
| Canvas | nothing set | every table, exactly as before |
| Course | `[tables]` in `config.toml`, or `--table-*` on `:root` in `branding.css` | every table in the course, except `.plain` ones |
| Named style | a class defined in `branding.css` | the tables that carry that class |
| One table | a `.cqs-table` wrapper | the table inside it |

**Course.** Either file turns course styling on. `[tables] style` lists the options
for every table whose caption line names none of its own; a table that names any gets
exactly what it names:

```toml
[tables]
style = ["striped", "hover"]
```

The colours go on `:root` in `branding.css`, and may use the brand colours there:

```css
:root {
  --table-stripe: #f0f4f7;
  --table-header-bg: var(--brand-heading);
  --table-header-fg: #ffffff;
}
```

**Named style.** A class whose rule sets `--table-*` values. Add it to a table's
caption line and only that table changes:

```css
.schema { --table-stripe: #eef3f8; --table-border: #999999; }
```

```markdown
: {.striped .bordered .schema}
```

**One table.** Quarto drops `key=value` settings on a table, so values for a single
table go on a wrapper around it. The sync removes the wrapper and keeps the table:

```markdown
::: {.cqs-table stripe="#eef3f8" border="#999999"}

| Vecka | Ämne |
|---|---|
| 44 | Kursintro |

: {.striped .bordered}

:::
```

**Opting out.** `: {.plain}` leaves a table as Canvas draws it, whatever the course
sets.

Options, on a table's caption line or in `[tables] style`:

| Option | In `[tables] style` | What it does |
|:--|:--|:--|
| `.striped` | `"striped"` | a background on the 1st, 3rd, 5th... body row |
| `.bordered` | `"bordered"` | lines around every cell |
| `.sm` | `"sm"` | tighter cell padding |
| `.hover` | `"hover"` | passed on to Canvas, which shows no visible highlight (see below) |

Values, in `branding.css` or on a wrapper:

| In `branding.css` | On a wrapper | What it sets | When unset |
|:--|:--|:--|:--|
| `--table-stripe` | `stripe=` | background of the striped rows | `#f0f4f7` |
| `--table-border` | `border=` | colour of the lines between rows, and around cells | Canvas's lines; `#dddddd` for `.bordered` |
| `--table-header-bg` | `header-bg=` | background of the header row | Canvas's |
| `--table-header-fg` | `header-fg=` | text colour of the header row | Canvas's |

A value is a colour: `#003865`, `rgb(0, 56, 101)`, `white`, or `var(--brand-heading)`
for a property on `:root`.

- Header colours and a line colour set for the course reach every table's header row
  and lines. Stripes, grid lines and tight padding appear only on tables that ask for
  them, through their caption line or `[tables] style`.
- A table written as raw HTML in a `.qmd` is left alone by course settings. Give it a
  named style or a wrapper to style it.
- The sync cannot draw a hover effect: it needs `:hover`, which only a stylesheet can
  express, and Canvas removes stylesheets from pages. `.hover` is passed on to Canvas
  as the class `table-hover`, but Canvas's own hover colour for it is a near-white
  grey (`#F2F4F4`), and in testing it showed no visible highlight at all.
- A study guide's Canvas page follows these settings; its PDF does not.
- The `table th` and `tr:nth-child` rules in older `branding.css` files are not table
  settings. They only ever reached study guides, and they still mean what they meant.
- Changing `config.toml` or `branding.css` re-publishes every page on the next sync,
  so a new colour reaches all of them.
- `check_content` (`cqs check`) reports what the sync would ignore: an option it does
  not know, a value that is not a colour, a wrapper with no table inside.

### Usage
Run the script from the root of your project:

```powershell
# Default: Sync content from current directory
python sync_to_canvas.py

# Sync from a specific folder
python sync_to_canvas.py ../MyCourseData

# Sync a SINGLE asset (one page/assignment/quiz/file) instead of the whole course.
# The item is created/updated and placed in the correct slot of its module,
# and recorded in the sync map so a later full sync recognizes it.
python sync_to_canvas.py --only 01_Introduction/02_Welcome.qmd

# Sync including Calendar (Opt-in)
python sync_to_canvas.py --sync-calendar

# Verbose output (shows debug details with timestamps)
python sync_to_canvas.py --verbose

# Quiet mode (only show errors)
python sync_to_canvas.py --quiet

# Save full debug log to a file
python sync_to_canvas.py --log-file sync.log
```

---

## 2. File Organization & Module Order

The course folder says which modules exist, in what order, and which files are in each.
There are two ways to say it. New courses use **order files**; courses made before them
use **`NN_` prefixes** until their owner converts them (see *Converting* below). The
Course Board in VS Code works with both, and lets you reorder an order-file course by
dragging.

### Order files (`_modules.yml`, `_order.yml`)

```text
Course/
├── _modules.yml            -> the modules, top to bottom
├── Introduction/           -> Module "Introduction"
│   ├── _order.yml          -> its items, top to bottom (optional)
│   ├── Welcome.qmd         -> Page
│   ├── Syllabus.pdf        -> File item, because _order.yml lists it
│   └── notes.md            -> not synced: no canvas.type and not listed
├── Python Basics/          -> Module "Python basics and tools" (name set in _modules.yml)
│   └── FirstProg.qmd       -> Assignment
├── Hidden.qmd              -> Page outside any module (listed in _modules.yml)
└── graphics/               -> not a module: not listed
```

`_modules.yml`:

```yaml
- Introduction
- Python Basics: Python basics and tools    # folder: name in Canvas
- Hidden.qmd                                # a file: synced outside any module
```

`Introduction/_order.yml`:

```yaml
- Welcome.qmd
- Syllabus.pdf
```

*   **Modules**: a folder is a module only if `_modules.yml` lists it, in that order. The
    module takes the folder's name, or the name after the colon.
*   **Items**: a file in a module folder is included if `_order.yml` lists it, or if it
    declares itself as content: a `canvas.type`, or a quiz. Listed files come first, in
    order; content that is not listed follows, sorted by name. A new page therefore needs
    no bookkeeping: it appears at the end of its module.
*   **Other files** (PDFs, notes, images) are synced only when `_order.yml` lists them.
*   **Names starting with `_`** are kept out unless listed. Use them for drafts.
*   **Titles** come from the frontmatter `title:`, or the filename. File and folder names
    are free: no prefix, and renaming a file never changes the order.
*   **Modules are placed in Canvas in this order.** Modules that exist only in Canvas keep
    their place relative to the others.
*   **Moving a file to another module folder** moves its item in Canvas on the next sync:
    it is added to the new module and taken out of the old one.

**Several teachers.** Both files are one entry per line, so git merges them line by line,
and the course's `.gitattributes` marks them `merge=union`, so a merge never stops on
them. Reading is tolerant: a duplicate line keeps its first position, an entry whose file
is gone is skipped (with a warning from `cqs check`), and a file nobody listed is added
at the end. If two people reorder the same module at the same time, one order wins and
the Course Board shows it; drag to fix it.

### `NN_` prefixes (older courses)

A course without `_modules.yml` uses the original rule.

*   **Modules**: folders named `NN_Name` (two digits, underscore, name), for example
    `01_Introduction`. The prefix sets the order and is removed from the Canvas name.
    **Folders not matching this pattern are ignored.**
*   **Files**: `NN_Name.ext`, for example `01_Welcome.qmd`. In a module folder the file is
    added to that module; at the root it is synced but **not added to any module**. The
    prefix is stripped from titles (`01_Intro.pdf` becomes "Intro.pdf"). **Files not
    matching this pattern are ignored.**

```text
DailyWork/
├── 01_Introduction/        -> Module: "Introduction"
│   ├── 01_Welcome.qmd      -> Page (In Module)
│   └── 03_Resources.md     -> SubHeader
├── 02_Python Basics/       -> Module: "Python Basics"
│   └── 01_FirstProg.qmd    -> Assignment (In Module)
├── 99_HiddenPage.qmd       -> Page (Synced, but NOT in any module)
├── graphics/               -> Ignored (no prefix)
└── handlers/               -> Ignored (no prefix)
```

### Converting an `NN_` course

The Course Board offers it when it opens an `NN_` course (*Preview conversion*), and
`cqs migrate` does the same from a terminal. Start with a preview:

```
cqs migrate --dry-run
```

The conversion is local: nothing in Canvas changes, and the next sync shows the same
module names and item titles. It

*   removes the prefix from each module folder and the files directly in it (a name that
    is already taken keeps its prefix);
*   writes `_modules.yml` and one `_order.yml` per module, in the current order;
*   updates links and paths that point at a renamed file, in every text file of the
    course (`.qmd`, `.md`, `.yml`, `.json`, ...);
*   moves the sync map entries and drift snapshots along, so every Canvas id is kept;
*   gives a leading `_` to a file that was ignored for lacking a prefix but has a
    `canvas.type`, so it stays out of Canvas;
*   adds the `merge=union` lines to `.gitattributes`.

It refuses to run with uncommitted changes, so the conversion is one commit of its own
(`--allow-dirty` overrides). In a team course, have everyone commit and push first, and
pull afterwards.

---

## 3. Content Types & Metadata

> [!IMPORTANT]
> **Safe Updates**: When the system syncs, it checks if an item with the same title or internal ID already exists. 
> *   **If Found**: It **updates** the existing item (description, points, dates, etc.). This ensures student submissions and grades are **preserved**.
> *   **Dynamic Renaming**: If you change the `title` in your frontmatter (or JSON), the system will update the title of the existing Page/Assignment in Canvas. The link within the Canvas Module will also be updated to match the new title automatically.
> *   **File Rename**: Renaming the physical file (e.g., `Intro.qmd` -> `Introduction.qmd`) while keeping the same `title` in the frontmatter is perfectly safe and will not create a duplicate. In a course with order files, rename it in `_order.yml` too if it is listed there.

### Quarto Pages (`.qmd`)
*   **Locality**: Place in a module folder.
*   **Metadata**:
    ```yaml
    ---
    title: "Page Title"
    format:
      html:
        page-layout: article # Recommended
    canvas:
      type: page
      published: true      # (optional, Default: false)
      indent: 0            # (optional, 0-5)
      front_page: false    # (optional, Default: false) — see below
    ---
    ```

*   **`front_page: true`** makes this page the course home page and switches the course
    to display it. Only one page should set this.

> [!NOTE]
> Canvas does not allow the front page to be unpublished. If a page is the course front
> page, `published: false` is rejected and the sync updates the title and body only,
> leaving the published state untouched.

### Study Guides (`.qmd`)

A study guide is rendered to **both** a Canvas page (HTML) and a downloadable PDF, synced from a single `.qmd` file.

There are two modes: **preprocessed** (recommended) and **manual**.

> [!WARNING]
> **Filename detection.** Any `.qmd` whose filename contains `studyguide` or `kurspm`
> (case-insensitive) is treated as a study guide **even without** `canvas.type:
> study_guide`. Avoid those words in ordinary page filenames, or the sync will try to
> render a PDF from them.

#### Preprocessed Mode (`preprocess: true`)

Write a **minimal QMD with only plain markdown** — no LaTeX, no raw HTML, no `content-visible` blocks. The `qmd_preprocessor.py` automatically generates:

*   **PDF front page** from `config.toml` (course name, code, credits, semester) with JU brand colors
*   **Syllabus link** (HTML-only) derived from the course code
*   **JU Canvas CSS** injection (brand colors for headings, links, tables)
*   **Dual-format tables**: markdown tables for HTML, styled LaTeX longtables for PDF
*   **Grading criteria**: a 5-column table (ILO/Fail/3/4/5) becomes collapsible `<details>` cards in HTML
*   **Teaching staff**: a 4-column table (Name/Role/Image/Link) becomes photo cards in HTML, plain table in PDF
*   **Research connection**: wrapped in a collapsible `<details>` for HTML

**Metadata** (this is all you need in the frontmatter):
    ```yaml
    ---
    title: "Course PM — Course Name (CODE)"
    canvas:
      type: study_guide
      preprocess: true
      published: true
      pdf:
        target_module: "Course Documents"
        filename: "KursPM.pdf"
        title: "Course PM (PDF)"
        published: true
    ---
    ```

**Required `config.toml` fields:**
    ```toml
    course_name = "Mechatronics"
    course_code = "TMRK16"
    credits = "7.5 ECTS"
    semester = "Spring 2026"
    language = "english"       # or "swedish"
    ```

**Required `_quarto.yml`** in the project root — provides LaTeX packages and JU color definitions for PDF rendering. See [Example/_quarto.yml](../Example/_quarto.yml).

**Special sections** (detected by `# Heading` name):

| Section heading | Transformation |
|:----------------|:---------------|
| `# Grading Criteria` | Table → collapsible cards (HTML) + longtable (PDF) |
| `# Teaching Staff` | Table → photo cards (HTML) + simple table (PDF) |
| `# Research Connection` | Content → collapsible `<details>` (HTML) |
| Any section with a table | Table → markdown (HTML) + longtable (PDF) |

**Teaching Staff table format:**
    ```markdown
    | Name | Role | Image | Link |
    |:-----|:-----|:------|:-----|
    | Jane Doe | Course responsible | jane.png | https://example.com/jane |
    ```

**Standalone testing** (without Canvas sync):
    ```bash
    python handlers/qmd_preprocessor.py input.qmd --config config.toml -o output.qmd
    quarto render output.qmd --to html
    quarto render output.qmd --to pdf
    ```

**JU Brand Colors** — from the [official graphic manual (2023)](https://ju.se/download/18.3bf3a08918eeceec0f24e843/1714479504941/Grafisk%20manual%202023_EN_web.pdf):

| Colour       | HEX       | RGB            | Usage             |
|:-------------|:----------|:---------------|:------------------|
| **Purple**   | `#961B81` | 150, 27, 129   | Identity colour — links, accents, card borders |
| **Dark Blue**| `#003865` | 0, 56, 101     | Headings (H1/H2), table headers, summaries |
| **Turquoise**| `#55AAA7` | 85, 170, 167   | Complementary accent |
| **Yellow**   | `#FFB500` | 255, 181, 0    | Complementary accent |
| **Grey**     | `#797979` | 121, 121, 121  | Complementary accent |

#### Manual Mode (without preprocessor)

If `preprocess` is not set to `true`, you manage dual-format content yourself using `::: {.content-visible when-format="..."}` blocks, raw HTML, and LaTeX. The handler renders the QMD as-is.

*   See the [Example study guide](../Example/01_Introduction/09_Study_Guide.qmd) for a working preprocessed example.

### Quarto Assignments (`.qmd`)
*   **Metadata**:
    ```yaml
    ---
    title: "Assignment Title"
    format:
      html:
        page-layout: article
    canvas:
      type: assignment
      published: true                   # (optional)
      points: 10                       # (optional)
      due_at: "2024-10-15T23:59:00"     # (optional, ISO 8601 — course-local time)
      unlock_at: "2024-10-01T08:00:00"  # (optional)
      lock_at: "2024-10-20T23:59:00"    # (optional)
      grading_type: points              # (optional: points, percentage, pass_fail, letter_grade, gpa_scale, not_graded)
      submission_types: [online_upload] # (optional: [online_upload, online_text_entry, online_url, media_recording, student_annotation, none, external_tool])
      allowed_extensions: [py, txt]     # (optional)
      omit_from_final_grade: true       # (optional, Default: false) — do not count towards final grade
      allowed_attempts: -1              # (optional, Default: -1 = unlimited; use a positive integer for a finite cap)
      hide_in_gradebook: true           # (optional, Default: false) — no gradebook column at all; requires points 0/unset
      indent: 1                       # (optional)
      group_assignment: true            # (optional, Default: false) — group submission
      group_set: "Project Groups"       # (optional) — name of an existing Canvas group set
      rollup:                           # (optional) — grade derived from other assignments
        requires: [01_Lab.qmd, 02_Lab.qmd]
        pass_at: 1
    ---
    ```

#### Grade Rollups: One Result From Several Assignments

Some student records systems want **one** result per examination module, while
teaching wants **several** assignments that are examined separately. A Swedish
LADOK module such as "Laboratory 3 hp" is a single pass/fail column, but the
course may run five labs that students demonstrate one at a time.

A **rollup** bridges the two. Declare it in the frontmatter of the assignment
that reports the result:

```yaml
---
title: "Laboratory"
canvas:
  type: assignment
  grading_type: pass_fail
  points: 0
  submission_types: [none]
  omit_from_final_grade: true
  rollup:
    requires:
      - 01_Tensile_Test.qmd
      - 02_Beam_Solver.qmd
      - 03_Truss.qmd
    pass_at: 1
---
```

*   **`requires`** lists the assignments that must be passed. Paths are relative
    to the file declaring the rollup, the same rule as links in the body. There
    is no central registry: declare as many rollups as you like, each in its own
    target's frontmatter.
*   **`pass_at`** is the score at or above which a requirement counts. A
    `pass_fail` requirement counts on `complete` whatever its points are, and an
    excused one always counts. That matters: a `pass_fail` assignment worth 0
    points has a *score* of 0 even when the student passed, so the score alone
    would be misleading.

**A sync never applies a rollup.** Content sync is routine; writing grades is
not, so they are separate commands:

```bash
python rollup.py .                  # what is declared — no Canvas calls at all
python rollup.py . --status         # who qualifies — reads Canvas, writes nothing
python rollup.py . --apply          # mark them
```

Running it with no flags is an offline check of the declaration itself: does
every path resolve, has each requirement been synced, does the target have a
Canvas id. That is where a typo shows up, and it needs no credentials.
`check_content` reports the same path errors while you author.

Grades are only ever **raised**. A student who already holds a pass but no
longer qualifies is reported as a conflict and left untouched, and Canvas's own
"Test Student" and inactive enrolments are skipped.

**In VS Code**, the target's row in the Course Board has a
**Rollup** button, just left of its type. It shows **✓** when the offline
check passes and **⚠** when it does not; hover it for the rule and the
options. Clicking it asks Canvas who qualifies and shows the counts under the
row. The **▾** menu has *Check setup* and *Mark complete…*, which asks first
with the number and names, then marks only those students: anyone who
qualified after you confirmed waits for the next run.

**Tools → Grade Rollup** in the sidebar shows every rollup in the course on one
page, each with the same button. Opening it reads local files only: it lists
each requirement with its Canvas id, so a requirement that was never synced is
visible before anything is asked of Canvas.

#### Keeping an Assignment Out of the Gradebook

Two settings, doing different things:

*   **`omit_from_final_grade: true`** — the column stays, the score doesn't count
    towards the final grade.
*   **`hide_in_gradebook: true`** — no column at all. Canvas is strict here: the
    assignment must be worth **0 points** and be omitted from the final grade. The sync
    sets `omit_from_final_grade` for you, but it cannot get around the points rule — with
    points assigned it skips the setting, warns, and syncs the rest, leaving the
    assignment visible.

Both are source of truth: remove the key and the next sync puts things back.

> [!NOTE]
> `hide_in_gradebook` works on assignments and New Quizzes. A **classic quiz** doesn't
> take it — its points come from its questions, so it can only be worth 0 if every
> question is, which defeats the purpose. Use `quiz_type: practice_quiz` for a quiz that
> shouldn't reach the gradebook at all.

#### Group Assignments

Set `group_assignment: true` to make an assignment a group submission. Canvas needs to
know **which** group set to use:

*   **With `group_set`**: the named group set is used. It must already exist in the
    Canvas course — the sync does not create group sets.
*   **Without `group_set`**: the sync **stops and prompts you** to pick one from the
    course's existing group sets, then writes your choice back into the `.qmd`
    frontmatter so later syncs run unattended. You can also apply that choice to all
    remaining assignments in the same run.

> [!TIP]
> Always set `group_set` explicitly for unattended or scripted syncs — otherwise the
> run blocks waiting for input.

### Text Headers (`.qmd`)
*   Used to create visual separators within modules.
*   **Metadata**:
    ```yaml
    ---
    title: "Section Header"
    canvas:
      type: subheader
      published: true      # (optional)
      indent: 0            # (optional)
    ---
    ```

### External Links (`.qmd`)
*   Used to add external website links as module items.
*   **Locality**: Place in a module folder. External links in the root are ignored.
*   **Metadata**:
    ```yaml
    ---
    title: "Canvas API Documentation"
    canvas:
      type: external_url
      url: "https://canvas.instructure.com/doc/api/"
      published: true      # (optional, Default: false)
      indent: 0            # (optional, 0-5)
      new_tab: true        # (optional, Default: false) — open link in a new browser tab
    ---
    ```
*   **Note**: The body content of the QMD file is ignored — only the frontmatter is used.

### Quizzes — JSON Format (`.json`)

JSON is the **concise format** for quizzes. It supports basic question types, LaTeX math, and works with both Classic and New quiz engines. Use this format when you don't need images in answers or advanced question types like formula/numeric.

*   **LaTeX Support**: LaTeX math (e.g., `$x^2$` or `$$ \int dx $$`) is rendered through Quarto automatically.
*   **Note**: Quizzes are **unpublished** by default.

**Example:**
```json
{
  "canvas": {
    "title": "Quiz Title",
    "published": true,
    "shuffle_answers": true,
    "show_correct_answers": true,
    "allowed_attempts": 3
  },
  "questions": [
    {
      "question_name": "Q1",
      "question_text": "What is $2+2$?",
      "question_type": "multiple_choice_question",
      "points_possible": 1,
      "answers": [
        {"answer_text": "4", "weight": 100},
        {"answer_text": "5", "weight": 0}
      ]
    }
  ]
}
```

**Supported question types** (in `question_type`):
`multiple_choice_question`, `true_false_question`, `short_answer_question`, `fill_in_multiple_blanks_question`, `multiple_answers_question`, `multiple_dropdowns_question`, `matching_question`, `numerical_question`, `calculated_question`, `essay_question`, `file_upload_question`, `text_only_question`

> [!IMPORTANT] 
> **Quiz description files**: You can link a rich `.qmd` description using `"description_file": "Quiz_Description.qmd"`. It must not be a module item itself, or it will also sync as a separate page: don't give it a `canvas.type` or list it in `_order.yml`, and in an `NN_` course don't give it a prefix.

### Quizzes — QMD Format (`.qmd`)

QMD is the **full-featured format** for quizzes. It supports everything JSON does, plus:
*   **Rich content** — images, formatted text, and multi-paragraph answers
*   **Two answer styles** — simple checklists or structured div blocks
*   **Per-answer comments** — inline feedback for each answer choice
*   **Numeric questions** — student types a number, graded with tolerance (New Quizzes engine)
*   **Formula questions** — parameterized questions with randomized variables (New Quizzes engine)

The system detects a `.qmd` file as a quiz by checking for the `type: quiz` (or `type: new_quiz`) key in the `canvas` YAML frontmatter. (If omitted, it will attempt a fallback scan for `:::: {.question` blocks).

*   **Structure**: YAML frontmatter (quiz settings) + `:::: {.question}` fenced div blocks.
*   **Rendering**: All markdown content is rendered to HTML via Quarto and images are uploaded to Canvas automatically.

**Frontmatter (Classic Quiz):**
```yaml
---
canvas:
  type: quiz
  title: "Quiz Title"
  published: true
  shuffle_answers: true
  show_correct_answers: true
  allowed_attempts: -1
---
```

**Frontmatter (New Quiz):**
```yaml
---
canvas:
  type: new_quiz
  title: "Quiz Title"
  published: true
  shuffle_answers: true
  shuffle_questions: true
  points: 10
  allowed_attempts: -1        # unlimited attempts
  score_to_keep: highest
  time_limit: 1800            # 1800 seconds = 30 minutes
---
```

#### Question Block Reference

| Element | Syntax | Default |
|---|---|---|
| Question block | `:::: {.question name="..." points=N type=...}` | `points=1`, `type=multiple_choice_question` |
| Question name | `name="..."` attribute | Auto: "Fråga 1", "Fråga 2", ... |
| Simple answer ✓ | `- [x] answer text` | `answer_weight: 100` |
| Simple answer ✗ | `- [ ] answer text` | `answer_weight: 0` |
| Simple answer comment | Indented sub-item: `  - comment text` | Optional |
| Rich answer | `::: {.answer correct=true comment="..."}` | `correct=false`, no comment |
| Correct feedback | `::: correct-comment` ... `:::` | Optional |
| Incorrect feedback | `::: incorrect-comment` ... `:::` | Optional |

> [!IMPORTANT]
> Each question uses **either** checklist answers (`- [x]`/`- [ ]`) **or** div answers (`::: .answer`) — never both in the same question.
>
> - **Checklist style**: Best for short text/formula answers. Per-answer comments are indented sub-items.
> - **Div style**: Best when answers need images, multiple paragraphs, or rich formatting. Per-answer comments use the `comment="..."` attribute.

**Example — Checklist answers** (simple, short answers):
```markdown
:::: {.question name="Stress Definition"}

  Which formula describes **normal stress**?

  ![](graphics/stress_diagram.png)

  - [x] $\sigma = F/A$
    - Correct! Stress is force per area.
  - [ ] $\sigma = F \cdot A$
    - This gives the wrong units.
  - [ ] $\sigma = F + A$
  - [ ] $\sigma = F - A$

  ::: correct-comment
  Well done! Stress is defined as force per unit area.
  :::

  ::: incorrect-comment
  Think about what the unit Pa represents.
  :::

::::
```

**Example — Rich div answers** (multi-line, images in answers):
```markdown
:::: {.question name="Hooke's Law" points=2}

  What does $E$ represent in **Hooke's law**?

  ::: {.answer correct=true comment="Correct! Also known as Young's modulus."}
  **Elastic modulus** (Young's modulus) — a material constant
  that describes the material's stiffness.

  ![](graphics/e_modulus.png)
  :::

  ::: {.answer comment="No, strain is denoted by ε."}
  Strain
  :::

  ::: {.answer}
  Cross-sectional area
  :::

::::
```

> [!TIP]
> **Indentation is optional.** Content inside `:::: question` and `::: answer` blocks can be indented (e.g., 2 spaces) for readability — the parser handles both indented and non-indented content.

#### Numeric Questions (New Quizzes Engine)

Use `type="numeric_question"`. The student types a number and the answer is graded with tolerance. Define correct answers using `.answer` blocks with attributes:

*   **Exact match**: `::: {.answer value="200"}`
*   **Margin of error (Absolute)**: `::: {.answer value="200" margin="5"}` (accepts 195–205)
*   **Margin of error (Percent)**: `::: {.answer value="200" margin="2" margin_type="percent"}` (accepts 196–204)
*   **Range**: `::: {.answer start="190" end="210"}`
*   **Precise response**: `::: {.answer value="200.00" precision="2" precision_type="decimals"}`

You can provide multiple valid answers by including multiple `::: {.answer}` blocks.

#### Formula Questions — Parametric (New Quizzes Engine)

Use `type="formula_question"`. This type generates unique variable values for each student attempt based on a math formula. It requires three block types inside the question:

1.  **Variables in question text**: Use `[varname]` placeholders in the text.
    > [!WARNING]
    > Do not place variable placeholders inside LaTeX math blocks (e.g. `$[var] = 5$`) as Quarto's rendering engine may interfere with the brackets.
2.  **Formula configuration**: `::: {.formula}` block with the math expression and grading tolerance.
3.  **Variable definitions**: `::: {.variable name="varname"}` blocks with range and precision.

**Formula configuration options:**

| Key | Required | Default | Description |
|---|---|---|---|
| `formula` | yes | — | Math expression (e.g. `F * 1000 / A`). Supports `+`, `-`, `*`, `/`, `**`, parentheses, and functions like `sin`, `cos`, `sqrt`, `abs`, `pi`. |
| `margin` | no | `0` | Tolerance applied when grading the student answer |
| `margin_type` | no | `absolute` | `absolute` or `percent` |
| `answer_count` | no | `10` | How many pre-computed solution sets to generate and upload |
| `distribution` | no | `random` | `random` — uniform random sampling within each variable's range. `even` — linearly spaced values covering the full range (ensures full coverage). |

**Example:**
```markdown
:::: {.question type="formula_question" name="Stress Calc" points_possible="10"}

A beam has area A = [A] mm² and force F = [F] N. 
Compute the stress.

::: {.formula}
formula: F / A
margin: 2
margin_type: percent
answer_count: 5
distribution: even
:::

::: {.variable name="F"}
min: 10
max: 100
precision: 0
:::

::: {.variable name="A"}
min: 50
max: 500
precision: 0
:::

::::
```

> [!NOTE]
> The sync tool evaluates the formula locally via the `asteval` Python package before uploading pre-computed solutions to Canvas. Canvas does **not** calculate the formula on its own — it picks one of the uploaded datasets per student attempt.

### Choosing Classic vs New Quiz Engine

Both JSON and QMD quizzes can target either the **Classic** or **New** quiz engine in Canvas. The engine is selected via a single frontmatter key:

| | Classic Quizzes | New Quizzes |
|---|---|---|
| **QMD** | `type: quiz` | `type: new_quiz` |
| **JSON** | *(default, no extra key)* | `"quiz_engine": "new"` |

**Key differences:**

| | Classic | New |
|---|---|---|
| Canvas representation | Quiz object | Assignment (quiz-backed) |
| Time limit unit | **Minutes** | **Seconds** |
| Numeric & Formula questions | Not supported | ✅ Supported (QMD only) |
| `quiz_type` setting | ✅ (practice, graded, survey) | Not applicable |
| `omit_from_final_grade` | ✅ Graded quiz types only | ✅ Supported |
| `hide_in_gradebook` | ❌ Not offered — points come from the questions, so a real quiz can't be worth 0 | ✅ Supported |
| `score_to_keep` | Not applicable | ✅ `highest`, `latest`, `average`, `first` |
| `shuffle_questions` | Not applicable | ✅ Supported |
| `calculator_type` | Not applicable | ✅ `none`, `basic`, `scientific` |
| `cooling_period_seconds` | Not applicable | ✅ Wait time between attempts |
| Modifying active quizzes | Unpublish → Modify → Republish | Direct update |

> [!WARNING]
> **Modifying Active Classic Quizzes**
> The sync tool uses an **"Unpublish → Modify → Republish"** workflow for Classic Quizzes.
> *   **If no students have started**: Seamless — the quiz briefly flips to "Draft" mode, updates, and re-publishes.
> *   **If students have submissions**: Canvas **blocks** unpublishing. The tool updates questions in-place, but you will need to click **"Save It Now"** in Canvas to regenerate the quiz snapshot. The tool prints a direct link for convenience.
>     *   *This is a known Canvas API limitation.*

### Quiz Settings Reference

Settings shared by both formats and both engines (specified in `canvas` frontmatter or JSON `canvas` block):

| Setting | Type | Notes |
|---|---|---|
| `title` | String | Quiz title |
| `published` | Boolean | Default: `false` |
| `due_at` | ISO 8601 String | Removing clears the date in Canvas |
| `unlock_at` | ISO 8601 String | Removing clears the date in Canvas |
| `lock_at` | ISO 8601 String | Removing clears the date in Canvas |
| `shuffle_answers` | Boolean | Randomize answer order |
| `allowed_attempts` | Integer | `1` = single attempt, `-1` = unlimited, `N` = N attempts |
| `time_limit` | Integer | **Minutes** (Classic) or **Seconds** (New) |
| `one_question_at_a_time` | Boolean | Show one question at a time |
| `cant_go_back` | Boolean | Prevent going back (requires `one_question_at_a_time`) |
| `access_code` | String | Student must enter code to take quiz |
| `ip_filter` | String or List | Restrict the quiz to IPv4 addresses: single addresses, ranges `a-b`, or CIDR blocks, comma-separated or as a list, e.g. `"193.10.0.1-193.10.255.255"`. Removing the key does not lift the filter in Canvas |
| `description_file` | String | Path to `.qmd` description (Classic only) |
| `show_correct_answers` | Boolean | Classic only |
| `quiz_type` | String | Classic only: `practice_quiz`, `assignment`, `graded_survey`, `survey` |
| `points` | Float | New Quizzes only: total points possible |
| `instructions` | String | New Quizzes only: text shown to students before the quiz starts |
| `grading_type` | String | New Quizzes only: `points` (default), `percentage`, `pass_fail`, `letter_grade`, `gpa_scale`, `not_graded` |
| `shuffle_questions` | Boolean | New Quizzes only |
| `omit_from_final_grade` | Boolean | Do not count towards final grade. On Classic it needs `quiz_type: assignment` or `graded_survey` — other types never reach the gradebook |
| `hide_in_gradebook` | Boolean | New Quizzes only — hide from gradebook (requires `omit_from_final_grade` and `points` must be 0 or unset). Not possible on Classic |
| `score_to_keep` | String | New Quizzes only: `highest`, `latest`, `average`, `first` (default: `highest`) |
| `cooling_period_seconds` | Integer | New Quizzes only: wait time (seconds) between attempts |
| `calculator_type` | String | New Quizzes only: `none`, `basic`, `scientific` |
| `result_view` | Object | New Quizzes only: result/grade visibility settings (see below) |

> [!NOTE]
> **Classic vs New Quizzes — settings are handled differently by Canvas.**
> Classic Quizzes accept settings as flat fields on the quiz object. New Quizzes require settings nested inside a `quiz_settings` object (the sync tool handles this automatically). You use the **same YAML keys** for both engines — the tool translates to the correct API format internally.

#### Result View Settings (New Quizzes)

Control what students see after submitting a New Quiz. These settings are nested under `result_view:` in the `canvas` frontmatter block.

| Setting | Type | Notes |
|---|---|---|
| `restricted` | Boolean | Master switch: hide results from students |
| `show_questions` | Boolean | Show question text in results |
| `show_student_responses` | Boolean | Show the student's answers |
| `show_responses_frequency` | String | `always`, `once_per_attempt`, `after_last_attempt`, `once_after_last_attempt` |
| `show_responses_at` | ISO 8601 | Date to start showing responses |
| `hide_responses_at` | ISO 8601 | Date to stop showing responses |
| `show_correctness` | Boolean | Indicate correct/incorrect |
| `show_correctness_at` | ISO 8601 | Date to start showing correctness |
| `hide_correctness_at` | ISO 8601 | Date to stop showing correctness |
| `show_correct_answers` | Boolean | Show the correct answer |
| `show_feedback` | Boolean | Show per-question feedback |
| `show_points_awarded` | Boolean | Show points earned |
| `show_points_possible` | Boolean | Show total points possible |

> [!NOTE]
> **Dependency chain**: `restricted: true` is the master switch. When `false`, Canvas shows all result details regardless of other settings. Sub-settings like `show_responses_frequency` only take effect when their parent (`show_student_responses`) is enabled. The sync tool sends exactly what you specify — Canvas handles enforcement.

**Example:**
```yaml
canvas:
  type: new_quiz
  published: true
  result_view:
    restricted: true
    show_questions: true
    show_student_responses: true
    show_responses_frequency: after_last_attempt
    show_correctness: false
    show_correct_answers: false
    show_points_awarded: true
```

### Solo Files (PDFs, ZIPs, etc.)
*   **Inclusion**: Place directly inside a module folder and list it in that folder's `_order.yml`. In an `NN_` course, name it `NN_Name.ext` instead.
*   **Behavior**: 
    1.  The file is uploaded to the system-managed `synced-files` folder in Canvas.
    2.  It is automatically added to the Module as a **File** item.
    3.  **Titles**: The module item is titled with the filename, an `NN_` prefix stripped (e.g., `05_Syllabus.pdf` becomes "Syllabus.pdf").
    4.  Because it is a "Module Item", it is protected from the automatic **Orphan Cleanup**.

---

## 4. Calendar Synchronization

*   **File**: `schedule.yaml` in the content root.
*   **Command**: Must run with `--sync-calendar` to update.
*   **Logic**: 
    *   **Single Events**: Created as-is.
    *   **Series**: Defined with `days: ["Mon", "Thu"]`. Expanded into individual events.
*   **Manual Changes**: Syncing without the flag preserves manual changes in Canvas.

**Example `schedule.yaml`**:
```yaml
events:
  - title: "Kickoff Meeting"
    date: "2024-01-10"
    time: "09:00-10:00"
    description: "Introductory session."

  - title: "Weekly Lecture"
    start_date: "2024-01-15"
    end_date: "2024-05-15"
    days: ["Mon", "Wed"]
    time: "10:15-12:00"
    location: "Room 101"
```

---

## 5. Linking & Asset Handling (Power Feature)

The system automatically scans your Quarto content (`.qmd`) for links to local files and converts them into Canvas-ready links using intelligent resolution.

### A. Local Files (Downloads)
When you link to a non-content file (PDF, ZIP, DOCX, PY, etc.), the system **uploads** it to Canvas and links to the **Canvas file preview page**, which has a built-in **Download** button.
*   **Markdown**: `[Download Syllabus](docs/Syllabus.pdf)` or `[Get Script](files/script.py)`
*   **Result**: Link becomes `https://canvas.../courses/101/files/123`

### B. Images
Local images are **uploaded** to `course_images` and embedded.
*   **Markdown**: `![Elephant](graphics/elephant.jpg)`
*   **Result**: Image displays using Canvas file storage.

### C. Cross-Linking (Smart Navigation)
You can link directly to other Pages, Assignments, or Quizzes by referencing their **local filename**.
*   **Markdown**: `[Next Assignment](../02_Python/01_Assignment.qmd)`
*   **Result**: The system finds the real Canvas Assignment URL and links to it `https://canvas.../courses/101/assignments/555`.
*   **Circular Links**: If you link to a Page that hasn't been synced yet, the system automatically creates a **"Stub"** (empty placeholder) to generate the URL, ensuring your links never break.

### D. Asset Namespacing & Optimization
To keep your course clean and fast, the system uses a specialized strategy for assets:
*   **Reserved Folders**: All assets from your `.qmd` files are uploaded to `synced-images` and `synced-files`. 
*   **Smart Render & Upload**: The system records a **fingerprint of the content** it published, and compares it on the next sync.
    *   If a `.qmd` or `.json` file hasn't changed, it **skips Quarto rendering** and the Canvas `edit()` call.
    *   The fingerprint covers the images and included files a page uses, and the course-wide `_quarto.yml`, `branding.css` and `config.toml`. Replacing an image re-publishes the pages that show it; changing `branding.css` re-publishes everything.
    *   If an asset (image/PDF) hasn't changed, it **skips the upload**.
    *   Saving a file without changing it, or a `git pull`, does not count as a change. Line endings do not either, so a Windows and a Mac checkout agree.
    *   This makes subsequent syncs for large courses faster.
*   **Caching**: Folder IDs are cached during the run to minimize API calls.

### E. Orphan Asset Cleanup (Pruning)
Over time, course storage can get cluttered with old images you no longer use.
*   **How it works**: At the end of every sync, the system scans the reserved `synced` folders.
*   **Pruning**: Any file in these folders that is **NOT** referenced in your current content is automatically deleted.
*   **Safety**: This process **only** touches files inside the system's reserved folders. Your manuals uploads in `course_files` or `Documents` are never affected.

---

## 6. Three Ways to Run the Tool

Pick whichever fits how you work. They run the same tool and give the same results.

| | How | What the course folder needs |
|:--|:--|:--|
| **VS Code** (recommended) | The CanvasQuartoSync extension: sidebar, Course Board, Grade Rollup | Nothing |
| **Double-click** (Windows) | `.bat` files in the course folder | `check_content.bat`, `update_kit.bat`, optionally `run_sync_here.bat` |
| **Terminal** (any OS) | The `cqs` command | Nothing |

### The Course Board (VS Code)

The Course Board is the extension's main view, and opens by itself when a course folder
does. It shows every module and item in the order a sync will produce, next to what
Canvas holds:

*   **Status per item**: in sync, local or Canvas newer, never synced, Canvas only.
*   **Drag to reorder** modules (by the handle) and items (by the row), or drag an item
    into another module. It rewrites `_modules.yml`, `_order.yml` and, for a move, the
    file's folder and the links to it. Nothing reaches Canvas until you sync.
*   **Sync** one item, a whole module (*Sync all*), or a module Canvas does not have yet
    (*Create in Canvas*).
*   **Import**, **publish**, **delete**, **+ New** items and modules, **Check Canvas**
    for edits made there, and **Config** to open `config.toml`.
*   Files in a module folder that the sync skips are listed under the module with
    **+ Add**, and folders with content that are not in the course with **Add to course**.

It opens even when Canvas cannot be reached: with no `config.toml`, no `course_id`, no
token or no network it shows the local course and a banner saying what is wrong, with
**Open config.toml** and **Retry**. An `NN_` course gets a banner offering
**Preview conversion**.

### Double-click (Windows)

*   **`check_content.bat`** — check the course offline. The window stays open so you can
    read the result.
*   **`update_kit.bat`** — refresh the authoring kit after updating the tool.
*   **`run_sync_here.bat`** — sync the course to Canvas. VS Code's **New Project** adds
    it; otherwise copy it from the tool's folder into the course folder.
    For options such as calendar sync, make a shortcut to it, open the shortcut's
    **Properties**, and append the option to **Target**, e.g. `--sync-calendar`.

The `.bat` files find the installed tool themselves, so they keep working after the tool
moves or updates.

### Terminal: `cqs`

The installer puts `cqs` on your PATH. Run it anywhere inside a course folder:

```
cqs check                     # check the whole course, offline
cqs check 01_Intro/02_x.qmd   # one file; paths are relative to where you stand
cqs sync                      # sync to Canvas
cqs sync 02_x.qmd             # sync one file
cqs diff                      # has anyone edited Canvas since the last sync? Reads only
cqs rollup --status           # grade rollups: who qualifies
cqs kit update                # refresh the authoring kit
cqs where                     # which tool, Python and course cqs is using
cqs github --org jth-crunch   # put the course on GitHub as a private team course
cqs github invite USERNAME    # invite a teacher to the course's organisation
```

`cqs` on its own prints the list and does nothing else. Anything that writes to Canvas
takes a command you typed. On macOS and Linux this replaces the `.sh` launchers; courses
that already have them keep them.

---

## 7. Synchronization Strategy & Tracking

To ensure your Canvas course stays in sync through renames and moves, the system uses a **Local Mapping** strategy.

### The Sync Map (`.canvas_sync_map.json`)
The first time a file is synced, the system records its unique **Canvas ID**, a **fingerprint of the source** it was published from, and a **fingerprint of what Canvas showed** right after, in a hidden file called `.canvas_sync_map.json` in your content root. Nothing in it is specific to one computer, so in a shared course it is committed to git (see [Team mode](#team-mode-several-teachers-one-course)).

*   **Persistent Tracking**: Even if you change the `title:` in the metadata or rename the physical `.qmd` file, the system uses this ID to find and update the **existing** object in Canvas.
*   **Safe Renaming**: You can safely change the title of an assignment; it will be updated in both the Canvas Assignment list and the Module without creating duplicates.
*   **Preserving Data**: Because it updates the existing object by ID, student submissions, grades, and quiz results are always preserved.

> [!CAUTION]
> **Do not delete `.canvas_sync_map.json`**. If this file is lost, the system will fall back to "Matching by Title" for all content items. If you then rename a title, it will likely create a duplicate object in Canvas.

### When someone edits Canvas directly

Every sync compares each page, assignment and quiz in Canvas with what was last published. For assignments that includes the settings (due dates, points, submission types), not just the description. When Canvas has been edited:

| The `.qmd` | Canvas | What the sync does |
|:--|:--|:--|
| unchanged | edited | Leaves the Canvas version alone, saves a copy, reports it |
| changed | edited | **Solo:** overwrites, after saving a copy. **Team:** refuses to publish that file |

The copy goes to `.canvas_drift/` in the course folder: the Canvas HTML, a `.qmd` conversion of it for a side-by-side comparison, and who edited it and when (for pages, Canvas records the editor's name).

**To resolve it**, either:

*   **Keep the Canvas edit**: bring the change into the `.qmd`, commit, and sync with `--accept-drift <file>` (`cqs sync --accept-drift 04_Labs/01_Tensile.qmd`).
*   **Throw it away**: sync with `--accept-drift <file>` directly. The repo version is published over it.

`--accept-drift all` applies to every file. `cqs diff` lists Canvas edits without syncing.

### Team mode: several teachers, one course

When more than one person publishes a course, keep the course in a private GitHub repository. One step does all of it, from the course folder:

```
cqs github --org jth-crunch
```

or the **Create GitHub Repository** button in the extension's Project panel, which signs in to GitHub for you. It makes the folder a git repository, commits the sync map (and never student data or tokens), sets `workflow = "team"` in `config.toml`, creates the repository **private** in the organisation you choose, and pushes. `cqs github --help` has examples. Then invite the other teachers with `cqs github invite USERNAME` (or **Invite Teacher to GitHub**); each of them clones the repository in VS Code and sets `CANVAS_API_TOKEN` to their own Canvas token.

The idea: **the repository holds the truth, and Canvas is where it is published.** Every sync then:

1.  Refuses if there are uncommitted changes, so Canvas shows what is in the repo.
2.  Takes a **lock**, so two people cannot sync at the same time. If a colleague is syncing, you are told who and since when.
3.  Pulls, and refuses if you have commits that are not pushed. (Publishing something that is not on GitHub would let the next person's sync revert it.)
4.  Refuses to overwrite anything edited in Canvas, as described above.
5.  Commits and pushes the sync map, then releases the lock.

The sync map **must be committed** in team mode: remove `.canvas_sync_map.json` from `.gitignore`. It holds the Canvas ids everyone needs; a clone without it creates duplicates.

If a sync crashes, the lock stays for safety. After 30 minutes the message says so; run the sync again with `--break-lock`.

Each teacher uses their own Canvas token, so Canvas records who published what.

### Troubleshooting Missing Assets or Duplicates
If you manually delete an image or file on Canvas that was previously synced, the sync tool won't re-upload it automatically because the local file hasn't changed.
*   **To force a re-upload of a specific asset**: Run the sync with `--force` for the `.qmd` that uses it (`cqs sync 01_Intro/02_x.qmd --force`).
*   **To force a full course re-sync**: Delete the `.canvas_sync_map.json` file entirely. The tool will safely adopt existing modules, pages, assignments, and quiz questions by matching their exact titles/names. It will not create duplicates as long as you haven't renamed items locally. It will also clean up any duplicate quiz questions that share the exact same name.

---

## 8. Authoring with an AI Assistant

You will usually write course content in a **separate folder** from this tool, with an
AI coding assistant (Claude Code, Copilot, Cursor) open on it. A fresh assistant session
knows nothing about CanvasQuartoSync: not how modules and their order are declared, not the `canvas.*`
settings, not the quiz syntax.

The **authoring kit** fixes that. It installs a documentation bundle into your content
folder so any assistant opened there starts out knowing the format — without reading
this tool's source code.

### Setting up a content folder

In VS Code, use **New Project** in the sidebar. From a terminal, run the scaffolder once
per course, pointing it at the content folder:

```powershell
cqs init C:\Courses\MECH201
```

It creates:

```text
MECH201/
├── CLAUDE.md                      # Loaded automatically by Claude Code every session
├── .claude/skills/canvas-content/ # The authoring skill + reference documentation
├── check_content.bat              # Offline content validator (double-click)
├── update_kit.bat                 # Kit refresh (double-click)
├── config.toml                    # Course id and metadata (edit this first)
├── _quarto.yml                    # Quarto format config (HTML + PDF)
├── branding.css                   # Brand colours and callout styling
└── .gitignore
```

Add `--with-example` for a sample module to copy from.

Only the Windows `.bat` launchers are written; on macOS and Linux, `cqs` does the same
from the terminal. A folder that already has `.sh` launchers keeps them.

The launchers contain **no paths**: each run looks the tool up, first in
`CANVAS_QUARTO_SYNC_DIR` (a clone with its own `.venv`, for development), then in the
install location (`%LOCALAPPDATA%\CanvasQuartoSync` on Windows), then in the layouts
earlier installers used. Moving or reinstalling the tool never breaks a content folder.

Existing content is never overwritten — running the scaffolder on a folder that already
has content is safe.

### The workflow

1. Set `course_id` and `course_name` in `config.toml`.
2. Open the content folder in your editor and start the assistant.
3. Ask for what you want — a page, a quiz, an embedded video, a whole module. The
   assistant writes the files, validates them, and reports back.
4. Review the result and iterate.
5. **You run the sync** when it's ready — from the VS Code sidebar, `run_sync_here.bat`,
   or `cqs sync`.

The kit deliberately instructs the assistant **never to sync to Canvas**. Pushing to a
live course stays a human decision, so an assistant cannot reach your students by
accident.

### Checking content yourself

The validator is useful on its own, assistant or not. Double-click `check_content.bat`,
or from a terminal:

```powershell
cqs check                                  # everything
cqs check 01_Introduction\02_Welcome.qmd   # one file
```

It runs offline — no Canvas connection, no credentials, no Quarto — and reports what
each file **will become in Canvas**, plus the mistakes that otherwise only surface after
a sync:

*   files with `canvas:` metadata that the sync would skip, and why (a folder not in
    `_modules.yml`, a leading `_`, or a missing `NN_` prefix in an older course);
*   problems in `_modules.yml` and `_order.yml`: entries whose file is gone, conflict markers;
*   misspelled settings (`publish:` instead of `published:`);
*   invalid dates, out-of-range indents, unknown grading types;
*   broken image and link paths;
*   cross-references with no target, and images stacked on adjacent lines that never
    become figures (either way `@fig-x` reaches Canvas as `?@fig-x`);
*   quiz problems: mixed answer styles, missing correct answers, numeric or formula
    questions on the Classic engine, formulas that divide by zero.

It exits non-zero if it found errors, so it also works in a pre-commit hook or CI.

### Keeping the kit up to date

The kit ships with the tool, so upgrading the tool can leave a content folder's copy
behind. Two things keep them aligned, and neither rewrites files behind your back:

*   **`update_kit.bat`** in the content folder — double-click to refresh, or run
    `cqs kit update`. Your content,
    `config.toml`, and any edits you made to `CLAUDE.md` are preserved; if `CLAUDE.md`
    has been edited the refresh skips it and says so.
*   **A notice on sync** — `sync_to_canvas.py` prints a one-line warning when the
    folder's kit is older than the tool.
