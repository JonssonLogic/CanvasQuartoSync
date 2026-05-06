import * as vscode from 'vscode';
import * as path from 'path';
import * as fs from 'fs';
import * as os from 'os';

/**
 * Resolves the path to the Python executable inside the CanvasQuartoSync venv.
 *
 * Resolution order:
 * 1. cqs.pythonVenvPath setting
 * 2. CANVAS_QUARTO_VENV environment variable
 * 3. ~/.venvs/canvas_quarto_env/
 * 4. ~/venvs/canvas_quarto_env/  (legacy)
 * 5. Workspace-local .venv/
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

  // 3. Default location (current)
  candidates.push(path.join(os.homedir(), '.venvs', 'canvas_quarto_env'));

  // 4. Legacy location
  candidates.push(path.join(os.homedir(), 'venvs', 'canvas_quarto_env'));

  // 5. Workspace-local .venv
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
 * 1. ~/CanvasQuartoSync/ (default install location)
 * 2. Parent of extensionPath (dev: extension lives at repo/extension/)
 * 3. Legacy ~/venvs/canvas_quarto_env/CanvasQuartoSync/
 */
export function resolveCqsRoot(extensionPath: string): string {
  const candidates = [
    path.join(os.homedir(), 'CanvasQuartoSync'),
    path.dirname(extensionPath),
    path.join(os.homedir(), 'venvs', 'canvas_quarto_env', 'CanvasQuartoSync'),
  ];

  for (const candidate of candidates) {
    if (fs.existsSync(path.join(candidate, 'sync_to_canvas.py'))) {
      return candidate;
    }
  }

  // Fallback to default location even if not yet cloned
  return candidates[0];
}
