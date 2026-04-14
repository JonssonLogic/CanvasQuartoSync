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
├── extension/                 # VS Code extension (TypeScript + React)
│   ├── src/                   #   Extension host (Node.js, VS Code API)
│   │   ├── extension.ts       #     Entry point — registers commands, sidebar, status bar
│   │   ├── commands/          #     syncToCanvas, importFromCanvas, diffWithCanvas, purgeCourse, etc.
│   │   ├── providers/         #     Sidebar tree view, preview panel, new project form, status bar
│   │   ├── python/            #     Venv resolution + Python script runner
│   │   └── config/            #     config.toml loader
│   ├── webview/               #   React app (runs in sandboxed webview)
│   │   ├── components/        #     MarkdownRenderer, CodeBlock, MermaidBlock, TabsetBlock, etc.
│   │   ├── preprocessing/     #     qmdPreprocess, remarkCallouts, bibParser, commentParser
│   │   ├── hooks/             #     useFileContent, useComments
│   │   └── styles/            #     Canvas-matching CSS
│   ├── package.json           #   Extension manifest (commands, menus, settings, walkthrough)
│   ├── esbuild.mjs            #   Bundles src/ → dist/extension.js
│   ├── vite.config.ts         #   Bundles webview/ → dist/webview/
│   ├── devInstructions.md     #   Extension build/debug/test guide
│   └── TODO.md                #   Known issues (math highlighting, scroll sync)
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
├── install.ps1                # One-line installer (Python + Git + packages + VS Code extension)
└── run_sync_here.bat          # Portable launcher (copy to content folder, double-click)
```

---

## Architecture Overview

### Sync Pipeline

```
sync_to_canvas.py
  │
  ├── Parse CLI args (content_root, --course-id, --sync-calendar, --verbose, --quiet, --log-file)
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

**Quick setup** (Windows): `irm https://raw.githubusercontent.com/cenmir/CanvasQuartoSync/main/install.ps1 | iex`

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

---

## VS Code Extension

The `extension/` directory contains a VS Code extension that wraps the Python tools with a GUI. See `extension/devInstructions.md` for build and debug instructions.

### How it works

The extension has two parts:

- **Extension host** (`src/`): Node.js code that uses the VS Code API. Spawns the Python scripts, manages the sidebar, status bar toggles, and webview panels.
- **Webview** (`webview/`): A React app running in a sandboxed iframe inside VS Code. Handles the QMD preview rendering (remark/rehype pipeline, KaTeX, Mermaid, syntax highlighting) and inline commenting.

### Extension commands

| Command | What it does |
|---|---|
| Sync to Canvas | Opens a menu with Sync All / Sync Current File + toggle flags |
| Sync This File | Syncs a single .qmd file (right-click or editor title icon) |
| Import from Canvas | Runs `import_from_canvas.py` with scope picker |
| Diff with Canvas | Runs `sync_to_canvas.py --check-drift --show-diff` |
| Purge Canvas Course | Runs `purge_course.py` with confirmation (must type course name) |
| New Project | Opens a full-page form to scaffold a new course |
| Open Preview | Opens a side-by-side QMD preview matching Canvas styling |

### Preview rendering pipeline

The preview ports MDViewer's rendering pipeline (from `github.com/JonssonLogic/MDViewer`):

1. `qmdPreprocess.ts` strips YAML, converts fenced divs, tabsets, shortcodes, cross-refs
2. `remarkCallouts.ts` transforms `:::note` directives into callout divs
3. `react-markdown` with remark-gfm, remark-math, remark-directive, rehype-katex, rehype-highlight, rehype-slug, rehype-raw
4. Custom components: CodeBlock (language badge + copy), MermaidBlock (diagram rendering), TabsetBlock (interactive tabs)
5. CSS matches Canvas branding colors from `base_handler.py`

### Commenting system

Comments are stored as an HTML comment block at the end of the `.qmd` file. The format is defined in `commentParser.ts`. Comments are invisible to Canvas sync because HTML comments are stripped during rendering. The extension highlights commented text in the preview via DOM manipulation after React renders.
