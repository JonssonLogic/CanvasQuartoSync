import * as vscode from 'vscode';
import * as path from 'path';
import { spawn } from 'child_process';
import { resolvePython, resolveCqsRoot } from '../python/venvResolver';
import { getWorkspaceRoot } from '../config/configLoader';
import { setSyncing } from '../providers/statusBar';

// ── Import from Canvas ───────────────────────────────────────────────
//
// Runs import_from_canvas.py to pull content from Canvas into the
// local workspace. Shows a scope picker first so the user can choose
// what to import, and optionally do a dry run.

const TERMINAL_NAME = 'Canvas Import';

function colorizeLine(line: string): string {
  let clean = line.replace(/\x1b\[[0-9;]*m/g, '');
  clean = clean.replace(/\[\/?\w[\w\s]*\]/g, '');

  if (/\bDEBUG\b/.test(clean)) return `\x1b[90m${clean}\x1b[0m`;
  if (/\bERROR\b/.test(clean)) return `\x1b[31m${clean}\x1b[0m`;
  if (/\bWARNING\b/.test(clean) || /\bSkipping\b/i.test(clean))
    return `\x1b[33m${clean}\x1b[0m`;
  if (/^(Importing|Creating|Writing|Downloading)/i.test(clean.trim()))
    return `\x1b[36m${clean}\x1b[0m`;
  if (/\b(complete|success|done)\b/i.test(clean))
    return `\x1b[32m${clean}\x1b[0m`;
  return clean;
}

export async function importFromCanvas(extensionPath: string): Promise<void> {
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

  // Step 1: Choose what to import
  const scope = await vscode.window.showQuickPick(
    [
      {
        label: '$(package) Full Import',
        description: 'Import all content types',
        value: '',
      },
      {
        label: '$(file) Pages only',
        description: 'Import only wiki pages',
        value: 'pages',
      },
      {
        label: '$(tasklist) Assignments only',
        description: 'Import only assignments',
        value: 'assignments',
      },
      {
        label: '$(question) Quizzes only',
        description: 'Import only quizzes',
        value: 'quizzes',
      },
    ],
    { placeHolder: 'What do you want to import from Canvas?' }
  );

  if (!scope) return;

  // Step 2: Dry run?
  const dryRun = await vscode.window.showQuickPick(
    [
      {
        label: '$(play) Import for real',
        description: 'Download and write files',
        value: false,
      },
      {
        label: '$(eye) Dry run',
        description: 'Show what would be created without writing files',
        value: true,
      },
    ],
    { placeHolder: 'Run mode' }
  );

  if (!dryRun) return;

  // Build arguments
  const cqsRoot = resolveCqsRoot(extensionPath);
  const scriptPath = path.join(cqsRoot, 'import_from_canvas.py');
  const args = [scriptPath, workspaceRoot];

  if (scope.value) {
    args.push('--include', scope.value);
  }
  if (dryRun.value) {
    args.push('--dry-run');
  }

  // Close previous import terminal
  vscode.window.terminals
    .filter((t) => t.name === TERMINAL_NAME)
    .forEach((t) => t.dispose());

  setSyncing(true);

  const progressTitle = dryRun.value
    ? 'Import from Canvas (dry run)'
    : 'Importing from Canvas';

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
              env: { ...process.env, PYTHONIOENCODING: "utf-8" },
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
                  `\x1b[32m✔ Import completed successfully.\x1b[0m\r\n`
                );
                vscode.window.showInformationMessage(
                  'Canvas import completed successfully.'
                );
              } else {
                writeEmitter.fire(
                  `\x1b[31m✖ Import failed (exit code ${code}).\x1b[0m\r\n`
                );
                vscode.window.showErrorMessage(
                  `Canvas import failed (exit code ${code}). See terminal for details.`
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
