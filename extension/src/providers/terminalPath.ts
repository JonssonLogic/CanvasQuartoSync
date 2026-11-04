import * as vscode from 'vscode';
import * as path from 'path';
import * as fs from 'fs';
import { installDir, resolveCqsRoot } from '../python/venvResolver';

/**
 * Puts the cqs command on PATH in VS Code's integrated terminals.
 *
 * The installer adds <install>/bin to the user PATH, but VS Code keeps the
 * environment it started with, so its terminals said "cqs is not recognized"
 * until VS Code was closed completely. The extension's environment collection
 * reaches every new terminal right away (open ones show a relaunch hint).
 */
export function registerTerminalPath(context: vscode.ExtensionContext): void {
  const launcher = process.platform === 'win32' ? 'cqs.cmd' : 'cqs';
  const bins = [path.join(resolveCqsRoot(context.extensionPath), 'bin'), path.join(installDir(), 'bin')];
  const bin = bins.find((b) => fs.existsSync(path.join(b, launcher)));

  const env = context.environmentVariableCollection;
  env.clear();
  if (!bin) {
    return;
  }
  env.description = 'Puts the cqs command on PATH';
  // First, so this install wins over an older cqs elsewhere on PATH.
  env.prepend('PATH', bin + path.delimiter);
}
