# Developer Guide — Canvas Quarto Sync

> **Purpose of this file**: Give any new contributor (human or AI) a fast, authoritative overview of the project so they can orient themselves and contribute safely.

---

## What This Project Does

**Canvas Quarto Sync** is a Python CLI tool that synchronizes a local folder of [Quarto](https://quarto.org/) content (`.qmd` files, JSON quizzes, PDFs, images, calendar YAML) to an [Instructure Canvas](https://www.instructure.com/) LMS course.

The user manages their entire course as a **local code repository** (Git). Running `sync_to_canvas.py` walks the directory tree, renders Quarto to HTML, and creates/updates Pages, Assignments, Quizzes, Module Items, and Calendar Events in Canvas via the REST API.

---

## Repository Layout

```
CanvasQuartoSync/
├── sync_to_canvas.py          # Entry point — CLI arg parsing, directory walk, handler dispatch
├── handlers/                  # All content-type handlers + shared utilities
│   ├── __init__.py
│   ├── base_handler.py        # Abstract base (can_handle, sync, add_to_module)
│   ├── single_sync.py         # Reusable single-asset sync (GUI-ready entry point)
│   ├── study_guide_handler.py  # .qmd → Canvas Page + PDF (dual output)
│   ├── page_handler.py        # .qmd → Canvas Page
│   ├── assignment_handler.py  # .qmd → Canvas Assignment
│   ├── quiz_handler.py        # .json / .qmd → Canvas Quiz (Classic Quizzes API)
│   ├── new_quiz_handler.py    # .json / .qmd → Canvas Quiz (New Quizzes API)
│   ├── new_quiz_api.py        # REST client wrapper for New Quizzes API
│   ├── qmd_quiz_parser.py     # Parser for QMD quiz format (fenced-div syntax)
│   ├── calendar_handler.py    # schedule.yaml → Canvas calendar events
│   ├── subheader_handler.py   # .md/.qmd → Module SubHeader (visual separator)
│   ├── external_link_handler.py # .qmd → Module External URL link
│   ├── content_utils.py       # Shared: image upload, cross-linking, sync map, pruning
│   └── log.py                 # Logging configuration (logger + setup_logging)
├── Guides/
│   ├── Canvas_Sync_User_Guide.md   # Full user-facing documentation
│   └── Canvas_token_setup.md       # How to get a Canvas API token
├── Example/                   # Reference content directory (module folders, .qmd files)
├── DEVELOPER_GUIDE.md         # This file — project overview & architecture
├── BUGS_AND_IMPROVEMENTS.md   # Tracked bugs & improvement ideas
├── LESSONS_LEARNED.md         # Canvas API gotchas, design decisions, pitfalls
├── README.md                  # GitHub readme
├── DISCLAIMER.md
├── LICENSE                    # MIT
├── requirements.txt           # Python package dependencies
├── requirements-dev.txt       # Test dependencies (pytest)
├── pytest.ini                 # Pytest configuration and markers
├── TESTING.md                 # Full testing guide
├── tests/                     # Test suite
│   ├── conftest.py            # Shared fixtures, --course-id CLI option, global state reset
│   ├── unit/                  # Pure logic tests (no external deps)
│   ├── integration/           # Mocked Canvas API tests
│   ├── e2e/                   # Real Canvas course tests
│   └── fixtures/
│       └── e2e_content/       # Stable test content for E2E tests
├── install.ps1                # One-line installer script (PowerShell)
└── run_sync_here.bat          # Portable launcher (copy to content folder, double-click)
```

---

## Architecture Overview

### Sync Pipeline

```
sync_to_canvas.py
  │
  ├── Parse CLI args (content_root, --course-id, --sync-calendar, --force, --check-drift, --only, --verbose, --quiet, --log-file)
  ├── Initialize logging (handlers/log.py → setup_logging())
  ├── Load Canvas API via canvasapi library
  ├── Walk content_root for NN_* folders (→ Modules) and NN_* files
  │
  └── For each file:
        ├── Handler chain: PageHandler → AssignmentHandler → QuizHandler
        │                  → SubHeaderHandler → CalendarHandler
        ├── First handler where can_handle() returns True wins
        └── handler.sync() does rendering + API create/update
```

### Handler Pattern

All handlers inherit `BaseHandler` (ABC):

| Method | Purpose |
|---|---|
| `can_handle(file_path)` | Return `True` if this handler owns the file (checks extension + frontmatter `canvas.type`) |
| `sync(file_path, course, module, ...)` | Render → upload → create/update Canvas object → add to module |
| `add_to_module(module, item_dict, indent)` | Shared logic: find existing module item or create new, sync title/indent/published |

### Key Shared Utilities (`content_utils.py`)

| Function | What it does |
|---|---|
| `process_content()` | Scans HTML/Markdown for images and links; uploads assets, resolves cross-links |
| `upload_file()` | Uploads a file to Canvas with smart caching (skips if `mtime` unchanged) |
| `resolve_cross_link()` | Resolves `[text](other.qmd)` → Canvas URL; creates stubs (JIT) for unsynced targets |
| `prune_orphaned_assets()` | Deletes files in `synced-images`/`synced-files` that are no longer referenced |
| `load_sync_map()` / `save_sync_map()` | Persist `.canvas_sync_map.json` (maps local path → Canvas ID + mtime) |
| `safe_delete_file/dir()` | Retry-with-backoff deletion (Dropbox/OneDrive lock workaround) |

---

## Naming Conventions

- **Modules**: Directories named `NN_Name` (e.g., `01_Introduction`). The `NN_` prefix sets order and is stripped for Canvas display.
- **Content files**: `NN_Name.ext` inside module dirs. Prefix determines module order; stripped from Canvas titles.
- **Non-prefixed files/dirs**: Ignored by the sync tool (e.g., `graphics/`, `handlers/`).

---

## Content Types & Detection

| Extension | Frontmatter `canvas.type` | Handler | Canvas Object |
|---|---|---|---|
| `.qmd` | `study_guide` | `StudyGuideHandler` | Wiki Page + PDF File (dual output) |
| `.qmd` | `page` | `PageHandler` | Wiki Page |
| `.qmd` | `assignment` | `AssignmentHandler` | Assignment |
| `.qmd` | `subheader` | `SubHeaderHandler` | Module Text Header |
| `.qmd` | `external_url` | `ExternalLinkHandler` | Module External URL |
| `.qmd` | `new_quiz` | `NewQuizHandler` | Quiz (New Quizzes) |
| `.qmd` | *(contains `:::: {.question` blocks)* | `QuizHandler` | Quiz (Classic) |
| `.json` | *(has `quiz_engine: new`)* | `NewQuizHandler` | Quiz (New Quizzes) |
| `.json` | *(structural check)* | `QuizHandler` | Quiz (Classic) |
| `.pdf`, `.zip`, etc. | N/A | Solo file logic in `sync_to_canvas.py` | Uploaded File + Module Item |
| `schedule.yaml` | N/A | `CalendarHandler` | Calendar Events |

---

## Smart Sync (mtime-based Skipping)

Each handler checks the file's `mtime` against the value stored in `.canvas_sync_map.json`. If unchanged → skip Quarto render and Canvas API update. Always runs `process_content()` to track `ACTIVE_ASSET_IDS` for pruning.

---

## Dependencies

All Python dependencies are listed in `requirements.txt` at the project root. The project uses a **virtual environment** managed with "uv".

**Quick setup** (Windows): `irm https://raw.githubusercontent.com/JonssonLogic/CanvasQuartoSync/main/install.ps1 | iex`

**Manual setup**: activate the venv and install:

```powershell
.venv\Scripts\activate      # Windows
# source .venv/bin/activate  # macOS/Linux
uv pip install -r requirements.txt
```

```
canvasapi          # Canvas REST API wrapper
requests           # Raw HTTP client for New Quizzes API
python-frontmatter # YAML frontmatter parser
PyYAML             # YAML parsing (calendar, quiz metadata)
asteval            # Safe math evaluation for Formula questions
rich               # Colored console output and pretty tracebacks
quarto             # External CLI — must be in PATH
```

---

## Testing

The project has a full test suite in `tests/`. **Always run the tests before and after making changes** to verify nothing is broken.

```powershell
# Run all fast tests (unit + integration, no external deps)
.venv\Scripts\python -m pytest tests/unit/ tests/integration/ -v

# Run a single test file
.venv\Scripts\python -m pytest tests/unit/test_qmd_quiz_parser.py -v

# Run E2E tests against a real Canvas test course
.venv\Scripts\python -m pytest tests/e2e/ -v -m canvas --course-id 12345
```

E2E credentials, course id, and a safety marker can be stored once in a gitignored `./.e2e/config.toml` (copy `tests/e2e/e2e.config.example.toml`) instead of passing them each run — resolution reuses `handlers/config.py`. The target course is purged and re-synced fresh each run, guarded by a course-name marker (`test_course_marker`) so a non-test course is never wiped. Shared logic lives in `tests/e2e/canvas_helpers.py`.

See `TESTING.md` for the full guide including how to set up E2E credentials.

### Test tiers

| Tier | Location | Requires | Speed |
|------|----------|----------|-------|
| Unit | `tests/unit/` | Nothing | < 1 s |
| Integration | `tests/integration/` | Nothing (mocked Canvas API) | < 1 s |
| E2E | `tests/e2e/` | Canvas credentials + Quarto CLI | Minutes |

### Writing tests for new functionality

**Every new feature or bug fix must be accompanied by tests.** This is the primary way to ensure changes do not break existing functionality.

- **New content type / handler** → add `can_handle()` tests in `tests/unit/test_handler_detection.py` and a sync integration test in `tests/integration/`. Add at least one representative content file to `tests/fixtures/e2e_content/` and a corresponding assertion in `tests/e2e/test_full_sync.py`.
- **New parser logic** (quiz format, preprocessor, etc.) → add unit tests directly in a `tests/unit/test_<module>.py` file covering the happy path plus edge cases.
- **New content utility** (upload logic, cross-linking, etc.) → add unit tests in `tests/unit/test_content_utils.py` and, if Canvas API interaction is involved, a mocked integration test.
- **Bug fix** → add a test that reproduces the bug first, then fix it. This prevents regressions.

Follow the **Arrange / Act / Assert** pattern (see `TESTING.md`). Group related tests in a class and use descriptive names (`test_rejects_missing_prefix`, not `test_case_3`).

---

## Common Tasks for AI Assistants

### Adding a new content type
1. Create a new handler class inheriting `BaseHandler`.
2. Implement `can_handle()` and `sync()`.
3. Register the handler in `sync_to_canvas.py`'s handler chain.
4. Add `can_handle()` tests to `tests/unit/test_handler_detection.py`.
5. Add a representative content file to `tests/fixtures/e2e_content/`.
6. Add E2E assertions to `tests/e2e/test_full_sync.py`.

### Syncing a single asset

A single content file can be synced without walking the whole course, via the
`--only <relpath>` CLI flag **or** programmatically (e.g. from a future GUI).

- **Reusable entry point**: `handlers/single_sync.py → sync_single_file(course, content_root, target_abs_path, canvas=None, handlers=None)`. It returns a `SingleSyncResult` dataclass (`success`, `message`, `module_item`, `position`) — no log parsing required.
- **Same handlers as full sync**: both paths build the handler chain via `build_handlers()` and find/create modules via `find_or_create_module()` (both in `single_sync.py`), so behavior is identical to a full sync. The handler still writes the sync map (`save_mapped_id`), so a later full sync recognizes the item.
- **Correct placement**: after syncing, `compute_insert_position()` determines the item's slot by counting the syncable sibling files (sorted filename order) that sort before it **and** are already present as module items. It matches files to module items by title via `content_utils.expected_canvas_title()` (a render-free title computation that mirrors the per-handler title rule).
- The CLI `--only` branch in `sync_to_canvas.py` simply resolves the path and delegates to `sync_single_file()`.

When adding a new content type, no extra work is needed for single-asset sync as long as the handler follows the standard pattern (returns its module item from `sync()` and updates the sync map). If the handler derives its title differently, update `expected_canvas_title()` to match.

### Modifying Quarto rendering
- The render pipeline is in `PageHandler.sync()` and `AssignmentHandler.sync()` (duplicated — see Improvements).
- Pattern: write temp `.qmd` → `quarto render --to html` → extract `<main>` content → cleanup temp files.

### Working with quizzes
- JSON format: parsed directly in `QuizHandler.sync()`.
- QMD format: parsed by `qmd_quiz_parser.py → parse_qmd_quiz()`, then rendered via `_render_qmd_questions()` batch Quarto call.
- Both formats support the same Canvas quiz settings.
- **Deduplication matching logic**: Canvas questions do not have a client-assigned ID. To match a local question with an existing Canvas question (and gracefully recover missing caches without duplicating), handlers pull all questions from Canvas, map them by `question_name` into arrays. As local questions are synced, they pop from the list. What's left over gets aggressively deleted from Canvas.

### Debugging sync issues
- Check `.canvas_sync_map.json` in the content root for ID mappings.
- Delete the map entry for a file to force re-render on next sync.
- Use `--verbose` (`-v`) to see DEBUG-level output with timestamps and log levels.
- Use `--log-file sync.log` to capture full debug output to a file.
- All output uses Python's `logging` module via the shared `logger` from `handlers/log.py`.

---

## Important Notes

- **Read `LESSONS_LEARNED.md`** for Canvas API quirks and design rationale. This file captures things a contributor still needs to be aware of (API limitations, non-obvious design choices, gotchas). Once an issue or limitation is resolved, its entry can be removed.
- **Read `Guides/Canvas_Sync_User_Guide.md`** for the full user-facing feature documentation.
- All dates in Canvas API use ISO 8601 format. Empty string `''` clears a date field; `None` is ignored.
- The Canvas API ignores `published` during module item creation — a separate `.edit()` call is required.
