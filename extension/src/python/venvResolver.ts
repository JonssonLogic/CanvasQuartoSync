import * as vscode from 'vscode';
import * as path from 'path';
import * as fs from 'fs';
import * as os from 'os';

/**
 * Where install.ps1 / install.sh put the tool, with its venv inside as .venv.
 * The course-folder launchers (check_content, update_kit, run_sync_here) look
 * in the same places in the same order; keep them in step.
 */
export function installDir(): string {
  if (process.platform === 'win32') {
    const localAppData = process.env.LOCALAPPDATA || path.join(os.homedir(), 'AppData', 'Local');
    return path.join(localAppData, 'CanvasQuartoSync');
  }
  if (process.platform === 'darwin') {
    return path.join(os.homedir(), 'Library', 'Application Support', 'CanvasQuartoSync');
  }
  const dataHome = process.env.XDG_DATA_HOME || path.join(os.homedir(), '.local', 'share');
  return path.join(dataHome, 'canvasquartosync');
}

/** A dev clone to use instead of the install, as the launchers honour it. */
function devCloneDir(): string | undefined {
  return process.env.CANVAS_QUARTO_SYNC_DIR || undefined;
}

/**
 * Resolves the path to the Python executable inside the CanvasQuartoSync venv.
 *
 * Resolution order:
 * 1. cqs.pythonVenvPath setting
 * 2. CANVAS_QUARTO_VENV environment variable
 * 3. CANVAS_QUARTO_SYNC_DIR/.venv (dev clone)
 * 4. <install dir>/.venv
 * 5. ~/.venvs/canvas_quarto_env/  (previous layout)
 * 6. ~/venvs/canvas_quarto_env/   (oldest layout)
 * 7. Workspace-local .venv/
 */
export function resolvePython(): string | undefined {
  const candidates: string[] = [];

  // 1. Extension setting
  const settingPath = vscode.workspace
    .getConfiguration('cqs')
    .get<string>('pythonVenvPath');
  if (settingPath) {
    candidates.push(settingPath);
  }

  // 2. Environment variable
  const envPath = process.env.CANVAS_QUARTO_VENV;
  if (envPath) {
    candidates.push(envPath);
  }

  // 3. Dev clone
  const clone = devCloneDir();
  if (clone) {
    candidates.push(path.join(clone, '.venv'));
  }

  // 4. Install location
  candidates.push(path.join(installDir(), '.venv'));

  // 5-6. Previous layouts
  candidates.push(path.join(os.homedir(), '.venvs', 'canvas_quarto_env'));
  candidates.push(path.join(os.homedir(), 'venvs', 'canvas_quarto_env'));

  // 7. Workspace-local .venv
  const workspaceFolders = vscode.workspace.workspaceFolders;
  if (workspaceFolders) {
    candidates.push(path.join(workspaceFolders[0].uri.fsPath, '.venv'));
  }

  for (const venvDir of candidates) {
    const pythonPath = getPythonInVenv(venvDir);
    if (pythonPath && fs.existsSync(pythonPath)) {
      return pythonPath;
    }
  }

  return undefined;
}

function getPythonInVenv(venvDir: string): string {
  if (process.platform === 'win32') {
    return path.join(venvDir, 'Scripts', 'python.exe');
  }
  return path.join(venvDir, 'bin', 'python3');
}

/**
 * Resolves the path to the CanvasQuartoSync repo root.
 *
 * Resolution order:
 * 1. CANVAS_QUARTO_SYNC_DIR (dev clone)
 * 2. The install location (see installDir)
 * 3. ~/CanvasQuartoSync/ (previous layout)
 * 4. Parent of extensionPath (dev: extension lives at repo/extension/)
 * 5. ~/venvs/canvas_quarto_env/CanvasQuartoSync/ (oldest layout)
 */
export function resolveCqsRoot(extensionPath: string): string {
  const clone = devCloneDir();
  const candidates = [
    ...(clone ? [clone] : []),
    installDir(),
    path.join(os.homedir(), 'CanvasQuartoSync'),
    path.dirname(extensionPath),
    path.join(os.homedir(), 'venvs', 'canvas_quarto_env', 'CanvasQuartoSync'),
  ];

  for (const candidate of candidates) {
    if (fs.existsSync(path.join(candidate, 'sync_to_canvas.py'))) {
      return candidate;
    }
  }

  // Fallback to the install location even if not yet installed
  return installDir();
}
