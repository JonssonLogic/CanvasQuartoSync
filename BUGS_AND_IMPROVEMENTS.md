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

---

## 🚀 Future Improvements

### 1. Support for Custom Quarto Profiles/Args
The system uses a hardcoded render command: `quarto render ... --to html`.

**Enhancement**:
- Allow passing a `--quarto-args` flag via CLI.
- Detect `_quarto.yml` in the content root and use it during rendering.

---

### 2. Logging System
Replace `print()` statements with the standard Python `logging` module. This would allow:
- Saving logs to a file.
- Enabling `--verbose` or `--debug` modes.
- Cleaner output for automated CI/CD runners.
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

### 6. One-line Install Command
Create a simplified installation script for the entire system, similar to [python-dev-installer](https://github.com/cenmir/python-dev-installer).

**Enhancement**:
- A single terminal command (e.g., `curl ... | bash` or a PowerShell equivalent) that:
    - Checks for Python and Quarto CLI.
    - Creates a virtual environment.
    - Installs all necessary pip dependencies.
    - Sets up the basic directory structure if it doesn't exist.
- Significantly lowers the barrier to entry for new users/contributors.
