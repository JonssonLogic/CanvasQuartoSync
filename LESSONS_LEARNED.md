# Lessons Learned

This document captures Canvas API quirks, design decisions, and pitfalls discovered during development. Read this before making changes to avoid repeating past mistakes.

---

## Canvas API Limitations

### Quiz Snapshot Regeneration (Cannot Be Fixed)
The Canvas REST API **cannot** force-regenerate a quiz snapshot (`quiz_data`) for an already-published quiz. The internal `generate_quiz_data` call only triggers during a `workflow_state` transition to `"available"`. For quizzes with student submissions, Canvas blocks unpublishing entirely, so the "Unpublish → Modify → Republish" workflow fails. The Canvas UI has a dedicated controller for this, but it requires SSO session authentication — Bearer tokens are not accepted.

**Current workaround**: Update questions in-place and print a direct URL so the user can click **"Save It Now"** manually in the Canvas UI.

### Published Flag Ignored on Module Item Creation
The Canvas API silently **ignores** the `published` field when creating a new module item. You must create the item first, then call `.edit(module_item={'published': True/False})` in a second API call. This is implemented in `BaseHandler.add_to_module()`.

### Date Fields: `None` vs Empty String
- Passing `None` for a date field (e.g., `due_at`) means **"don't change it"** — Canvas keeps the existing value.
- Passing an **empty string** `''` means **"clear the date"**.
- This distinction matters when a user removes a `due_at` from their frontmatter — we must send `''`, not `None`.

### Quiz Detection: Structural, Not Name-Based
Early versions detected quizzes by checking if the filename contained `"Quiz"`. This was brittle. The current approach checks the **JSON structure** (presence of `questions` array and `canvas` metadata block) or the presence of `:::: {.question` blocks in `.qmd` files.

### Classic Quizzes Only
This project targets the **Classic Quizzes** API. Canvas also has "New Quizzes" (backed by a different engine) which use a completely different API. The `canvasapi` library does not support New Quizzes.

---

## Design Decisions

### Why mtime-Based Skipping (Not Content Hashing)
We use the file's **last-modified time** (`os.path.getmtime()`) rather than content hashing to decide whether to re-render and re-sync. This is simpler, faster, and avoids reading + hashing every file on each run. The trade-off: touching a file without changing its content triggers a re-sync, but this is harmless.

### Why Always Run `process_content()` Even When Skipping Render
`process_content()` populates the global `ACTIVE_ASSET_IDS` set, which is used by `prune_orphaned_assets()` at the end of the sync. If we skipped `process_content()` for unchanged files, orphan cleanup would accidentally delete assets that are still in use.

### Sync Map (`.canvas_sync_map.json`) for ID Persistence
We track `local_path → (canvas_id, mtime)` in a JSON file so that:
1. Renaming a file or changing a title still updates the **existing** Canvas object (no duplicates).
2. We can skip unchanged files without querying Canvas.
3. Student submissions and grades are preserved across renames.

**Warning**: Deleting this file forces a fresh sync. If titles have changed since last sync, duplicates may be created.

### Reserved Asset Namespaces (`synced-images`, `synced-files`)
All uploaded images and files go into dedicated Canvas folders. This isolation enables safe **orphan pruning** — we can delete anything in these folders that isn't currently referenced, without risking user-uploaded content in other folders.

### JIT Stubbing for Cross-Links
When content A links to content B (`[see B](../02_Module/01_B.qmd)`), but B hasn't been synced yet, we create a **stub** (empty Page/Assignment) to get a valid Canvas URL. When B is eventually synced, it updates the existing stub via the sync map. This handles circular dependencies gracefully.

### Quarto Temp File Pattern
Rendering uses `_temp_{filename}.qmd` → `quarto render` → extract from `_temp_{filename}.html`. The `_temp_` prefix is checked in `can_handle()` to prevent handlers from recursively processing their own temp files.

### Retry-With-Backoff for File Deletion
When the project lives inside a Dropbox/OneDrive folder, the sync service can lock temp files immediately after creation. `safe_delete_file()` and `safe_delete_dir()` retry up to 5 times with 0.5s delays to handle this.

---

## Quarto Rendering Gotchas

### Extracting Content from Rendered HTML
Quarto wraps the rendered body in `<main id="quarto-document-content">`. We extract only the inner content to avoid injecting Quarto's full page shell into Canvas. We also strip the `<header id="title-block-header">` to avoid duplicating the title (Canvas provides its own).

### Duplicate Rendering Logic
`PageHandler.sync()` and `AssignmentHandler.sync()` contain **near-identical** Quarto rendering code. This is a known tech debt item. A future refactor should extract this into a shared `QuartoRenderer` utility.

### Batch Rendering for QMD Quizzes
QMD quizzes can have many questions, each with markdown and LaTeX. Rendering them individually would invoke Quarto N times. Instead, `_render_qmd_questions()` batches all question/answer content into a **single** temp `.qmd` file using `<div id="qchunk-N">` markers, renders once, then splits the output back into individual pieces. This is a significant performance optimization.

---

## QMD Quiz Format Notes

### Two Answer Styles
- **Checklist** (`- [x]` / `- [ ]`): Best for short text and formula answers. Per-answer comments are indented sub-items.
- **Rich div** (`::: {.answer ...}`): Best for multi-paragraph answers or answers containing images. Per-answer comments use the `comment="..."` attribute.
- **Never mix both styles** in the same question block.

### Indentation Is Optional
Content inside `:::: question` and `::: answer` blocks can be indented for readability. The parser uses `textwrap.dedent`-style logic to strip common leading whitespace.

---

## Canvas API Tips

- **Search by title is unreliable for exact matching**: `course.get_pages(search_term=title)` returns fuzzy matches. Always iterate results and compare `p.title == title` exactly.
- **Module item ordering**: New items are appended to the end. If order matters, you must move items after creation using the Canvas API's position endpoint.
- **Rate limiting**: The Canvas API has rate limits. The `canvasapi` library handles some retries, but large courses with many assets can still hit limits. Folder caching (`FOLDER_CACHE`) and mtime-skipping help reduce API calls.
- **`quiz_type` default**: If not specified, Canvas creates quizzes as `"practice_quiz"`. Use `quiz_type: assignment` in metadata to make graded quizzes.
