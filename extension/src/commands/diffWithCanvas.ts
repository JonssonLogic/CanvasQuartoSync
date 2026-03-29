import * as vscode from 'vscode';
import * as path from 'path';
import { spawn } from 'child_process';
import { resolvePython, resolveCqsRoot } from '../python/venvResolver';
import { getWorkspaceRoot } from '../config/configLoader';
import { setSyncing } from '../providers/statusBar';

// ── Diff with Canvas ─────────────────────────────────────────────────
//
// Runs sync_to_canvas.py with --check-drift --show-diff to compare
// local content against what's currently on Canvas. This tells you
// if someone edited content directly on Canvas since your last sync.
//
// Two modes:
//   - Check all files (full drift check)
//   - Check a single file (--only <file>)

const TERMINAL_NAME = 'Canvas Drift Check';

function colorizeLine(line: string): string {
  let clean = line.replace(/\x1b\[[0-9;]*m/g, '');
  clean = clean.replace(/\[\/?\w[\w\s]*\]/g, '');

  if (/\bDEBUG\b/.test(clean)) return `\x1b[90m${clean}\x1b[0m`;
  if (/\bERROR\b/.test(clean)) return `\x1b[31m${clean}\x1b[0m`;
  if (/\bWARNING\b/.test(clean)) return `\x1b[33m${clean}\x1b[0m`;
  // Drift detected lines
  if (/\bdrift\b/i.test(clean) || /\bmodified\b/i.test(clean) || /\bchanged\b/i.test(clean))
    return `\x1b[33m${clean}\x1b[0m`;
  // Diff output: + lines green, - lines red
  if (/^\+/.test(clean.trim()) && !/^\+\+\+/.test(clean.trim()))
    return `\x1b[32m${clean}\x1b[0m`;
  if (/^-/.test(clean.trim()) && !/^---/.test(clean.trim()))
    return `\x1b[31m${clean}\x1b[0m`;
  if (/^@@/.test(clean.trim()))
    return `\x1b[36m${clean}\x1b[0m`;
  // No drift
  if (/\bno drift\b/i.test(clean) || /\bin sync\b/i.test(clean) || /\bno changes\b/i.test(clean))
    return `\x1b[32m${clean}\x1b[0m`;
  if (/^Processing module:/i.test(clean.trim()))
    return `\x1b[1;36m${clean}\x1b[0m`;

  return clean;
}

export async function diffWithCanvas(extensionPath: string): Promise<void> {
  const workspaceRoot = getWorkspaceRoot();
  if (!workspaceRoot) {
    vscode.window.showErrorMessage('No workspace folder open.');
    return;
  }

  const pythonPath = resolvePython();
  if (!pythonPath) {
    vscode.window.showErrorMessage(
      'Python virtual environment not found. Run install.ps1 first.'
    );
    return;
  }

  // Choose scope
  const items: (vscode.QuickPickItem & { value: string })[] = [
    {
      label: '$(search) Check all files',
      description: 'Compare all synced content with Canvas',
      value: 'all',
    },
  ];

  // Offer single-file check if a .qmd is open
  const activeFile = vscode.window.activeTextEditor?.document;
  if (activeFile && activeFile.fileName.endsWith('.qmd')) {
    const fileName = path.basename(activeFile.fileName);
    items.push({
      label: `$(file) Check ${fileName}`,
      description: 'Compare just this file with Canvas',
      value: activeFile.fileName,
    });
  }

  const picked = await vscode.window.showQuickPick(items, {
    placeHolder: 'What do you want to compare with Canvas?',
  });

  if (!picked) return;

  const cqsRoot = resolveCqsRoot(extensionPath);
  const scriptPath = path.join(cqsRoot, 'sync_to_canvas.py');
  const args = [scriptPath, workspaceRoot, '--check-drift', '--show-diff'];

  if (picked.value !== 'all') {
    const relativePath = path.relative(workspaceRoot, picked.value);
    args.push('--only', relativePath);
  }

  // Close previous drift terminal
  vscode.window.terminals
    .filter((t) => t.name === TERMINAL_NAME)
    .forEach((t) => t.dispose());

  setSyncing(true);

  const progressTitle =
    picked.value === 'all'
      ? 'Checking drift (all files)'
      : `Checking drift (${path.basename(picked.value)})`;

  vscode.window.withProgress(
    {
      location: vscode.ProgressLocation.Notification,
      title: progressTitle,
      cancellable: false,
    },
    (progress) => {
      return new Promise<void>((resolveProgress) => {
        const writeEmitter = new vscode.EventEmitter<string>();
        const closeEmitter = new vscode.EventEmitter<number | void>();

        const pty: vscode.Pseudoterminal = {
          onDidWrite: writeEmitter.event,
          onDidClose: closeEmitter.event,
          open() {
            writeEmitter.fire(
              `\x1b[1m> ${progressTitle}...\x1b[0m\r\n\r\n`
            );

            const proc = spawn(pythonPath, args, {
              cwd: workspaceRoot,
              env: { ...process.env },
            });

            proc.stdout?.on('data', (data: Buffer) => {
              const lines = data.toString().split('\n');
              for (const line of lines) {
                if (line.length > 0) {
                  writeEmitter.fire(colorizeLine(line) + '\r\n');

                  const clean = line
                    .replace(/\x1b\[[0-9;]*m/g, '')
                    .replace(/\[\/?\w[\w\s]*\]/g, '');
                  const trimmed = clean.trim();
                  if (trimmed && !/^\s*$/.test(trimmed)) {
                    progress.report({ message: trimmed.slice(0, 80) });
                  }
                }
              }
            });

            proc.stderr?.on('data', (data: Buffer) => {
              const text = data.toString().replace(/\n/g, '\r\n');
              writeEmitter.fire(`\x1b[31m${text}\x1b[0m`);
            });

            proc.on('close', (code) => {
              writeEmitter.fire('\r\n');
              if (code === 0) {
                writeEmitter.fire(
                  `\x1b[32m✔ Drift check completed.\x1b[0m\r\n`
                );
                vscode.window.showInformationMessage(
                  'Drift check completed. See terminal for results.'
                );
              } else {
                writeEmitter.fire(
                  `\x1b[31m✖ Drift check failed (exit code ${code}).\x1b[0m\r\n`
                );
                vscode.window.showErrorMessage(
                  `Drift check failed (exit code ${code}). See terminal for details.`
                );
              }
              setSyncing(false);
              resolveProgress();
            });

            proc.on('error', (err) => {
              writeEmitter.fire(
                `\x1b[31mError: ${err.message}\x1b[0m\r\n`
              );
              setSyncing(false);
              resolveProgress();
              closeEmitter.fire(1);
            });
          },
          close() {},
        };

        const terminal = vscode.window.createTerminal({
          name: TERMINAL_NAME,
          pty,
        });
        terminal.show();
      });
    }
  );
}
