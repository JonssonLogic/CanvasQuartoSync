# Developer Instructions

## Prerequisites

- [Node.js](https://nodejs.org/) (v18 or later)
- [VS Code](https://code.visualstudio.com/)
- Python venv with CanvasQuartoSync installed (see `install.ps1` in the parent folder)

## First-time setup

```bash
cd extension
npm install
```

## Building

The extension has two build targets:

| Command | What it builds |
|---------|----------------|
| `npm run build:extension` | Extension host code (`src/` → `dist/extension.js`) |
| `npm run build` | Both extension host and webview |

After changing any `.ts` file, you need to rebuild before the changes take effect.

## Running the extension (development)

### Option 1: F5 (recommended during development)

1. Open the `extension/` folder in VS Code
2. Press **F5**
3. A second VS Code window opens with the TestCourse workspace loaded
4. The extension is active in that second window — test it there
5. The Debug Console (in the first window) shows `console.log` output and errors

**After code changes:**
- **Ctrl+Shift+F5** — restarts the debug session (rebuilds + reloads automatically)
- Or **Shift+F5** to stop, then **F5** to start fresh

The workspace that opens is configured in `.vscode/launch.json` (the second argument in `args`).

### Option 2: Command line launch

Same as F5 but from a terminal:

```bash
npm run build:extension
code --extensionDevelopmentPath="d:\Dropbox\Python\CanvasQuartoSyncDev\CanvasQuartoSync\extension" "d:\Dropbox\Python\CanvasQuartoSyncDev\TestCourse"
```

### Option 3: Symlink into VS Code extensions folder

Installs the extension into your regular VS Code (no second window). Run as admin:

```cmd
mklink /D "%USERPROFILE%\.vscode\extensions\canvasquartosync" "d:\Dropbox\Python\CanvasQuartoSyncDev\CanvasQuartoSync\extension"
```

After code changes: rebuild, then **Ctrl+Shift+P** → "Developer: Reload Window".

## Packaging for distribution

To create a `.vsix` file (an installable package you can share):

```bash
npm install -g @vscode/vsce   # one-time
vsce package                  # creates canvasquartosync-0.1.0.vsix
```

To install the `.vsix` in VS Code:
- **Ctrl+Shift+P** → "Extensions: Install from VSIX..." → select the file

## Project structure

```
extension/
├── .vscode/
│   ├── launch.json          # F5 debug configuration
│   └── tasks.json           # Pre-launch build task
├── src/                     # Extension host code (runs in Node.js)
│   ├── extension.ts         # Entry point — registers commands + status bar
│   ├── commands/            # One file per command
│   ├── config/              # Config loading (config.toml)
│   ├── providers/           # Status bar, preview panel
│   ├── python/              # Python venv resolution + script runner
│   └── utils/               # File watcher, webview messaging
├── webview/                 # Webview UI code (runs in browser/React)
├── dist/                    # Build output (gitignored)
├── package.json             # Extension manifest (commands, settings, etc.)
├── esbuild.mjs              # Bundles src/ → dist/extension.js
├── vite.config.ts           # Bundles webview/ → dist/webview/
├── tsconfig.json            # TypeScript config for extension host
└── tsconfig.webview.json    # TypeScript config for webview
```

## Sync features

### Sync menu (status bar button)

Clicking the "Sync to Canvas" button in the status bar opens a menu:

```
$(cloud-upload) Sync All Files          — sync the entire course
$(file)         Sync Current File       — sync just the open .qmd file
─────────────────────────────────────────
✓ Verbose output                        — show detailed debug output
  Force re-render                       — ignore cache, re-render everything
  Sync calendar                         — include calendar events
  Check drift only                      — check for external Canvas edits (no sync)
```

- **Toggle options**: Click an option to turn it on/off (checkmark = active). The menu re-appears so you can toggle multiple options before syncing.
- **Options are remembered** for the session — if you turn on "Force", it stays on until you turn it off.
- **Sync Current File** only appears when you have a `.qmd` file open.

### Right-click context menus

- **Right-click a .qmd file in the file explorer** → "Sync This File to Canvas"
- **Right-click inside a .qmd file in the editor** → "Sync This File to Canvas" and "Sync to Canvas" (full sync menu)

### Editor title bar icon

When a `.qmd` file is open, a small upload icon appears in the top-right corner of the editor tab bar. Clicking it syncs just that file.

### Terminal output

All sync operations open a "Canvas Sync" terminal at the bottom showing live output:

- **Gray** = DEBUG (less important details)
- **Cyan** = INFO (normal progress)
- **Yellow** = WARNING (something to notice)
- **Red** = ERROR (something went wrong)

A notification popup also appears in the bottom-right showing the latest status line with a spinner.

### Underlying Python flags

The sync options map to `sync_to_canvas.py` CLI flags:

| Menu option | Python flag | What it does |
|-------------|-------------|--------------|
| Verbose output | `--verbose` | Shows DEBUG-level log lines |
| Force re-render | `--force` | Ignores cached modification times, re-renders all files |
| Sync calendar | `--sync-calendar` | Also syncs calendar events (opt-in) |
| Check drift only | `--check-drift` | Compares local vs Canvas content without syncing |
| Sync Current File | `--only <path>` | Syncs a single .qmd file instead of the whole course |

## Key concepts

- **Extension host**: The Node.js side (`src/`). It has access to the VS Code API — commands, terminals, file system, settings, etc.
- **Webview**: The browser side (`webview/`). A sandboxed HTML/React panel inside VS Code. Communicates with the extension host via `postMessage`.
- **Commands**: Actions the user can trigger via the Command Palette (Ctrl+Shift+P) or the status bar button. Defined in `package.json` under `contributes.commands`.
- **Pseudo-terminal**: A terminal panel that we control programmatically. The sync command uses this to show live Python output with colored log levels.
