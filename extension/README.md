# Canvas Quarto Sync — VS Code Extension

Write your course in Quarto. Preview it live. Sync to Canvas in one click.

## Install (one command)

Open PowerShell and paste:

```powershell
irm https://raw.githubusercontent.com/cenmir/CanvasQuartoSync/main/install.ps1 | iex
```

This installs **everything** — Python (if missing), Quarto, Git, CanvasQuartoSync, and the VS Code extension. It also walks you through setting up your Canvas API credentials.

## Quick Start

1. Open VS Code
2. Click the **graduation cap** icon in the sidebar
3. Click **New Project** — fill in your course name, Canvas course ID, and API token
4. Write content in `.qmd` files
5. Click the **preview icon** in the editor to see a live preview
6. Click **Sync to Canvas** in the status bar to push to Canvas

## Features

- **Sidebar panel** — New Project, Sync, Import, Diff, Preview
- **Sync menu** — Sync All or Sync Current File, with toggle flags (Force, Calendar, Drift)
- **Right-click sync** — Sync a single `.qmd` from editor or file explorer
- **Live QMD preview** — Canvas-matching styling, KaTeX math, code highlighting, Mermaid diagrams, callouts, tabsets
- **Inline comments** — Select text in the preview to add comments (stored in the .qmd file, invisible to Canvas)
- **Import from Canvas** — Pull content from Canvas into local `.qmd` files
- **Diff with Canvas** — Check if content was modified directly on Canvas
- **New Project wizard** — Full-page form to scaffold a new course
- **Color-coded terminal output** — Live sync progress

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

## Example Project

The [Mechatronics course](https://github.com/cenmir/Mechatronics) is the canonical example.

## Development

See [devInstructions.md](devInstructions.md) for build setup, debugging, and project structure.
