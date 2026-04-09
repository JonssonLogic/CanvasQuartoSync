import * as vscode from 'vscode';
import * as path from 'path';
import { spawn } from 'child_process';
import { resolvePython, resolveCqsRoot } from '../python/venvResolver';
import { getWorkspaceRoot } from '../config/configLoader';
import { setSyncing } from '../providers/statusBar';
import { syncOptions } from '../providers/syncOptions';

const TERMINAL_NAME = 'Canvas Sync';

// ── Colorize terminal output by log level ────────────────────────────

function colorizeLine(line: string): string {
  let clean = line.replace(/\x1b\[[0-9;]*m/g, '');
  clean = clean.replace(/\[\/?\w[\w\s]*\]/g, '');

  // ── Verbose mode: lines have explicit log level labels ──
  if (/\bDEBUG\b/.test(clean)) {
    return `\x1b[90m${clean}\x1b[0m`;   // gray
  }
  if (/\bERROR\b/.test(clean)) {
    return `\x1b[31m${clean}\x1b[0m`;    // red
  }

  // ── Non-verbose mode: color by content patterns ──

  // Warnings (group_assignment issues, skipping, etc.)
  if (/\bWARNING\b/.test(clean) || /\bSkipping\b/i.test(clean)) {
    return `\x1b[33m${clean}\x1b[0m`;    // yellow
  }
  // Module headers ("Processing module: ...")
  if (/^Processing module:/.test(clean.trim())) {
    return `\x1b[1;36m${clean}\x1b[0m`;  // bold cyan
  }
  // Status lines (connecting, starting, cleanup, etc.)
  if (/^(Target content|Connecting|Connected|Starting|Skipping calendar)/.test(clean.trim())) {
    return `\x1b[90m${clean}\x1b[0m`;    // gray — setup info
  }
  // Sync actions ("Syncing page:", "Syncing assignment:", "Uploading file:")
  if (/^\s+(Syncing|Uploading)/.test(clean)) {
    return `\x1b[36m${clean}\x1b[0m`;    // cyan
  }
  // Success / completion
  if (/\b(complete|success|done|Sync complete)\b/i.test(clean)) {
    return `\x1b[32m${clean}\x1b[0m`;    // green
  }
  // Indented detail lines (sub-messages like "No changes detected")
  if (/^\s{4,}/.test(clean)) {
    return `\x1b[90m${clean}\x1b[0m`;    // gray — minor detail
  }

  return clean;
}

// ── Sync menu (shown when clicking the status bar button) ────────────

export async function showSyncMenu(extensionPath: string): Promise<void> {
  // Build menu items
  const items: (vscode.QuickPickItem & { id: string })[] = [];

  // Action items at the top
  items.push({
    id: 'syncAll',
    label: '$(cloud-upload) Sync All Files',
    description: 'Sync the entire course to Canvas',
  });

  // Only show "Sync Current File" if a .qmd file is open
  const activeFile = vscode.window.activeTextEditor?.document;
  if (activeFile && activeFile.fileName.endsWith('.qmd')) {
    const fileName = path.basename(activeFile.fileName);
    items.push({
      id: 'syncFile',
      label: '$(file) Sync Current File',
      description: fileName,
    });
  }

  // Separator (using a disabled item with a line)
  items.push({
    id: 'separator',
    label: '',
    kind: vscode.QuickPickItemKind.Separator,
  });

  // Toggle options — checkmark shows which are active
  const check = (on: boolean) => (on ? '$(check)' : '     ');
  items.push({
    id: 'verbose',
    label: `${check(syncOptions.verbose)} Verbose output`,
    description: 'Show detailed debug output',
  });
  items.push({
    id: 'force',
    label: `${check(syncOptions.force)} Force re-render`,
    description: 'Ignore cache, re-render all files',
  });
  items.push({
    id: 'syncCalendar',
    label: `${check(syncOptions.syncCalendar)} Sync calendar`,
    description: 'Include calendar events',
  });
  items.push({
    id: 'checkDrift',
    label: `${check(syncOptions.checkDrift)} Check drift only`,
    description: 'Check for external Canvas edits (no sync)',
  });

  const picked = await vscode.window.showQuickPick(items, {
    placeHolder: 'Choose sync action or toggle options',
  });

  if (!picked) {
    return; // user pressed Escape
  }

  // Handle toggle options — flip the flag and re-show the menu
  switch (picked.id) {
    case 'verbose':
      syncOptions.verbose = !syncOptions.verbose;
      return showSyncMenu(extensionPath);
    case 'force':
      syncOptions.force = !syncOptions.force;
      return showSyncMenu(extensionPath);
    case 'syncCalendar':
      syncOptions.syncCalendar = !syncOptions.syncCalendar;
      return showSyncMenu(extensionPath);
    case 'checkDrift':
      syncOptions.checkDrift = !syncOptions.checkDrift;
      return showSyncMenu(extensionPath);
    case 'syncAll':
      return runSync(extensionPath, undefined);
    case 'syncFile':
      return runSync(extensionPath, activeFile!.fileName);
  }
}

// ── Sync a single file (called from right-click or command) ──────────

export async function syncFile(
  extensionPath: string,
  fileUri?: vscode.Uri
): Promise<void> {
  // fileUri comes from right-click context menu; fall back to active editor
  const filePath =
    fileUri?.fsPath ?? vscode.window.activeTextEditor?.document.fileName;

  if (!filePath || !filePath.endsWith('.qmd')) {
    vscode.window.showWarningMessage('No .qmd file selected.');
    return;
  }

  return runSync(extensionPath, filePath);
}

// ── Core sync execution ─────────────────────────────────────────────

async function runSync(
  extensionPath: string,
  singleFile?: string
): Promise<void> {
  const workspaceRoot = getWorkspaceRoot();
  if (!workspaceRoot) {
    vscode.window.showErrorMessage('No workspace folder open.');
    return;
  }

  const pythonPath = resolvePython();
  if (!pythonPath) {
    const action = await vscode.window.showErrorMessage(
      'Python virtual environment not found. Run install.ps1 first, or configure cqs.pythonVenvPath in settings.',
      'Open Settings'
    );
    if (action === 'Open Settings') {
      vscode.commands.executeCommand(
        'workbench.action.openSettings',
        'cqs.pythonVenvPath'
      );
    }
    return;
  }

  const cqsRoot = resolveCqsRoot(extensionPath);
  const scriptPath = path.join(cqsRoot, 'sync_to_canvas.py');

  // Build the argument list based on current options
  const args = [scriptPath, workspaceRoot];

  if (singleFile) {
    // --only expects a relative path from the content dir
    const relativePath = path.relative(workspaceRoot, singleFile);
    args.push('--only', relativePath);
  }
  if (syncOptions.verbose) {
    args.push('--verbose');
  }
  if (syncOptions.force) {
    args.push('--force');
  }
  if (syncOptions.syncCalendar) {
    args.push('--sync-calendar');
  }
  if (syncOptions.checkDrift) {
    args.push('--check-drift');
  }

  // Close any previous sync terminal
  vscode.window.terminals
    .filter((t) => t.name === TERMINAL_NAME)
    .forEach((t) => t.dispose());

  setSyncing(true);

  // Build a description for the progress popup
  const syncTarget = singleFile ? path.basename(singleFile) : 'all files';
  const progressTitle = syncOptions.checkDrift
    ? `Checking drift (${syncTarget})`
    : `Syncing ${syncTarget} to Canvas`;

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
            // Show what command is being run
            const flagList = args.slice(2).join(' ');
            writeEmitter.fire(
              `\x1b[1m> ${progressTitle}\x1b[0m\r\n`
            );
            if (flagList) {
              writeEmitter.fire(`\x1b[90m  Flags: ${flagList}\x1b[0m\r\n`);
            }
            writeEmitter.fire('\r\n');

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
                  if (/\bINFO\b/.test(clean)) {
                    const msg = clean.replace(/^.*?\bINFO\s+/, '').trim();
                    if (msg) {
                      progress.report({ message: msg });
                    }
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
                  `\x1b[32m✔ ${progressTitle} — done.\x1b[0m\r\n`
                );
                vscode.window.showInformationMessage(
                  `${progressTitle} — completed successfully.`
                );
              } else {
                writeEmitter.fire(
                  `\x1b[31m✖ Failed (exit code ${code}).\x1b[0m\r\n`
                );
                vscode.window.showErrorMessage(
                  `Sync failed (exit code ${code}). See terminal for details.`
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
          close() {
            // Terminal was closed by user
          },
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
