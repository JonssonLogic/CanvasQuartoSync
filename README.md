# Canvas Quarto Sync

Write your course in Quarto. Preview it live. Sync to Canvas in one click.

Manage your entire course as a local Git repository and keep Canvas in sync for students.

## Install

**Windows** (PowerShell):
```powershell
irm https://raw.githubusercontent.com/JonssonLogic/CanvasQuartoSync/main/install.ps1 | iex
```

**Linux / macOS** (Terminal):
```bash
curl -fsSL https://raw.githubusercontent.com/JonssonLogic/CanvasQuartoSync/main/install.sh | bash
```

Both installers let you select which components to install (Python, Git, Quarto, venv, repo clone, VS Code extension). Deselect anything you already have. Restart VS Code after install.

The tool installs to one folder, with its Python environment inside: `%LOCALAPPDATA%\CanvasQuartoSync` on Windows, `~/Library/Application Support/CanvasQuartoSync` on macOS, `~/.local/share/canvasquartosync` on Linux. Deleting that folder uninstalls it. To use a clone of the repo instead (for development), set `CANVAS_QUARTO_SYNC_DIR` to the clone; it needs its own `.venv`.

## Quick Start

1. Open **VS Code**
2. Click the **graduation cap** icon in the sidebar
3. Click **New Project**: enter your course name, Canvas course ID, and API URL
4. Write content in `.qmd` files
5. Click **Sync to Canvas** in the status bar

## Working on a Course Together

Several teachers can publish one course. The course lives in a private GitHub
repository, everyone works in their own clone, and each sync publishes what is
on GitHub.

**Set up the course (once, by the course owner):**

1. Open the course folder in VS Code.
2. In the sidebar's Project panel, click **Create GitHub Repository**, pick the
   organisation and confirm. Or in a terminal: `cqs github --org <organisation>`.
3. Invite the other teachers: **Invite Teacher to GitHub**, or
   `cqs github invite <github-username>`.

**Join a course (each teacher):**

1. Accept the invitation to the organisation (sent by e-mail).
2. In VS Code: **Clone Repository**, and pick the course.
3. Set the environment variable `CANVAS_API_TOKEN` to your own Canvas token.
4. Ask for a teacher role in the Canvas course.

Then the daily routine: pull, edit, commit and push, sync. The sync refuses to
run with uncommitted or unpushed changes, takes a lock so two people cannot sync
at once, and never overwrites a page someone edited directly in Canvas. More in
the [User Guide](Guides/Canvas_Sync_User_Guide.md#team-mode-several-teachers-one-course);
the design is in [Team Mode](Guides/Team_Mode.md).

## Setting Up Canvas Credentials

You need a Canvas API token to sync. Two options:

**Option A: Token file** (recommended): Save your token to a text file (e.g. `C:\Users\you\privateCanvasToken`), then set the path in `config.toml`:

```toml
canvas_token_path = "C:/Users/you/privateCanvasToken"
```

**Option B: Environment variable**: Set `CANVAS_API_TOKEN` in PowerShell:

```powershell
setx CANVAS_API_TOKEN "your_token_here"
```

To generate a token: Canvas → Account → Settings → New Access Token.

## How It Works

```
MyCourse/
├── config.toml                    # Course settings + Canvas API config
├── _quarto.yml                    # Quarto rendering config
├── _modules.yml                   # Module order: Course_Info, Introduction, Fundamentals
├── Course_Info/                   # → Canvas module "Course_Info"
│   └── StudyGuide.qmd             #   → Study guide + PDF export
├── Introduction/                  # → Canvas module "Introduction"
│   ├── _order.yml                 #   Item order (optional)
│   ├── Welcome.qmd                #   → Canvas page
│   ├── Lab_Setup.qmd              #   → Canvas assignment
│   └── graphics/                  #   → Images (auto-uploaded)
└── Fundamentals/                  # → Canvas module "Fundamentals"
    ├── Theory.qmd                 #   → Canvas page
    └── Quiz.json                  #   → Canvas quiz
```

- **Folders** listed in `_modules.yml` become Canvas **modules**, in that order
- **Files** with a `canvas.type` become **module items**, in `_order.yml` order, then the
  rest; PDFs and other files when `_order.yml` lists them
- Reorder by dragging in the **Course Board**, which rewrites the order files
- Courses made before order files use `NN_` prefixes (`01_Introduction/01_Welcome.qmd`);
  `cqs migrate` converts them
- **YAML frontmatter** in each `.qmd` controls the Canvas type and settings
- **Images and PDFs** are auto-uploaded and linked
- **Cross-references** between `.qmd` files are resolved to Canvas URLs

## Config Reference

`config.toml` in the course root:

```toml
course_id = 12345
course_name = "Mechatronics"
course_code = "TMRK16"
credits = "7.5 ECTS"
semester = "Spring 2026"
canvas_api_url = "https://canvas.university.edu/api/v1"
canvas_token_path = "C:/Users/you/privateCanvasToken"
language = "english"
```

## Frontmatter Examples

**Page:**
```yaml
---
title: "Welcome"
canvas:
  type: page
  published: true
---
```

**Assignment:**
```yaml
---
title: "Lab 1: LED Circuit"
canvas:
  type: assignment
  published: true
  points: 10
  due_at: "2024-05-10T23:59:00"   # course-local time; DST handled at sync
  submission_types: [online_upload]
  allowed_extensions: [pdf]
---
```

**Study guide with PDF export:**
```yaml
---
title: "Course PM"
canvas:
  type: study_guide
  preprocess: true
  published: true
  pdf:
    target_module: "Course Documents"
    filename: "KursPM.pdf"
---
```

## Authoring with an AI Assistant

Course content is usually written in a separate folder, with an AI assistant open on it,
which by default knows nothing about this tool's conventions. Scaffold that folder once:

```powershell
cqs init C:\Courses\MECH201
```

This installs a Claude Code skill plus reference documentation into the folder, so a
fresh session starts out knowing how modules and their order are declared, the `canvas.*` frontmatter
schema, and the quiz syntax, without reading this repository. Content is checked with
an offline validator: double-click `check_content.bat` in the folder, or from a
terminal:

```powershell
cqs check Introduction\Welcome.qmd
```

There are three ways to run the tool: the VS Code extension (recommended), the
double-click `.bat` files on Windows, and the `cqs` command in a terminal on any OS.
See [Three Ways to Run the Tool](Guides/Canvas_Sync_User_Guide.md#6-three-ways-to-run-the-tool).

It needs no Canvas connection and reports what each file will become in Canvas, plus the
mistakes that otherwise only surface after a sync: files the sync would skip, misspelled
settings, broken image paths, cross-references that would render as `?@fig-x`, quiz
questions that will not grade.

The kit instructs assistants never to sync. Pushing to a live course stays your call.
Full details in the [User Guide](Guides/Canvas_Sync_User_Guide.md#8-authoring-with-an-ai-assistant).

## VS Code Extension Features

The extension adds a full GUI on top of the Python sync tool:

- **Sidebar panel**: New Project, Sync, Import, Diff, Preview
- **Live QMD preview**: Canvas-matching styling with math, code highlighting, Mermaid diagrams, callouts
- **Sync menu**: Sync All or Sync Current File, with Force/Calendar/Drift toggles and a **Cancel** button to abort mid-sync
- **Right-click sync**: Sync a single file from editor or file explorer
- **Inline comments**: Select text in preview to add review comments
- **Import from Canvas**: Pull existing Canvas content into local `.qmd` files
- **Diff with Canvas**: Check if someone edited content directly on Canvas
- **New Project wizard**: Full-page form to scaffold a new course
- **Course Board**: the course in one view, local order side-by-side with Canvas
  - **Drag to reorder** modules and items, or move an item to another module; the next sync applies it in Canvas
  - **Opens without Canvas**: a missing `course_id`, token or network shows a banner with what to fix, plus **Open config.toml** and **Retry**
  - **Sync a whole module**, including one Canvas does not have yet (*Create in Canvas*)
  - **Converts `NN_` courses** to order files after showing a preview
  - **Create modules** directly from the board (Draft or Published)
  - **Create new items** (Page, Assignment, Quiz, External URL) inside any module, with auto-suffixed titles (`New Page`, `New Page 2`, …)
  - **Publish/unpublish toggles** on every module and item
  - **Batch delete** modules and items via per-row checkboxes with a modal confirm dialog (removes Canvas content and matched local files)
  - **Sticky header** with quick Refresh and `+ New Module` actions

## CLI Usage

You can also use the Python tools directly:

```bash
python sync_to_canvas.py ./MyCourse              # Sync everything
python sync_to_canvas.py ./MyCourse --only Intro/Welcome.qmd  # Sync one file
python sync_to_canvas.py ./MyCourse --force       # Re-render all (ignore cache)
python sync_to_canvas.py ./MyCourse --check-drift # Check for external edits
python sync_to_canvas.py ./MyCourse --sync-calendar # Include calendar events
python import_from_canvas.py ./output             # Import from Canvas
```

Or, from anywhere inside a course folder, `cqs` (put on the PATH by the
installer): `cqs check`, `cqs sync`, `cqs diff`, `cqs migrate`, `cqs github --org <org>`,
`cqs github invite <user>`. Run `cqs` for the full list.

## Converting a Course from `NN_` Prefixes

Courses made before 0.15 order modules with `NN_` prefixes in folder and file names.
They keep working unchanged. To reorder such a course by dragging in the Course Board,
convert it once to order files (`_modules.yml`, `_order.yml`):

- **In VS Code**: the Course Board offers it when it opens the course. *Preview
  conversion* lists every rename and link fix before anything changes.
- **In a terminal**: `cqs migrate --dry-run` to preview, then `cqs migrate`.

It is local only: Canvas sees the same module names and item titles, and the sync map
moves along with the renamed files, so every Canvas id is kept. Commit first; the
conversion refuses to run on uncommitted changes, so it lands as one commit of its own.
In a team course, have everyone push first and pull afterwards.

## Example Project

The [Mechatronics course](https://github.com/cenmir/Mechatronics) is a real-world example showing the full folder structure, frontmatter conventions, and content types.

## Documentation

- [Changelog](CHANGELOG.md): What changed in each release
- [User Guide](Guides/Canvas_Sync_User_Guide.md): Full documentation on all features
- [Canvas Token Setup](Guides/Canvas_token_setup.md): How to generate an API token
- [Team Mode](Guides/Team_Mode.md): Several teachers publishing one course from one git repo, and what happens when someone edits Canvas directly
- [Extension Dev Guide](extension/devInstructions.md): Building and debugging the extension

## License

[MIT](LICENSE)
