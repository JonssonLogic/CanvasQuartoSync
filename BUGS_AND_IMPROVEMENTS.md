# Bugs & Improvements

Active known issues and planned enhancements.

For past issues and design rationale, see [LESSONS_LEARNED.md](LESSONS_LEARNED.md).

---

## Known Bugs

### Quiz "Save It Now" Banner After Sync (Canvas API Limitation)

When syncing a quiz that has student submissions, Canvas shows an "Unsaved Changes" banner. The script cannot unpublish/republish the quiz (Canvas blocks this), so the quiz snapshot is not regenerated.

- **Root cause**: The Canvas REST API only regenerates `quiz_data` during a `workflow_state` transition. The Canvas UI has a dedicated endpoint for this, but it requires SSO session auth.
- **Mitigation**: The script detects this, updates the quiz in-place, and prints a URL so the user can click "Save It Now" manually.
- **Status**: Cannot be fixed without Canvas-side changes.

---

## Planned Improvements

### Custom Quarto Profiles/Args

The system uses a hardcoded render command: `quarto render ... --to html`. Allow passing `--quarto-args` via CLI, or detect `_quarto.yml` in the content root.

### New Quizzes: Additional Question Types

Remaining New Quizzes API types not yet implemented:
- `matching`, `categorization`, `ordering`
- `essay`, `file-upload`
- `rich-fill-blank`, `hot-spot`

Each type has its own `interaction_data` and `scoring_data` structure. See the [Canvas API docs](https://canvas.instructure.com/doc/api/new_quiz_items.html#Question+Types-appendix).

### VS Code Extension

See [extension/TODO.md](extension/TODO.md) for extension-specific issues (comment highlighting on math content, scroll sync).
