# Canvas Quarto Sync

Write your course in Quarto. Preview it live. Sync to Canvas in one click.

Manage your entire course as a local Git repository and keep Canvas in sync for students.

## Install

**Windows** (PowerShell):
```powershell
irm https://raw.githubusercontent.com/cenmir/CanvasQuartoSync/main/install.ps1 | iex
```

**Linux / macOS** (Terminal):
```bash
curl -fsSL https://raw.githubusercontent.com/cenmir/CanvasQuartoSync/main/install.sh | bash
```

Both installers let you select which components to install (Python, Git, Quarto, venv, repo clone, VS Code extension). Deselect anything you already have. Restart VS Code after install.

## Quick Start

1. Open **VS Code**
2. Click the **graduation cap** icon in the sidebar
3. Click **New Project**: enter your course name, Canvas course ID, and API URL
4. Write content in `.qmd` files
5. Click **Sync to Canvas** in the status bar

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
├── 01_Introduction/               # → Canvas module "Introduction"
│   ├── 01_Welcome.qmd            #   → Canvas page
│   ├── 02_Lab_Setup.qmd          #   → Canvas assignment
│   └── graphics/                  #   → Images (auto-uploaded)
├── 02_Fundamentals/               # → Canvas module "Fundamentals"
│   ├── 01_Theory.qmd             #   → Canvas page
│   └── 02_Quiz.json              #   → Canvas quiz
└── 01_Course_Info/
    └── 01_StudyGuide.qmd          #   → Study guide + PDF export
```

- **Folders** starting with `NN_` become Canvas **modules**
- **Files** starting with `NN_` become **module items** (pages, assignments, quizzes)
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
  due_at: 2026-04-15T23:59:00Z
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

## VS Code Extension Features

The extension adds a full GUI on top of the Python sync tool:

- **Sidebar panel**: New Project, Sync, Import, Diff, Preview
- **Live QMD preview**: Canvas-matching styling with math, code highlighting, Mermaid diagrams, callouts
- **Sync menu**: Sync All or Sync Current File, with Force/Calendar/Drift toggles
- **Right-click sync**: Sync a single file from editor or file explorer
- **Inline comments**: Select text in preview to add review comments
- **Import from Canvas**: Pull existing Canvas content into local `.qmd` files
- **Diff with Canvas**: Check if someone edited content directly on Canvas
- **New Project wizard**: Full-page form to scaffold a new course

## CLI Usage

You can also use the Python tools directly:

```bash
python sync_to_canvas.py ./MyCourse              # Sync everything
python sync_to_canvas.py ./MyCourse --only 01_Intro/01_Welcome.qmd  # Sync one file
python sync_to_canvas.py ./MyCourse --force       # Re-render all (ignore cache)
python sync_to_canvas.py ./MyCourse --check-drift # Check for external edits
python sync_to_canvas.py --sync-calendar          # Include calendar events
python import_from_canvas.py ./output             # Import from Canvas
```

## Example Project

The [Mechatronics course](https://github.com/cenmir/Mechatronics) is a real-world example showing the full folder structure, frontmatter conventions, and content types.

## Documentation

- [User Guide](Guides/Canvas_Sync_User_Guide.md): Full documentation on all features
- [Canvas Token Setup](Guides/Canvas_token_setup.md): How to generate an API token
- [Extension Dev Guide](extension/devInstructions.md): Building and debugging the extension

## License

[MIT](LICENSE)
