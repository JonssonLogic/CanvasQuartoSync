# 🐛 Bugs & 🚀 Improvements

This document tracks **active** known issues and planned enhancements for the **Canvas Quarto Sync** project.

> For past issues and the reasoning behind current design choices, see [LESSONS_LEARNED.md](LESSONS_LEARNED.md).

---

## 🐛 Known Bugs

### 1. Quiz "Save It Now" Banner After Sync (Canvas API Limitation)
**Problem**: When syncing a quiz that has student submissions, the script cannot unpublish/republish the quiz (Canvas blocks this). As a result, question changes are saved to the database but the quiz **snapshot** (`quiz_data`) is not regenerated. Canvas shows an "Unsaved Changes" / "Save It Now" banner.

**Details**:
- **Root Cause**: The Canvas REST API only triggers `generate_quiz_data` (the snapshot) during a `workflow_state` transition to `"available"`. For already-published quizzes, the state doesn't change, so the snapshot stays stale. The Canvas UI has a dedicated controller that calls `generate_quiz_data` explicitly, but this endpoint requires SSO session auth and cannot be accessed with API Bearer tokens.
- **Location**: [quiz_handler.py](file:///c:/Users/CV/MyCodeProjects/CanvasQuartoSync/handlers/quiz_handler.py)
- **Mitigation**: The script detects this case, updates the quiz in-place (without crashing), and prints a direct URL to the quiz so the user can quickly click "Save It Now" manually.
- **Status**: **Known limitation** — cannot be fixed without Canvas-side changes or SSO browser automation.

### 2. `expected_canvas_title()` Disagrees With Quiz Handlers

**Problem**: `content_utils.expected_canvas_title()` reads the **top-level** `title:` from
frontmatter, but `QuizHandler` and `NewQuizHandler` take their title from
**`canvas.title`** and ignore the top-level one.

**Effect**: For a quiz whose title is set under `canvas:` (the documented way), the two
disagree — `expected_canvas_title()` returns the `NN_`-stripped filename while Canvas
shows the `canvas.title` value. `compute_insert_position()` in `single_sync.py` matches
module items by title, so a `--only` sync of a sibling can compute the wrong slot when
a quiz sits before it in the module.

- **Location**: [content_utils.py](handlers/content_utils.py) `expected_canvas_title()`
- **Fix**: make the helper mirror the per-handler rule — prefer `canvas.title` for
  `.qmd` files whose `canvas.type` is `quiz`/`new_quiz` (and for structurally-detected
  classic quizzes), falling back to the top-level `title:` otherwise.
- **Found by**: writing the content-kit documentation; not yet covered by a test.

---

## 📋 Kit Gaps

Behaviour that content authors (or their AI assistants) hit but which isn't documented,
collected from `.claude/kit-gaps.md` files in content folders. Promote entries here into
`content_kit/skills/canvas-content/reference/` once confirmed.

- **Quarto shortcodes beyond `{{< video >}}`** — `{{< include >}}` and `{{< embed >}}`
  are untested against the Canvas render path.
- **Cross-page figure references** — `@fig-label` across separate Canvas pages is
  undefined; Quarto numbering is per-document.
- **Client-side diagram blocks** — Mermaid and friends render via JavaScript, which
  Canvas strips. Presumed unsupported; needs confirming, and if so a documented
  alternative (render to an image at author time).

---

## 🚀 Future Improvements

### 1. Support for Custom Quarto Profiles/Args
The system uses a hardcoded render command: `quarto render ... --to html`.

**Enhancement**:
- Allow passing a `--quarto-args` flag via CLI.
- Detect `_quarto.yml` in the content root and use it during rendering.

---

### ~~2. Logging System~~ (Implemented)
Replaced all `print()` statements with Python's `logging` module + `rich` for colored console output.

_Implemented with `--verbose`, `--quiet`, and `--log-file` CLI flags. See `handlers/log.py` for configuration._

---

### ~~3. New Quizzes: Additional Question Types~~ (Partially Implemented)
`numeric` and `formula` questions were added. The remaining New Quizzes API types are:
- `matching` — match items to categories
- `categorization` — sort items into groups
- `ordering` — arrange items in sequence
- `numeric` — numeric input with margin of error
- `essay` — free-text response (manually graded)
- `file-upload` — student file submission
- `rich-fill-blank` — fill-in-the-blank with rich content
- `hot-spot` — click on a region of an image

Each type has its own `interaction_data` and `scoring_data` structure. See the [official API docs](https://canvas.instructure.com/doc/api/new_quiz_items.html#Question+Types-appendix) for details.

---

### ~~4. New Quizzes: Formula Questions with Variables~~ (Implemented)
The New Quizzes `formula` question type supports **parameterized questions**.

_Implemented via local evaluation utilizing `asteval` to precalculate and upload data sets per the Canvas API requirements._

**Considerations**:
- Requires defining variables (name, min, max, precision) and a formula string in the question metadata.
- The API uses `generated_solutions` — pre-computed answer sets that must be calculated and included in the payload.
- A new frontmatter/JSON syntax would be needed to define variables and formulas in a user-friendly way.
- 
---

### 5. Canvas Asset Removal Tool
Develop a dedicated utility or CLI flag to remove assets from Canvas that were previously synced.

**Details**:
- The tool should use the `.canvas_sync_map.json` file to identify items (Pages, Assignments, Quizzes, Files) that it "owns" in the Canvas course.
- Useful for cleaning up a course after a major restructuring or when wanting to start fresh without manually deleting dozens of items in the Canvas UI.
- Should include a `--dry-run` option to show what would be deleted.

---

### ~~6. One-line Install Command~~ (Implemented)
A PowerShell one-liner installs the entire system interactively.

_Implemented as `install.ps1` — checks for Python/Quarto/Git, clones the repo, creates a venv at `~/venvs/canvas_quarto_env`, installs packages from `requirements.txt`, and walks the user through Canvas API credential setup. Run via `irm .../install.ps1 | iex`._

---

### 7. Study Guide (Dual HTML + PDF Output)
A single `.qmd` file that produces **two Canvas artifacts** from one source:

1. **Canvas Page (HTML)** — the student-facing welcome/study guide, added to the module where the file lives.
2. **PDF** — a standardized regulatory document, uploaded to a separately specified module.

**Motivation**: Regulatory requirements mandate a formatted PDF study guide in every course. Rather than maintaining two separate files, a single QMD file uses Quarto's conditional content blocks (`.content-visible when-format="html"` / `when-format="pdf"`) to include shared and format-exclusive sections.

**Design**:
- New `canvas.type: study_guide` triggers a dedicated `StudyGuideHandler`.
- Frontmatter includes a `canvas.pdf.target_module` field (required) specifying which module receives the PDF.
- The handler renders the QMD twice (`--to html` and `--to pdf`), syncs the HTML as a Canvas Page, and uploads the PDF as a file item in the target module.
- Requires a LaTeX distribution (e.g., `quarto install tinytex`) for PDF rendering.
- If PDF rendering fails, the HTML page is still synced (partial success).
