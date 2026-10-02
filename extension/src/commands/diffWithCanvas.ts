import * as vscode from 'vscode';
import * as path from 'path';
import { spawn } from 'child_process';
import { resolvePython, resolveCqsRoot } from '../python/venvResolver';
import { getWorkspaceRoot } from '../config/configLoader';
import { setSyncing } from '../providers/statusBar';

// ── Diff with Canvas ─────────────────────────────────────────────────
//
// Runs sync_to_canvas.py with --check-drift --json to get
// structured drift results, then opens VS Code's built-in diff editor
// for each drifted file showing Canvas (left) vs Local (right).

interface DriftItem {
  file: string;
  type: string;
  title: string;
  // Written only when the drift check built diffs. Absent is a real case, so
  // it is optional here and checked before use.
  canvas_qmd_path?: string;
  local_path: string;
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

  await runDriftCheck(extensionPath, picked.value === 'all' ? null : picked.value);
}

/**
 * Compare one file with Canvas and open the diff, with no picker.
 *
 * The Module Structure panel knows which row was clicked, so asking again
 * would be asking a question the user already answered.
 *
 * `relPath` is relative to the workspace root, the form the panel already
 * carries and the form --only expects.
 */
export async function diffFileWithCanvas(
  extensionPath: string,
  relPath: string
): Promise<void> {
  const workspaceRoot = getWorkspaceRoot();
  if (!workspaceRoot) return;
  await runDriftCheck(extensionPath, path.join(workspaceRoot, relPath));
}

async function runDriftCheck(
  extensionPath: string,
  targetPath: string | null
): Promise<void> {
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

  const cqsRoot = resolveCqsRoot(extensionPath);
  const scriptPath = path.join(cqsRoot, 'sync_to_canvas.py');
  const args = [scriptPath, workspaceRoot, '--check-drift', '--json'];

  if (targetPath) {
    args.push('--only', path.relative(workspaceRoot, targetPath));
  }

  setSyncing(true);

  const progressTitle = targetPath
    ? `Checking drift (${path.basename(targetPath)})`
    : 'Checking drift (all files)';

  vscode.window.withProgress(
    {
      location: vscode.ProgressLocation.Notification,
      title: progressTitle,
      cancellable: false,
    },
    (progress) => {
      return new Promise<void>((resolveProgress) => {
        let stdout = '';
        let stderr = '';

        const proc = spawn(pythonPath, args, {
          cwd: workspaceRoot,
          env: { ...process.env, PYTHONIOENCODING: 'utf-8' },
        });

        proc.stdout?.on('data', (data: Buffer) => {
          const text = data.toString();
          stdout += text;
          // Show log lines as progress
          for (const line of text.split('\n')) {
            const clean = line
              .replace(/\x1b\[[0-9;]*m/g, '')
              .replace(/\[\/?\w[\w\s]*\]/g, '')
              .trim();
            if (clean) {
              progress.report({ message: clean.slice(0, 80) });
            }
          }
        });

        proc.stderr?.on('data', (data: Buffer) => {
          stderr += data.toString();
        });

        proc.on('close', async (code) => {
          setSyncing(false);
          resolveProgress();
          try {

          if (code !== 0) {
            vscode.window.showErrorMessage(
              `Drift check failed (exit code ${code}). ${stderr.slice(0, 200)}`
            );
            return;
          }

          // --check-drift --json implies --quiet upstream, so stdout is
          // the JSON document and nothing else. No prefix to look for.
          let driftItems: DriftItem[];
          try {
            driftItems = JSON.parse(stdout.trim()).drifted ?? [];
          } catch {
            vscode.window.showErrorMessage(
              'Failed to parse drift results: ' + stdout.trim().slice(0, 200)
            );
            return;
          }

          if (driftItems.length === 0) {
            vscode.window.showInformationMessage(
              targetPath
                ? `No drift in ${path.basename(targetPath)}. Canvas matches your local file.`
                : 'No drift detected. Canvas content matches your local files.'
            );
            return;
          }

          // For a single drifted item, open diff directly
          if (driftItems.length === 1) {
            await openDiff(driftItems[0]);
            return;
          }

          // Multiple drifted items: let user pick which to view
          const pickItems = driftItems.map((item) => ({
            label: `$(diff) ${item.title}`,
            description: `[${item.type}] ${item.file}`,
            item,
          }));

          // Add "Open all diffs" option
          const allOption = {
            label: '$(diff-multiple) Open all diffs',
            description: `${driftItems.length} files have drifted`,
            item: null as DriftItem | null,
          };

          const selected = await vscode.window.showQuickPick(
            [allOption, ...pickItems],
            {
              placeHolder: `${driftItems.length} file(s) drifted. Which diff to view?`,
            }
          );

          if (!selected) return;

          if (selected.item === null) {
            // Open all diffs
            for (const item of driftItems) {
              await openDiff(item);
            }
          } else {
            await openDiff(selected.item);
          }
          } catch (e) {
            vscode.window.showErrorMessage(
              'Drift check failed after the run: ' + String(e)
            );
          }
        });

        proc.on('error', (err) => {
          setSyncing(false);
          resolveProgress();
          vscode.window.showErrorMessage(`Drift check error: ${err.message}`);
        });
      });
    }
  );
}

async function openDiff(item: DriftItem): Promise<void> {
  // Uri.file(undefined) throws, and this runs inside an async handler whose
  // rejection goes nowhere: the user clicked Diff and watched nothing happen.
  // Say so instead.
  if (!item.canvas_qmd_path || !item.local_path) {
    vscode.window.showErrorMessage(
      `Cannot open the diff for ${item.title}: the drift check did not return `
      + `a Canvas copy to compare against.`
    );
    return;
  }

  const canvasUri = vscode.Uri.file(item.canvas_qmd_path);
  const localUri = vscode.Uri.file(item.local_path);

  const title = `Canvas ↔ Local: ${item.title}`;

  await vscode.commands.executeCommand(
    'vscode.diff',
    canvasUri,
    localUri,
    title
  );
}
