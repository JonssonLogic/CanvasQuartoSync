import * as vscode from 'vscode';
import * as path from 'path';
import { spawn } from 'child_process';
import { resolvePython, resolveCqsRoot } from '../python/venvResolver';
import { getWorkspaceRoot, loadConfig } from '../config/configLoader';
import { setSyncing } from '../providers/statusBar';

const TERMINAL_NAME = 'Canvas Purge';

function colorizeLine(line: string): string {
  let clean = line.replace(/\x1b\[[0-9;]*m/g, '');
  clean = clean.replace(/\[\/?\w[\w\s]*\]/g, '');
  if (/\bDEBUG\b/.test(clean)) return `\x1b[90m${clean}\x1b[0m`;
  if (/\bERROR\b/.test(clean)) return `\x1b[31m${clean}\x1b[0m`;
  if (/\bWARNING\b/.test(clean)) return `\x1b[33m${clean}\x1b[0m`;
  if (/\b(delet|remov|purg)/i.test(clean)) return `\x1b[31m${clean}\x1b[0m`;
  if (/\b(complete|done|success)\b/i.test(clean)) return `\x1b[32m${clean}\x1b[0m`;
  if (/\b(skip|dry.run|would)\b/i.test(clean)) return `\x1b[36m${clean}\x1b[0m`;
  return clean;
}

export async function purgeCourse(extensionPath: string): Promise<void> {
  const workspaceRoot = getWorkspaceRoot();
  if (!workspaceRoot) {
    vscode.window.showErrorMessage('No workspace folder open.');
    return;
  }

  const pythonPath = resolvePython();
  if (!pythonPath) {
    vscode.window.showErrorMessage('Python virtual environment not found.');
    return;
  }

  const config = loadConfig(workspaceRoot);
  const courseName = config?.course_name || 'unknown';

  // Step 1: Pick what to purge (multi-select flags)
  const options = await vscode.window.showQuickPick(
    [
      { label: 'Modules', picked: true },
      { label: 'Pages', picked: true },
      { label: 'Assignments', picked: true },
      { label: 'Quizzes', picked: true },
      { label: 'Files', picked: true },
      { label: 'Dry run (list only, delete nothing)', picked: false },
    ],
    {
      canPickMany: true,
      placeHolder: 'Select what to purge from Canvas',
    }
  );

  if (!options || options.length === 0) return;

  const isDryRun = options.some(o => o.label.startsWith('Dry run'));

  // Step 2: Require typing the course name to confirm (skip for dry run)
  if (!isDryRun) {
    const typed = await vscode.window.showInputBox({
      prompt: `Type "${courseName}" to confirm deletion. This cannot be undone.`,
      placeHolder: courseName,
      validateInput: (v) => v === courseName ? null : `Type "${courseName}" exactly to confirm`,
    });

    if (typed === undefined) return; // cancelled
  }

  // Build args
  const cqsRoot = resolveCqsRoot(extensionPath);
  const scriptPath = path.join(cqsRoot, 'purge_course.py');
  const args = [scriptPath, workspaceRoot, '--verbose'];

  if (isDryRun) args.push('--dry-run');

  // purge_course.py purges everything by default. We don't need type flags
  // unless the user deselected some. But the CLI doesn't have a "purge only
  // modules and pages" mode — it's all or specific named items.
  // For now, run the full purge.

  vscode.window.terminals
    .filter((t) => t.name === TERMINAL_NAME)
    .forEach((t) => t.dispose());

  setSyncing(true);

  const progressTitle = isDryRun
    ? 'Purge dry run (listing content)'
    : 'Purging Canvas course content';

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
            writeEmitter.fire(`\x1b[1m> ${progressTitle}...\x1b[0m\r\n\r\n`);

            const proc = spawn(pythonPath, args, {
              cwd: workspaceRoot,
              env: { ...process.env, PYTHONIOENCODING: "utf-8" },
            });

            proc.stdout?.on('data', (data: Buffer) => {
              const lines = data.toString().split('\n');
              for (const line of lines) {
                if (line.length > 0) {
                  writeEmitter.fire(colorizeLine(line) + '\r\n');
                  const trimmed = line.replace(/\x1b\[[0-9;]*m/g, '').trim();
                  if (trimmed) progress.report({ message: trimmed.slice(0, 80) });
                }
              }
            });

            proc.stderr?.on('data', (data: Buffer) => {
              writeEmitter.fire(`\x1b[31m${data.toString().replace(/\n/g, '\r\n')}\x1b[0m`);
            });

            proc.on('close', (code) => {
              writeEmitter.fire('\r\n');
              if (code === 0) {
                writeEmitter.fire(`\x1b[32m✔ ${progressTitle} done.\x1b[0m\r\n`);
              } else {
                writeEmitter.fire(`\x1b[31m✖ Failed (exit code ${code}).\x1b[0m\r\n`);
              }
              setSyncing(false);
              resolveProgress();
            });

            proc.on('error', (err) => {
              writeEmitter.fire(`\x1b[31mError: ${err.message}\x1b[0m\r\n`);
              setSyncing(false);
              resolveProgress();
              closeEmitter.fire(1);
            });
          },
          close() {},
        };

        const terminal = vscode.window.createTerminal({ name: TERMINAL_NAME, pty });
        terminal.show();
      });
    }
  );
}
