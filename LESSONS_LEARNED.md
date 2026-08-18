# Lessons Learned

This document captures Canvas API quirks, design decisions, and pitfalls discovered during development. Read this before making changes to avoid repeating past mistakes.

---

## Canvas API Limitations

### Quiz Snapshot Regeneration (Cannot Be Fixed)
The Canvas REST API **cannot** force-regenerate a quiz snapshot (`quiz_data`) for an already-published quiz. The internal `generate_quiz_data` call only triggers during a `workflow_state` transition to `"available"`. For quizzes with student submissions, Canvas blocks unpublishing entirely, so the "Unpublish → Modify → Republish" workflow fails. The Canvas UI has a dedicated controller for this, but it requires SSO session authentication — Bearer tokens are not accepted.

**Current workaround**: Update questions in-place and log a direct URL so the user can click **"Save It Now"** manually in the Canvas UI.

### Front Page Cannot Be Unpublished
If a page is set as the Canvas course **front page** ("startsida"), the API will reject any update that includes `published: false` with a `BadRequest` error ("Framsidan kan inte avpubliceras"). The sync tool catches this specific error and retries the edit **without** the `published` field, so the page title and body still sync correctly. The `published` state of the front page is left unchanged. This is handled in `PageHandler.sync()`.

### Published Flag Ignored on Module Item Creation
The Canvas API silently **ignores** the `published` field when creating a new module item. You must create the item first, then call `.edit(module_item={'published': True/False})` in a second API call. This is implemented in `BaseHandler.add_to_module()`.

### Date Fields: `None` vs Empty String
- Passing `None` for a date field (e.g., `due_at`) means **"don't change it"** — Canvas keeps the existing value.
- Passing an **empty string** `''` means **"clear the date"**.
- This distinction matters when a user removes a `due_at` from their frontmatter — we must send `''`, not `None`.

### Dates Must Be Normalised Before They Reach Any API
Dates used to travel from source file to Canvas untouched. What a date *meant* then
depended on three things that had nothing to do with the author's intent:

- **The file format.** JSON has no date type, so `"2026-08-17T09:00:00"` stayed a
  string. PyYAML resolves the same unquoted text in `.qmd` frontmatter into a
  `datetime` (preserving `tzinfo`), and a bare date into a `datetime.date`.
- **Quoting.** Only in YAML, and only sometimes — which made quoting silently
  load-bearing in one handler and irrelevant in every other.
- **The receiving handler.** `canvasapi` form-encodes, so `requests` called `str()`
  on a `datetime` and it survived. The New Quizzes client posts JSON, where the same
  value hit `json.dumps` and aborted the sync with *"Object of type datetime is not
  JSON serializable"*. The E2E suite missed this for a long time because its only
  New Quiz fixture happened to quote its date.

Everything now funnels through `handlers/dates.py → to_canvas_iso()`, which always
returns a `str`. That makes the JSON crash unrepresentable rather than merely fixed.

### Local Wall-Clock Time Beats Both `Z` and Fixed Offsets
Canvas accepts ISO 8601 with an offset and stores UTC. Neither literal form is safe
to author by hand across a semester:

- A hardcoded `+02:00` is **wrong** for the half of the year Sweden is on `+01:00`.
- A fixed `09:00Z` is a valid instant but not a stable clock time — it displays as
  11:00 local in summer and 10:00 after the clocks change, so a weekly deadline
  drifts an hour mid-semester.

Authors therefore write naive local times and the tool converts. The zone comes from
`config.toml` first, then the Canvas course's `time_zone`. Resolution is **lazy** —
a course whose dates all carry `Z` never needs one — which is what let this ship
without a content migration. Values already carrying `Z` or an offset are passed
through untouched and keep their exact prior meaning.

Note the mismatch case is a warning, not an error: we always send UTC, so the stored
instant is right regardless. What a mismatch changes is *display*, since Canvas
renders in the course's zone — students would see a different clock time than the
files state.

`tzdata` is a hard dependency on Windows, which ships no system tz database.

### Comparing Dates Against Canvas Means Comparing Instants
Canvas answers in UTC no matter what was sent. The calendar handler used to dedup by
substring-matching the local time against `start_at`, which worked only because it
also sent local-looking UTC. Converting times broke it instantly — every sync would
have created duplicate events. Parse both sides and compare instants
(`parse_canvas_utc()`); never match timestamp substrings.

### Daylight Saving Has Two Odd Local Times
Spring forward creates an hour that never happens; autumn back creates one that
happens twice. `zoneinfo` resolves both silently. PEP 495's `fold` flag is set for
*both* cases, so a differing `utcoffset()` between `fold=0` and `fold=1` cannot tell
them apart — test for the gap first (a round trip through UTC lands on a different
wall clock), then for ambiguity. The validator warns; neither is an error, since both
still resolve to a real instant.

### Quiz Detection: Structural, Not Name-Based
Early versions detected quizzes by checking if the filename contained `"Quiz"`. This was brittle. The current approach checks the **JSON structure** (presence of `questions` array and `canvas` metadata block) or the presence of `:::: {.question` blocks in `.qmd` files.

### New Quizzes API (`/api/quiz/v1/`)
The New Quizzes engine uses a **completely separate API** from Classic Quizzes. Key differences and gotchas:

- **`canvasapi` has no support** — all New Quizzes calls must use raw `requests` against `/api/quiz/v1/courses/:id/quizzes`.
- **Payload wrapping is mandatory** — quiz creation/update requires `{"quiz": {...}}`, and item creation requires `{"item": {...}}`. Sending the data directly returns `{"errors":[{"message":"quiz is missing"}]}`.
- **`scoring_algorithm` is required** on every question item. Valid values are **not** obvious from the error messages:
  - `"Equivalence"` for `choice` and `true-false` questions
  - `"AllOrNothing"` for `multi-answer` questions
  - `"None"` for `essay` and `file-upload` questions
  - Using an invalid value (e.g., `"ExactMatch"`) returns an internal Ruby error: `"uninitialized constant Scoring::Algorithms::...::ExactMatch"`.
- **Quizzes are assignment-backed** — creating a New Quiz also creates an Assignment. Module items must use `type: 'Assignment'`, not `type: 'Quiz'`.
- **Time limits are in seconds**, not minutes (unlike Classic Quizzes).
- **The `properties` field** (even if empty `{}`) should be included in item payloads — the official API examples always include it.
- **The Question Name `title` is inside `entry`** — the New Quizzes API returns questions wrapped in an `entry` object, so you must get the title via `item['entry']['title']` when matching existing items (e.g. `item_body` usually doesn't have it).
- **Formulas are NOT evaluated by Canvas server-side** — when creating a Formula question, the API requires `generated_solutions` (an array of pre-computed outputs for randomized inputs). The sync tool must bundle a safe math lexer/parser (`asteval`) to randomly generate these inputs, calculate the result, and upload the fixed datasets. Canvas then blindly picks one of the pre-computed arrays for each student attempt.
- **`scoring_data.value` must be a raw object/array for numeric and formula** — the correct combination is `scoring_algorithm: "Numeric"` with a raw object (formula) or array (numeric). Sending `json.dumps()` passes API validation but causes the Canvas quiz builder to fail rendering. When in doubt, fetch items from a manually-created quiz via `GET /items` to see the expected format.
- **`scoring_algorithm` values for New Quizzes** — `"Equivalence"` for choice/true-false, `"AllOrNothing"` for multi-answer, `"Numeric"` for numeric/formula (enables autograding), `"None"` for essay/file-upload (manual grading only). Using `"None"` for numeric/formula will cause Canvas to mark those questions as "requires grading" instead of autograding them.
- **Digital Content (Images) in New Quizzes**: New Quizzes (LTI-based) handles file URLs differently than the main Canvas shell. `/preview` URLs for files return an HTML page wrapper, not the raw data, making them unsuitable for `<img>` tags. Use the standard `/download` URL with a `verifier` token for reliable rendering.
- **Stable URL persistence**: Verifier tokens for course files are remarkably stable and typically persist as long as the file exists in that course context. Attempting to strip them or using session-based preview URLs will result in broken images for students.
- **New Quizzes settings must be nested under `quiz_settings`**: The New Quizzes API expects display/behavior settings inside a `quiz_settings` object — sending them at the top level causes Canvas to silently ignore them. Multiple-attempt settings are nested one level deeper in `quiz_settings.multiple_attempts` with fields `multiple_attempts_enabled` (bool), `score_to_keep` (string, **required** when multiple attempts enabled), and optionally `max_attempts` (int, only for finite limits). Time limits need both `has_time_limit: true` and `session_time_limit_in_seconds`. Access codes need both `require_student_access_code: true` and `student_access_code`.
- **`score_to_keep` is mandatory and uses short-form values**: When `multiple_attempts_enabled` is `true`, omitting `score_to_keep` returns a 400 error (`"is not included in the list"`). Valid values are the **short forms**: `highest`, `latest`, `average`, `first`. The prefixed forms (`keep_highest`, etc.) that appear in some documentation are **rejected** by the write API, even though the GET response returns the short forms. The sync tool defaults to `highest` when not specified.
- **Setting name translation between Classic and New Quizzes**: Some settings use different API field names: `one_question_at_a_time` (bool) → `one_at_a_time_type` (enum `"question"`/`"none"`); `cant_go_back` (bool) → `allow_backtracking` (bool, **inverted logic**); `access_code` → `student_access_code`. The sync tool accepts the Classic YAML keys and translates internally, so users don't need to learn engine-specific field names.
- **Assignment-level settings must use the Assignments API**: New Quizzes are assignment-backed, but the New Quizzes API (`/api/quiz/v1/`) does **not** propagate assignment-level properties like `omit_from_final_grade`, `hide_in_gradebook`, or `grading_type`. These must be applied via a separate `course.get_assignment(id).edit()` call using the standard Canvas Assignments API after the quiz is created or updated.
- **`grading_type` must be set on the backing assignment for autograding**: Without `grading_type: "points"` (or another valid grading type) on the backing assignment, Canvas may not autograde questions and may not display student responses to either teachers or students. The sync tool defaults to `"points"` which matches what Canvas UI sets when creating a graded New Quiz. Valid values: `points`, `percentage`, `pass_fail`, `letter_grade`, `gpa_scale`, `not_graded`.
- **Cannot unpublish quizzes with student submissions**: Canvas rejects sending `published: false` on a quiz (or its backing assignment) that already has student submissions. The sync tool sends the user's requested published state directly rather than trying an unpublish→republish cycle.
- **`hide_in_gradebook` has strict Canvas-side constraints**: Canvas validates that (1) `omit_from_final_grade` must also be `true`, and (2) `points_possible` must be 0 or unset. If points are assigned, Canvas rejects the update with `"Hide in gradebook is not included in the list"`. The sync tool auto-enables `omit_from_final_grade` when `hide_in_gradebook` is requested; when points are set it **drops the flag and warns** rather than sending a request Canvas will refuse — a refusal takes the whole payload with it, which on an assignment create would mean nothing gets created at all. **An explicit `hide_in_gradebook: false` is accepted** — an earlier note here claimed otherwise, and a spike against a live course disproved it. `false` passes even in the same request that raises `points_possible` above 0, because the `inclusion` validation only bites on `true`. Clearing the flag therefore rides along in the payload the sync was already sending (`handlers/gradebook.py`), which is what makes removing the key from a file put the column back.
- **Classic quizzes keep gradebook settings on their backing assignment**: reading canvas-lms directly settles what the API docs omit. `Quizzes::Quiz#build_assignment` (a `before_save`) creates a backing assignment on **every save** of a graded quiz (`quiz_type: assignment` / `graded_survey`), published or not, and `assignment_id` is serialized on the Quiz API object. `Quiz#update_assignment` overwrites only `points_possible, description, title, due_at, lock_at, unlock_at, only_visible_to_overrides, submission_types, assignment_group_id, workflow_state` — it does **not** touch `omit_from_final_grade` or `hide_in_gradebook`, so what you set on the backing assignment survives later quiz saves. That is what makes `omit_from_final_grade` work for Classic despite the Quizzes API having no such field.
- **Classic quizzes are not offered `hide_in_gradebook`, though Canvas would take it**: verified against a live course — hiding a graded classic quiz's backing assignment *succeeds* when the quiz is worth 0 points (the spike's quiz had no questions). It is not exposed because a classic quiz's `points_possible` is derived from its questions and pushed onto the backing assignment on every save, so a quiz with actual content can only qualify if every question is worth 0 — which defeats the point of a graded quiz. `quiz_type: practice_quiz` is the real answer: practice quizzes and ungraded surveys have no backing assignment and never reach the gradebook. `validate_content.py` rejects the key with that explanation rather than letting it silently do nothing.

---

## Design Decisions

### Why mtime-Based Skipping (Not Content Hashing)
We use the file's **last-modified time** (`os.path.getmtime()`) rather than content hashing to decide whether to re-render and re-sync. This is simpler, faster, and avoids reading + hashing every file on each run. The trade-off: touching a file without changing its content triggers a re-sync, but this is harmless.

### Why Always Run `process_content()` Even When Skipping Render
`process_content()` populates the global `ACTIVE_ASSET_IDS` set, which is used by `prune_orphaned_assets()` at the end of the sync. If we skipped `process_content()` for unchanged files, orphan cleanup would accidentally delete assets that are still in use.
**Crucial for Quizzes:** Quizzes and Subheaders only run Quarto *if* `needs_update` is true. To track their assets we now run `process_content()` manually *before* the skip check and **deliberately discard** the generated HTML. This populates `ACTIVE_ASSET_IDS` without mutating internal dictionaries (which would trigger false updates).

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

### Title Rules Are Not Uniform Across Handlers
Quiz handlers (`QuizHandler`, `NewQuizHandler`) take their title from **`canvas.title`**
and ignore the top-level frontmatter `title:`. Every other handler does the opposite.
Anything that predicts a Canvas title without rendering — currently
`content_utils.expected_canvas_title()`, used by `compute_insert_position()` to place a
single synced asset — must branch on the content type, and must respect handler order
(`StudyGuideHandler` runs first and also claims files whose name contains
`studyguide`/`kurspm`). A file no handler claims uploads as a solo asset, whose module
item title **keeps the file extension**.

### Always Fetch Before Create-or-Update
When implementing update logic for any Canvas object, **always try to fetch the existing object** before deciding to create a new one — even if the local file has changed. A common bug pattern: putting the "fetch existing" call inside the "skip if unchanged" branch means that when the file *does* change, the handler thinks no object exists and creates a duplicate. The fetch must happen unconditionally whenever a sync map ID is available.

### Handling Duplicate Items on Canvas
When matching objects like Quiz Questions that don't have UUIDs, map the existing items from Canvas into a dictionary where the `key` is the title/name and the `value` is a **list** of matching items. That way, if multiple identical duplicates already exist on Canvas (e.g. from an aborted sync), you can adopt the first one and comprehensively delete all the remaining extras during cleanup. Simply matching 1-to-1 via dictionary strings will silently ignore extras and leave overlapping duplicates intact.

---

## Quarto Rendering Gotchas

### Extracting Content from Rendered HTML
Quarto wraps the rendered body in `<main id="quarto-document-content">`. We extract only the inner content to avoid injecting Quarto's full page shell into Canvas. We also strip the `<header id="title-block-header">` to avoid duplicating the title (Canvas provides its own).


### Batch Rendering for QMD Quizzes
QMD quizzes can have many questions, each with markdown and LaTeX. Rendering them individually would invoke Quarto N times. Instead, `_render_qmd_questions()` batches all question/answer content into a **single** temp `.qmd` file using `<div id="qchunk-N">` markers, renders once, then splits the output back into individual pieces. This is a significant performance optimization.

### Query String Mangling
Quarto/Pandoc appends extensions (e.g., `.png`) to URLs missing them. For Canvas links with verifier tokens, this appends the extension to the end of the query string, breaking the token. The sync tool prevents this by inserting the extension directly before the `?` (e.g., `.../download.png?verifier=...`).

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
