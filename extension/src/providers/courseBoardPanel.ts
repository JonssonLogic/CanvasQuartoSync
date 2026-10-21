import * as vscode from 'vscode';
import * as path from 'path';
import { spawn } from 'child_process';
import { resolvePython, resolveCqsRoot } from '../python/venvResolver';
import { getWorkspaceRoot } from '../config/configLoader';
import { diffFileWithCanvas } from '../commands/diffWithCanvas';
import { setSyncing } from './statusBar';
import {
  rollups, rollupKey, loadRollups, handleRollup, subscribeRollups,
  renderRollupCtl, renderRollupSummary, ROLLUP_CSS, ROLLUP_SCRIPT,
} from './rollups';

// ── Course Board ────────────────────────────────────────────────────
//
// The course in one view: every module and item in the order a sync will
// produce, matched against what Canvas holds. Modules and items are dragged
// to reorder them (courses with _modules.yml / _order.yml), and every action
// that talks to Canvas starts here.
//
// It opens whatever state the course is in. Without a working Canvas
// connection it shows the local course with a banner saying what to fix.
// All HTML is rendered server-side (in TypeScript) to avoid webview
// messaging / document.write issues.

let currentPanel: vscode.WebviewPanel | undefined;
let extContext: vscode.ExtensionContext | undefined;
let askedToConvert = false;
const log = vscode.window.createOutputChannel('CQS Course Board');


interface ModuleItem {
  title: string;
  type: string;
  published: boolean;
  indent: number;
  local_path: string | null;
  external_url?: string;
  content_id?: number | null;
  page_url?: string | null;
  module_item_id?: number | null;
  html_url?: string;
  updated_at?: string;
  local_mtime?: string;
  last_synced_at?: string;
  // true drifted, false checked and clean, null/undefined not checked.
  canvas_drift?: boolean | null;
  local_only?: boolean;
  // No Canvas connection: only the local side is known.
  offline?: boolean;
  // false: a Canvas item whose file the course layout leaves out.
  included?: boolean;
}

interface Module {
  name: string;
  id: number | null;          // null: not in Canvas yet
  published: boolean | null;
  items: ModuleItem[];
  local_dir?: string;
  in_layout?: boolean;        // a module of the local course
  unlisted?: string[];        // files in the folder the sync leaves out
}

interface Problem {
  level: 'error' | 'warning' | 'info';
  code?: string;
  message: string;
  action?: string;
  path?: string;
}

interface StructureData {
  course_name: string | null;
  course_code: string;
  course_id: number | null;
  total_students?: number | null;
  term?: string;
  workflow_state?: string;
  connected?: boolean;
  problems?: Problem[];
  layout?: {
    mode: 'order-files' | 'nn-prefix';
    problems: Problem[];
    unlisted_dirs: string[];
    root_items: string[];
  };
  modules: Module[];
}

export async function openCourseBoard(context: vscode.ExtensionContext): Promise<void> {
  extContext = context;
  const extensionPath = context.extensionPath;
  log.appendLine('[open] called');

  if (currentPanel) {
    currentPanel.reveal();
    await refreshPanel(extensionPath);
    return;
  }

  currentPanel = vscode.window.createWebviewPanel(
    'cqs.courseBoard',
    'Course Board',
    vscode.ViewColumn.One,
    { enableScripts: true, retainContextWhenHidden: true }
  );

  currentPanel.webview.onDidReceiveMessage(async (msg) => {
    const ws = getWorkspaceRoot();
    if (msg.type === 'openFile') {
      if (ws && msg.path) {
        const uri = vscode.Uri.file(path.join(ws, msg.path));
        vscode.window.showTextDocument(uri);
      }
    } else if (msg.type === 'diff') {
      if (msg.localPath) await diffFileWithCanvas(extensionPath, msg.localPath);
    } else if (msg.type === 'refresh') {
      await refreshPanel(extensionPath, !!msg.withDrift);
    } else if (msg.type === 'sync') {
      if (ws && msg.localPath) {
        await handleSync(extensionPath, ws, msg.localPath, true);
        await refreshPanel(extensionPath);
      }
    } else if (msg.type === 'import') {
      await handleImport(extensionPath, msg.itemData);
    } else if (msg.type === 'importModule') {
      // Import all canvas-only items in a module sequentially
      const items: Record<string, unknown>[] = msg.items;
      let imported = 0;
      let failed = 0;
      for (const itemData of items) {
        try {
          await handleImport(extensionPath, itemData);
          imported++;
        } catch {
          failed++;
        }
      }
      const summary = `Module import: ${imported} imported` + (failed ? `, ${failed} failed` : '');
      if (failed) {
        vscode.window.showWarningMessage(summary);
      } else {
        vscode.window.showInformationMessage(summary);
      }
      await refreshPanel(extensionPath);
    } else if (msg.type === 'batchSync') {
      // Sync multiple local items sequentially (no --force for batch)
      const ws = getWorkspaceRoot();
      if (ws) {
        let synced = 0;
        for (const localPath of msg.paths as string[]) {
          await handleSync(extensionPath, ws, localPath, false);
          synced++;
        }
        vscode.window.showInformationMessage(`Synced ${synced} item(s) to Canvas.`);
        await refreshPanel(extensionPath);
      }
    } else if (msg.type === 'batchImport') {
      // Import multiple canvas-only items sequentially
      const items: Record<string, unknown>[] = msg.items;
      let imported = 0;
      let failed = 0;
      for (const itemData of items) {
        try {
          await handleImport(extensionPath, itemData);
          imported++;
        } catch {
          failed++;
        }
      }
      const summary = `Batch import: ${imported} imported` + (failed ? `, ${failed} failed` : '');
      if (failed) {
        vscode.window.showWarningMessage(summary);
      } else {
        vscode.window.showInformationMessage(summary);
      }
      await refreshPanel(extensionPath);
    } else if (msg.type === 'openUrl') {
      if (msg.url) {
        vscode.env.openExternal(vscode.Uri.parse(msg.url));
      }
    } else if (msg.type === 'setPublished') {
      await handleSetPublished(extensionPath, msg.data);
    } else if (msg.type === 'createItem') {
      await handleCreateItem(extensionPath, msg.moduleDir, msg.itemType, msg.moduleName);
    } else if (msg.type === 'createModule') {
      await handleCreateModule(extensionPath);
    } else if (msg.type === 'batchDelete') {
      await handleBatchDelete(extensionPath, msg.items);
    } else if (msg.type === 'rollup') {
      await handleRollup(extensionPath, msg.action, msg.target);
    } else if (msg.type === 'openConfig') {
      await openConfig();
    } else if (msg.type === 'boardOp') {
      await handleBoardOp(extensionPath, msg.op);
    } else if (msg.type === 'convert') {
      await handleConvert(extensionPath);
    }
  });

  const panel = currentPanel;
  const rollupSub = subscribeRollups((m) => { panel.webview.postMessage(m); });
  currentPanel.onDidDispose(() => { currentPanel = undefined; rollupSub.dispose(); });

  // Show loading, then fetch and render
  currentPanel.webview.html = wrapHtml('<div class="loading">Reading the course and asking Canvas...</div>');
  await refreshPanel(extensionPath);
}

async function refreshPanel(extensionPath: string, withDrift = false): Promise<void> {
  if (!currentPanel) return;
  // The rollup scan is local and cheap, so it rides along with every load.
  const [result] = await Promise.all([
    fetchStructure(extensionPath, withDrift),
    loadRollups(extensionPath),
  ]);
  if (!currentPanel) return;

  if (result.type === 'error') {
    currentPanel.webview.html = wrapHtml(renderFatal(result.message || 'Unknown error'));
  } else if (result.data) {
    currentPanel.webview.html = wrapHtml(renderBody(result.data));
    if ((result.data.problems || []).some(p => p.code === 'nn_prefix')) {
      offerConversion(extensionPath);
    }
  }
}

// ── Config, board edits, conversion ─────────────────────────────────

const CONFIG_STARTER = `# CanvasQuartoSync course settings. The user guide lists every setting.

course_id      = 0     # the number in the Canvas course address: .../courses/12345
canvas_api_url = ""    # e.g. "https://yourschool.instructure.com"

# The API token goes in the CANVAS_API_TOKEN environment variable, or in a
# gitignored file named here:
# canvas_token_path = "canvas_token.txt"
`;

async function openConfig(): Promise<void> {
  const ws = getWorkspaceRoot();
  if (!ws) {
    vscode.window.showErrorMessage('Open the course folder first.');
    return;
  }
  const cfg = path.join(ws, 'config.toml');
  if (!fs.existsSync(cfg)) {
    const pick = await vscode.window.showWarningMessage(
      'This folder has no config.toml.', 'Create config.toml');
    if (pick !== 'Create config.toml') return;
    fs.writeFileSync(cfg, CONFIG_STARTER, 'utf-8');
  }
  await vscode.window.showTextDocument(vscode.Uri.file(cfg));
}

/** Run course_board.py. Local only: it never talks to Canvas. */
function runBoard(extensionPath: string, ws: string, args: string[]): Promise<{ code: number; stdout: string; stderr: string }> {
  return new Promise((resolve) => {
    const pythonPath = resolvePython();
    if (!pythonPath) {
      resolve({ code: 1, stdout: '', stderr: 'Python venv not found. Run install.ps1.' });
      return;
    }
    const script = path.join(resolveCqsRoot(extensionPath), 'course_board.py');
    let stdout = '';
    let stderr = '';
    const proc = spawn(pythonPath, [script, ws, ...args], { cwd: ws, env: { ...process.env, PYTHONIOENCODING: 'utf-8' } });
    proc.stdout?.on('data', (d: Buffer) => { stdout += d.toString(); });
    proc.stderr?.on('data', (d: Buffer) => { stderr += d.toString(); });
    proc.on('close', (code) => resolve({ code: code ?? 1, stdout, stderr }));
    proc.on('error', (err) => resolve({ code: 1, stdout, stderr: err.message }));
  });
}

async function handleBoardOp(extensionPath: string, op: Record<string, unknown>): Promise<void> {
  const ws = getWorkspaceRoot();
  if (!ws || !op) return;
  const res = await runBoard(extensionPath, ws, ['op', JSON.stringify(op)]);
  const line = res.stdout.split('\n').find(l => l.trim().startsWith('BOARD_RESULT_JSON:'));
  try {
    const out = line ? JSON.parse(line.trim().slice('BOARD_RESULT_JSON:'.length)) : null;
    if (!out) {
      vscode.window.showErrorMessage('Course Board: ' + (res.stderr.trim() || 'no answer from course_board.py').slice(0, 400));
    } else if (!out.success) {
      vscode.window.showErrorMessage('Course Board: ' + out.error);
    } else {
      vscode.window.setStatusBarMessage('$(check) ' + out.message, 4000);
    }
  } catch {
    vscode.window.showErrorMessage('Course Board: could not read the result.');
  }
  await refreshPanel(extensionPath);
}

/** Ask once per session whether an NN_ course should move to order files. */
function offerConversion(extensionPath: string): void {
  if (askedToConvert || !extContext) return;
  askedToConvert = true;
  if (extContext.workspaceState.get<boolean>('cqs.convertDismissed')) return;
  vscode.window.showInformationMessage(
    'This course orders modules with NN_ prefixes in file names. Convert it to order files, '
      + 'so you can rearrange it by dragging in the Course Board?',
    'Preview conversion', 'Not now', "Don't ask again"
  ).then((pick) => {
    if (pick === 'Preview conversion') {
      handleConvert(extensionPath);
    } else if (pick === "Don't ask again") {
      extContext?.workspaceState.update('cqs.convertDismissed', true);
    }
  });
}

/** Show what the conversion would do, then do it if asked. */
async function handleConvert(extensionPath: string): Promise<void> {
  const ws = getWorkspaceRoot();
  if (!ws) return;
  const dry = await runBoard(extensionPath, ws, ['migrate', '--dry-run']);
  if (dry.code !== 0) {
    vscode.window.showErrorMessage('Cannot convert: ' + (dry.stderr.trim() || dry.stdout.trim()).slice(0, 400));
    return;
  }
  const doc = await vscode.workspace.openTextDocument({
    content: 'Converting this course from NN_ prefixes to order files\n'
      + '=========================================================\n\n'
      + 'Nothing has changed yet. This is what Convert would do. Nothing in Canvas changes:\n'
      + 'the next sync shows the same module names and item titles.\n\n'
      + dry.stdout,
    language: 'plaintext',
  });
  await vscode.window.showTextDocument(doc, { viewColumn: vscode.ViewColumn.Beside, preview: true });

  const pick = await vscode.window.showWarningMessage(
    'Convert this course to order files?',
    {
      modal: true,
      detail: 'Module folders and their files lose the NN_ prefix, links to them are updated, and '
        + '_modules.yml and _order.yml are written in the current order. The sync map follows, so '
        + 'Canvas ids are kept.\n\nCommit the result as one change. In a team course, ask the others '
        + 'to commit and push first, and to pull afterwards.',
    },
    'Convert'
  );
  if (pick !== 'Convert') return;

  let res = await runBoard(extensionPath, ws, ['migrate']);
  if (res.code !== 0 && /uncommitted/.test(res.stderr)) {
    const again = await vscode.window.showWarningMessage(
      'The course has uncommitted changes.',
      { modal: true, detail: 'Converting now mixes the renames with your other changes. Commit them first if you can.' },
      'Convert anyway'
    );
    if (again !== 'Convert anyway') return;
    res = await runBoard(extensionPath, ws, ['migrate', '--allow-dirty']);
  }
  if (res.code !== 0) {
    vscode.window.showErrorMessage('Conversion failed: ' + (res.stderr.trim() || res.stdout.trim()).slice(0, 400));
  } else {
    log.appendLine('[convert]\n' + res.stdout);
    vscode.window.showInformationMessage(
      'Converted to order files. Drag modules and items in the Course Board to reorder them.',
      'Show changes'
    ).then((p) => { if (p === 'Show changes') vscode.commands.executeCommand('workbench.view.scm'); });
  }
  await refreshPanel(extensionPath);
}

async function handleSetPublished(extensionPath: string, data: Record<string, unknown>): Promise<void> {
  const ws = getWorkspaceRoot();
  if (!ws) return;
  const pythonPath = resolvePython();
  if (!pythonPath) return;

  const cqsRoot = resolveCqsRoot(extensionPath);
  const scriptPath = path.join(cqsRoot, 'sync_to_canvas.py');
  const pubJson = JSON.stringify(data);
  const args = [scriptPath, ws, '--set-published', pubJson];

  setSyncing(true);
  const result = await new Promise<string>((resolve) => {
    let stdout = '';
    const proc = spawn(pythonPath, args, { cwd: ws, env: { ...process.env, PYTHONIOENCODING: 'utf-8' } });
    proc.stdout?.on('data', (d: Buffer) => { stdout += d.toString(); });
    proc.on('close', () => { setSyncing(false); resolve(stdout); });
    proc.on('error', () => { setSyncing(false); resolve(''); });
  });

  const jsonLine = result.split('\n').find(l => l.trim().startsWith('PUBLISH_RESULT_JSON:'));
  if (jsonLine) {
    try {
      const res = JSON.parse(jsonLine.trim().replace('PUBLISH_RESULT_JSON:', ''));
      if (res.success) {
        vscode.window.showInformationMessage(res.message);
      } else {
        vscode.window.showErrorMessage('Publish failed: ' + res.error);
      }
    } catch {
      vscode.window.showErrorMessage('Failed to parse publish result.');
    }
  }
  await refreshPanel(extensionPath);
}

async function handleSync(extensionPath: string, ws: string, localPath: string, force: boolean): Promise<void> {
  const pythonPath = resolvePython();
  if (!pythonPath) return;
  const cqsRoot = resolveCqsRoot(extensionPath);
  const scriptPath = path.join(cqsRoot, 'sync_to_canvas.py');
  const args = [scriptPath, ws, '--only', localPath];
  if (force) args.push('--force');

  // Close previous sync terminal
  const TERM_NAME = 'Canvas Sync';
  vscode.window.terminals.filter(t => t.name === TERM_NAME).forEach(t => t.dispose());

  setSyncing(true);
  await new Promise<void>((resolve) => {
    const writeEmitter = new vscode.EventEmitter<string>();
    const closeEmitter = new vscode.EventEmitter<number | void>();
    const pty: vscode.Pseudoterminal = {
      onDidWrite: writeEmitter.event,
      onDidClose: closeEmitter.event,
      open() {
        writeEmitter.fire(`\x1b[1m> Syncing ${path.basename(localPath)}...\x1b[0m\r\n\r\n`);
        const proc = spawn(pythonPath, args, { cwd: ws, env: { ...process.env, PYTHONIOENCODING: 'utf-8' } });
        proc.stdout?.on('data', (d: Buffer) => {
          for (const line of d.toString().split('\n')) {
            if (line) writeEmitter.fire(line + '\r\n');
          }
        });
        proc.stderr?.on('data', (d: Buffer) => {
          writeEmitter.fire(`\x1b[31m${d.toString().replace(/\n/g, '\r\n')}\x1b[0m`);
        });
        proc.on('close', (code) => {
          writeEmitter.fire('\r\n');
          if (code === 0) {
            writeEmitter.fire(`\x1b[32m✔ Sync complete.\x1b[0m\r\n`);
          } else {
            writeEmitter.fire(`\x1b[31m✖ Sync failed (exit ${code}).\x1b[0m\r\n`);
          }
          setSyncing(false);
          resolve();
        });
        proc.on('error', (err) => {
          writeEmitter.fire(`\x1b[31mError: ${err.message}\x1b[0m\r\n`);
          setSyncing(false);
          resolve();
        });
      },
      close() {},
    };
    const terminal = vscode.window.createTerminal({ name: TERM_NAME, pty });
    terminal.show(true);
  });
}

async function handleImport(extensionPath: string, itemData: Record<string, unknown>): Promise<void> {
  const ws = getWorkspaceRoot();
  if (!ws) return;
  const pythonPath = resolvePython();
  if (!pythonPath) return;

  const cqsRoot = resolveCqsRoot(extensionPath);
  const scriptPath = path.join(cqsRoot, 'sync_to_canvas.py');
  const itemJson = JSON.stringify(itemData);
  const args = [scriptPath, ws, '--import-item', itemJson];

  setSyncing(true);
  const result = await new Promise<string>((resolve) => {
    let stdout = '';
    let stderr = '';
    const proc = spawn(pythonPath, args, { cwd: ws, env: { ...process.env, PYTHONIOENCODING: 'utf-8' } });
    proc.stdout?.on('data', (d: Buffer) => { stdout += d.toString(); });
    proc.stderr?.on('data', (d: Buffer) => { stderr += d.toString(); });
    proc.on('close', () => { setSyncing(false); resolve(stdout); });
    proc.on('error', () => { setSyncing(false); resolve(''); });
  });

  const jsonLine = result.split('\n').find(l => l.trim().startsWith('IMPORT_RESULT_JSON:'));
  if (jsonLine) {
    try {
      const res = JSON.parse(jsonLine.trim().replace('IMPORT_RESULT_JSON:', ''));
      if (res.success) {
        vscode.window.showInformationMessage('Imported: ' + res.file);
        // Open the file
        const uri = vscode.Uri.file(path.join(ws, res.file));
        vscode.window.showTextDocument(uri);
        // Refresh the panel
        await refreshPanel(extensionPath);
      } else {
        vscode.window.showErrorMessage('Import failed: ' + res.error);
      }
    } catch {
      vscode.window.showErrorMessage('Failed to parse import result.');
    }
  } else {
    vscode.window.showErrorMessage('Import produced no output.');
  }
}

import * as fs from 'fs';

const ITEM_TEMPLATES: Record<string, string> = {
  page: `---
title: "New Page"
canvas:
  type: page
  published: false
---

`,
  assignment: `---
title: "New Assignment"
canvas:
  type: assignment
  published: false
  points: 0
  submission_types:
  - online_upload
---

`,
  quiz: `---
title: "New Quiz"
canvas:
  type: quiz
  published: false
---

`,
  external_url: `---
title: "New Link"
canvas:
  type: external_url
  url: ""
  published: false
  new_tab: true
---
`,
};

const TYPE_LABELS: Record<string, string> = {
  page: 'Page',
  assignment: 'Assignment',
  quiz: 'Quiz',
  external_url: 'External_URL',
};

async function handleCreateItem(extensionPath: string, moduleDir: string, itemType: string, moduleName?: string): Promise<void> {
  const ws = getWorkspaceRoot();
  if (!ws) return;

  const template = ITEM_TEMPLATES[itemType];
  if (!template) {
    vscode.window.showErrorMessage('Unknown item type: ' + itemType);
    return;
  }

  const modPath = path.join(ws, moduleDir);
  if (!fs.existsSync(modPath)) {
    fs.mkdirSync(modPath, { recursive: true });
  }

  // A course with order files names files plainly and lists the folder in
  // _modules.yml; an NN_ course numbers them.
  const ordered = fs.existsSync(path.join(ws, '_modules.yml'));
  let prefix = '';
  if (!ordered) {
    // Determine next file index
    const existing = fs.readdirSync(modPath).filter(f => /^\d{2,}_/.test(f)).sort();
    let nextIdx = 1;
    if (existing.length > 0) {
      const m = existing[existing.length - 1].match(/^(\d+)/);
      if (m) nextIdx = parseInt(m[1], 10) + 1;
    }
    prefix = nextIdx.toString().padStart(2, '0') + '_';
  }
  const label = TYPE_LABELS[itemType] || 'Item';

  // Find next unused "New <Label>" suffix by scanning existing filenames
  const baseRe = new RegExp(`^${ordered ? '' : '\\d{2,}_'}New_${label}(?:_(\\d+))?\\.qmd$`, 'i');
  let suffix = 0;
  for (const f of fs.readdirSync(modPath)) {
    const m = f.match(baseRe);
    if (m) {
      const n = m[1] ? parseInt(m[1], 10) : 1;
      if (n > suffix) suffix = n;
    }
  }
  const nextSuffix = suffix === 0 ? 1 : suffix + 1;
  const titleName = nextSuffix === 1 ? `New ${label}` : `New ${label} ${nextSuffix}`;
  const fileSuffix = nextSuffix === 1 ? '' : `_${nextSuffix}`;
  const filename = `${prefix}New_${label}${fileSuffix}.qmd`;
  const filePath = path.join(modPath, filename);

  const content = template.replace(/title: "New [^"]+"/, `title: "${titleName}"`);
  fs.writeFileSync(filePath, content, 'utf-8');
  if (ordered) {
    // A module that so far existed only in Canvas becomes part of the course.
    // Already listed: add-module changes nothing.
    await runBoard(extensionPath, ws, ['op', JSON.stringify({ op: 'add-module', dir: moduleDir, name: moduleName })]);
  }
  const uri = vscode.Uri.file(filePath);
  await vscode.window.showTextDocument(uri);
  await refreshPanel(extensionPath);
}

async function handleCreateModule(extensionPath: string): Promise<void> {
  const ws = getWorkspaceRoot();
  if (!ws) return;

  const name = await vscode.window.showInputBox({
    prompt: 'Name of the new module',
    placeHolder: 'e.g. Introduction to Sensors',
    validateInput: (v) => (v && v.trim() ? null : 'Name is required'),
  });
  if (!name) return;

  const pubPick = await vscode.window.showQuickPick(
    [
      { label: 'Draft', description: 'Create unpublished', value: false },
      { label: 'Published', description: 'Create and publish immediately', value: true },
    ],
    { placeHolder: 'Publish state for the new module' }
  );
  if (!pubPick) return;

  const pythonPath = resolvePython();
  if (!pythonPath) return;
  const cqsRoot = resolveCqsRoot(extensionPath);
  const scriptPath = path.join(cqsRoot, 'sync_to_canvas.py');
  const payload = JSON.stringify({ name: name.trim(), published: pubPick.value });
  const args = [scriptPath, ws, '--create-module', payload];

  setSyncing(true);
  const result = await new Promise<string>((resolve) => {
    let stdout = '';
    const proc = spawn(pythonPath, args, { cwd: ws, env: { ...process.env, PYTHONIOENCODING: 'utf-8' } });
    proc.stdout?.on('data', (d: Buffer) => { stdout += d.toString(); });
    proc.on('close', () => { setSyncing(false); resolve(stdout); });
    proc.on('error', () => { setSyncing(false); resolve(''); });
  });

  const jsonLine = result.split('\n').find(l => l.trim().startsWith('CREATE_MODULE_JSON:'));
  if (jsonLine) {
    try {
      const res = JSON.parse(jsonLine.trim().replace('CREATE_MODULE_JSON:', ''));
      if (res.success) {
        vscode.window.showInformationMessage(res.message + (res.module_dir ? ` (${res.module_dir})` : ''));
      } else {
        vscode.window.showErrorMessage('Create module failed: ' + res.error);
      }
    } catch {
      vscode.window.showErrorMessage('Failed to parse create-module result.');
    }
  } else {
    vscode.window.showErrorMessage('Create module produced no output.');
  }
  await refreshPanel(extensionPath);
}

async function handleBatchDelete(extensionPath: string, items: Record<string, unknown>[]): Promise<void> {
  const ws = getWorkspaceRoot();
  if (!ws || !items || items.length === 0) return;

  const modCount = items.filter(i => i.target === 'module').length;
  const itemCount = items.filter(i => i.target === 'item').length;
  const localCount = items.filter(i => i.target === 'local_file').length;
  const parts: string[] = [];
  if (modCount) parts.push(`${modCount} module${modCount > 1 ? 's' : ''} (and their contents)`);
  if (itemCount) parts.push(`${itemCount} Canvas item${itemCount > 1 ? 's' : ''}`);
  if (localCount) parts.push(`${localCount} local file${localCount > 1 ? 's' : ''}`);
  const detail = 'This will permanently delete: ' + parts.join(', ') + '.\nLocal files matched to Canvas items will also be removed. This cannot be undone.';

  const choice = await vscode.window.showWarningMessage(
    `Delete ${items.length} selected item${items.length > 1 ? 's' : ''}?`,
    { modal: true, detail },
    'Delete'
  );
  if (choice !== 'Delete') return;

  const pythonPath = resolvePython();
  if (!pythonPath) return;
  const cqsRoot = resolveCqsRoot(extensionPath);
  const scriptPath = path.join(cqsRoot, 'sync_to_canvas.py');
  const payload = JSON.stringify({ items });
  const args = [scriptPath, ws, '--delete', payload];

  setSyncing(true);
  const result = await new Promise<string>((resolve) => {
    let stdout = '';
    const proc = spawn(pythonPath, args, { cwd: ws, env: { ...process.env, PYTHONIOENCODING: 'utf-8' } });
    proc.stdout?.on('data', (d: Buffer) => { stdout += d.toString(); });
    proc.on('close', () => { setSyncing(false); resolve(stdout); });
    proc.on('error', () => { setSyncing(false); resolve(''); });
  });

  const jsonLine = result.split('\n').find(l => l.trim().startsWith('DELETE_RESULT_JSON:'));
  if (jsonLine) {
    try {
      const res = JSON.parse(jsonLine.trim().replace('DELETE_RESULT_JSON:', ''));
      const msg = `Deleted ${res.deleted}` + (res.failed ? `, ${res.failed} failed` : '');
      if (res.failed) {
        vscode.window.showWarningMessage(msg + (res.errors?.length ? ': ' + res.errors.join('; ') : ''));
      } else {
        vscode.window.showInformationMessage(msg);
      }
    } catch {
      vscode.window.showErrorMessage('Failed to parse delete result.');
    }
  } else {
    vscode.window.showErrorMessage('Delete produced no output.');
  }
  await refreshPanel(extensionPath);
}

// ── Fetch from Python ───────────────────────────────────────────────

async function fetchStructure(
  extensionPath: string,
  withDrift = false
): Promise<{ type: string; data?: StructureData; message?: string }> {
  const workspaceRoot = getWorkspaceRoot();
  if (!workspaceRoot) return { type: 'error', message: 'No folder is open. Open the course folder (File > Open Folder) to see its board.' };

  const pythonPath = resolvePython();
  if (!pythonPath) return { type: 'error', message: 'The CanvasQuartoSync Python environment was not found. Run install.ps1 (Windows) or install.sh again.' };

  const cqsRoot = resolveCqsRoot(extensionPath);
  const scriptPath = path.join(cqsRoot, 'sync_to_canvas.py');
  const args = [scriptPath, workspaceRoot, '--module-structure'];
  // Off by default: drift costs one Canvas request per synced item, and
  // opening the panel should not pay for an answer nobody asked for.
  if (withDrift) args.push('--with-drift');

  log.appendLine('[fetch] python=' + pythonPath);
  log.appendLine('[fetch] cwd=' + workspaceRoot);
  setSyncing(true);

  return new Promise((resolve) => {
    let stdout = '';
    let stderr = '';

    const proc = spawn(pythonPath, args, { cwd: workspaceRoot, env: { ...process.env, PYTHONIOENCODING: 'utf-8' } });
    proc.stdout?.on('data', (d: Buffer) => { stdout += d.toString(); });
    proc.stderr?.on('data', (d: Buffer) => { stderr += d.toString(); });

    proc.on('close', (code) => {
      setSyncing(false);
      log.appendLine('[fetch] exit=' + code);
      if (code !== 0) {
        // The tool logs through rich, which writes to stdout, so stderr is
        // empty on a normal failure and this message used to read just
        // 'Python exit 1:'. Prefer whichever stream actually said something.
        const detail = (stderr.trim() || stdout.trim() || '(no output)').slice(0, 500);
        resolve({ type: 'error', message: 'Python exit ' + code + ': ' + detail });
        return;
      }
      // --module-structure implies --quiet upstream, so stdout is the
      // JSON document and nothing else. No prefix to look for.
      try {
        resolve({ type: 'structure', data: JSON.parse(stdout.trim()) });
      } catch (e) {
        resolve({
          type: 'error',
          message: 'JSON parse error: ' + e + ' | ' + (stderr || stdout).slice(0, 500),
        });
      }
    });

    proc.on('error', (err) => {
      setSyncing(false);
      resolve({ type: 'error', message: 'Spawn error: ' + err.message });
    });
  });
}

// ── HTML rendering (all server-side) ────────────────────────────────

function esc(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function relativeTime(isoStr: string): string {
  if (!isoStr) return '';
  const d = new Date(isoStr);
  const now = new Date();
  const diffMs = now.getTime() - d.getTime();
  const diffMin = Math.floor(diffMs / 60000);
  if (diffMin < 1) return 'just now';
  if (diffMin < 60) return diffMin + 'm ago';
  const diffHr = Math.floor(diffMin / 60);
  if (diffHr < 24) return diffHr + 'h ago';
  const diffDays = Math.floor(diffHr / 24);
  if (diffDays < 30) return diffDays + 'd ago';
  // Show date
  return d.toLocaleDateString('sv-SE', { month: 'short', day: 'numeric' });
}

const TYPE_ICONS: Record<string, string> = {
  Page: '&#x1F4C4;',
  Assignment: '&#x1F4DD;',
  Quiz: '&#x2753;',
  File: '&#x1F4CE;',
  ExternalUrl: '&#x1F517;',
  ExternalTool: '&#x1F527;',
  SubHeader: '&#x1F4CC;',
};

/**
 * What state is this item in, as words.
 *
 * The panel used to answer this with a coloured dot and a legend, which is
 * unreadable if you cannot separate the colours, and needs a lookup even if
 * you can. The text is the answer; any colour is decoration on top of it.
 *
 * `title` carries the part that does not fit in two words, including the one
 * honest caveat: without a drift check we know our own file has not changed
 * since we pushed it, but not whether somebody edited it in Canvas.
 */
function syncStatus(item: ModuleItem): { cls: string; text: string; title: string } {
  if (item.offline) {
    return item.local_only
      ? { cls: 'not-synced', text: 'Never synced', title: 'Not connected to Canvas. The sync map has no record of this file.' }
      : { cls: 'unknown', text: 'Synced before', title: 'Not connected to Canvas, so only the local side is known.' };
  }
  if (item.local_only) {
    return {
      cls: 'not-synced',
      text: 'Not synced',
      title: 'Exists locally, never pushed to Canvas.',
    };
  }
  if (!item.local_path) {
    return {
      cls: 'canvas-only',
      text: 'Canvas only',
      title: 'Exists in Canvas with no local file. Import it to bring it under source control.',
    };
  }

  // A content hash beats every timestamp, and for an assignment it is the only
  // signal there is: Canvas bumps their updated_at for submissions and grading,
  // so it is never fetched and never means anything about the content.
  if (item.canvas_drift === true) {
    return {
      cls: 'canvas-newer',
      text: 'Canvas newer',
      title: 'Canvas content differs from what was last synced. Syncing overwrites it.',
    };
  }

  const syncTime = item.last_synced_at ? new Date(item.last_synced_at).getTime() : 0;
  const localTime = item.local_mtime ? new Date(item.local_mtime).getTime() : 0;
  const canvasTime = item.updated_at ? new Date(item.updated_at).getTime() : 0;

  if (syncTime) {
    // updated_at is not a content timestamp for anything. Canvas bumps it for
    // publishing, grading and settings changes, so it cannot say the content
    // changed, only that something happened. Claiming "Canvas newer" from it
    // made the panel accuse Canvas of an edit the panel itself had just caused
    // by publishing an item. Only the hash gets to make that claim; the
    // timestamp gets to raise the question.
    if (item.canvas_drift == null && canvasTime > syncTime + 60000) {
      return {
        cls: 'check-canvas',
        text: 'Check Canvas',
        title: 'Canvas reports activity since the last sync, but a timestamp '
          + 'moves for publishing and settings changes too, so it may not be the '
          + 'content. Press Check Canvas to compare it properly.',
      };
    }
    if (localTime > syncTime + 60000) {
      return {
        cls: 'local-newer',
        text: 'Local newer',
        title: 'The local file changed after the last sync. Sync to push it.',
      };
    }
    return {
      cls: 'in-sync',
      text: 'In sync',
      title: item.canvas_drift === false
        ? 'Local file and Canvas content both match the last sync.'
        : 'Local file unchanged since the last sync. The Canvas side has not been '
          + 'checked: press Check Canvas to compare content.',
    };
  }

  // Never synced by this tool, so the only comparison left is two timestamps.
  if (canvasTime && localTime) {
    if (Math.abs(canvasTime - localTime) < 120000) {
      return { cls: 'in-sync', text: 'In sync', title: 'Timestamps match. Never synced by this tool.' };
    }
    return canvasTime > localTime
      ? { cls: 'canvas-newer', text: 'Canvas newer', title: 'Canvas is newer than the local file. Never synced by this tool.' }
      : { cls: 'local-newer', text: 'Local newer', title: 'The local file is newer than Canvas. Never synced by this tool.' };
  }

  return {
    cls: 'unknown',
    text: 'Unknown',
    title: 'Matched to a local file, but there is nothing to compare: no sync '
      + 'record and no usable Canvas timestamp.',
  };
}

/** Button label and what it does, for the actions a problem can offer. */
const PROBLEM_ACTIONS: Record<string, { label: string; js: string }[]> = {
  no_config: [{ label: 'Open config.toml', js: 'openConfig()' }, { label: 'Retry', js: 'refresh()' }],
  no_course_id: [{ label: 'Open config.toml', js: 'openConfig()' }, { label: 'Retry', js: 'refresh()' }],
  no_credentials: [{ label: 'Open config.toml', js: 'openConfig()' }, { label: 'Retry', js: 'refresh()' }],
  bad_token: [{ label: 'Open config.toml', js: 'openConfig()' }, { label: 'Retry', js: 'refresh()' }],
  course_not_found: [{ label: 'Open config.toml', js: 'openConfig()' }, { label: 'Retry', js: 'refresh()' }],
  connect_failed: [{ label: 'Retry', js: 'refresh()' }, { label: 'Open config.toml', js: 'openConfig()' }],
  wrong_course: [{ label: 'Open config.toml', js: 'openConfig()' }, { label: 'Retry', js: 'refresh()' }],
  nn_prefix: [{ label: 'Preview conversion', js: 'convertLayout()' }],
  fatal: [{ label: 'Retry', js: 'refresh()' }, { label: 'Open config.toml', js: 'openConfig()' }],
};

function renderBanner(p: Problem): string {
  const level = p.level === 'error' ? 'error' : p.level === 'warning' ? 'warning' : 'info';
  const icon = level === 'error' ? '&#x26D4;' : level === 'warning' ? '&#x26A0;&#xFE0F;' : '&#x2139;&#xFE0F;';
  let h = '<div class="banner ' + level + '"><span class="banner-icon">' + icon + '</span><div class="banner-text">';
  h += '<div class="banner-msg">' + esc(p.message) + '</div>';
  if (p.action) h += '<div class="banner-action">' + esc(p.action) + '</div>';
  h += '</div><div class="banner-btns">';
  for (const a of PROBLEM_ACTIONS[p.code || ''] || []) {
    h += '<button class="refresh-btn" onclick="' + a.js + '">' + esc(a.label) + '</button>';
  }
  h += '</div></div>';
  return h;
}

/** The page shown when there is no structure at all: a banner and nothing else. */
function renderFatal(message: string): string {
  let h = '<div class="header"><div><h1>Course Board</h1>'
    + '<div class="subtitle">Could not read the course</div></div>' + headerButtons(false) + '</div>';
  h += renderBanner({ level: 'error', code: 'fatal', message, action: '' });
  return h;
}

function headerButtons(online: boolean): string {
  const off = online ? '' : ' disabled title="Needs a connection to Canvas"';
  return '<div class="header-btns">'
    + '<button class="refresh-btn" onclick="doCreateModule()"' + (online ? ' title="Create a new module in Canvas and a folder for it"' : off) + '>+ New Module</button>'
    + '<button class="refresh-btn" onclick="checkCanvas()"' + (online ? ' title="Ask Canvas what has changed since the last sync. One request per synced item, so it takes a moment."' : off) + '>Check Canvas</button>'
    + '<button class="refresh-btn secondary" onclick="openConfig()" title="Open config.toml: course id, Canvas address, workflow">Config</button>'
    + '<button class="refresh-btn" onclick="refresh()">Refresh</button>'
    + '</div>';
}

function moduleDirGuess(mod: Module, mi: number, ordered: boolean): string {
  if (mod.local_dir) return mod.local_dir;
  const fromItem = mod.items.find(i => i.local_path)?.local_path?.split('/')[0];
  if (fromItem) return fromItem;
  const safe = mod.name.replace(/[^a-zA-Z0-9åäöÅÄÖ _-]/g, '').replace(/\s+/g, ' ').trim() || 'Module';
  return ordered ? safe : (mi + 1).toString().padStart(2, '0') + '_' + safe;
}

function jsArg(s: string): string {
  return esc(s).replace(/\\/g, '\\\\').replace(/'/g, "\\'");
}

function renderBody(data: StructureData): string {
  const online = data.connected !== false;
  const ordered = data.layout?.mode === 'order-files';
  const allItems = data.modules.flatMap(m => m.items);
  // Compute counts excluding local-only items from canvas totals
  const canvasItems = allItems.filter(i => !i.local_only && !i.offline).length;
  const syncedItems = allItems.filter(i => i.local_path && !i.local_only && !i.offline).length;
  const canvasOnly = canvasItems - syncedItems;
  const notSyncedItems = allItems.filter(i => i.local_only).length;

  let h = '';

  // Header
  h += '<div class="header"><div>';
  const courseTitle = online
    ? (data.course_code ? esc(data.course_code) + ' &mdash; ' + esc(data.course_name || '') : esc(data.course_name || ''))
    : 'Course Board';
  h += '<h1>' + courseTitle + '</h1>';
  const infoParts: string[] = [];
  if (online) {
    infoParts.push('ID: ' + data.course_id);
    if (data.total_students != null) infoParts.push(data.total_students + ' students');
    if (data.term) infoParts.push('Term: ' + esc(data.term));
    if (data.workflow_state) infoParts.push(esc(data.workflow_state));
  } else {
    infoParts.push('Not connected to Canvas');
  }
  infoParts.push(data.modules.length + ' modules');
  infoParts.push(online ? canvasItems + ' items' : allItems.length + ' local files');
  infoParts.push(ordered ? 'order files' : 'NN_ prefixes');
  if (online) {
    // Say plainly whether the Canvas side was compared, so "In sync" cannot be
    // read as a promise the panel never made.
    const driftChecked = data.modules.some(m => m.items.some(i => i.canvas_drift != null));
    infoParts.push(driftChecked ? 'Canvas checked' : 'Canvas not checked');
  }
  h += '<div class="subtitle">' + infoParts.join(' &middot; ') + '</div>';
  h += '</div>' + headerButtons(online) + '</div>';

  // What is wrong, and what to do: config and connection first, then the
  // order files, then the invitation to convert.
  const problems = (data.problems || []).slice().sort((a, b) =>
    (a.level === 'info' ? 1 : 0) - (b.level === 'info' ? 1 : 0));
  for (const p of problems.filter(p => p.level !== 'info')) h += renderBanner(p);
  const layoutProblems = data.layout?.problems || [];
  if (layoutProblems.length) {
    h += renderBanner({
      level: layoutProblems.some(p => p.level === 'error') ? 'error' : 'warning',
      code: 'layout',
      message: 'The order files need a look:',
      action: layoutProblems.map(p => p.path + ': ' + p.message).join('\n'),
    });
  }
  for (const p of problems.filter(p => p.level === 'info')) h += renderBanner(p);

  // Stats
  if (online) {
    h += '<div class="stats">';
    h += '<div class="stat"><span class="num" style="color:#198754">' + syncedItems + '</span> synced</div>';
    h += '<div class="stat"><span class="num" style="color:#dc3545">' + canvasOnly + '</span> Canvas-only</div>';
    if (notSyncedItems > 0) {
      h += '<div class="stat"><span class="num" style="color:#0d6efd">' + notSyncedItems + '</span> not synced</div>';
    }
    h += '</div>';
  }
  if (ordered) {
    h += '<div class="hint">Drag modules by their handle and items by their row to reorder, or into another module to move them. '
      + 'It changes the order files and folders only; the next sync applies it in Canvas.</div>';
  }

  // Modules
  h += '<div id="modules">';
  for (let mi = 0; mi < data.modules.length; mi++) {
    const mod = data.modules[mi];
    const hasCanvas = mod.id != null;
    const inLayout = !!mod.in_layout;
    const dragMod = ordered && inLayout;
    const modCanvasItems = mod.items.filter(i => !i.local_only && !i.offline);
    const modLocalOnly = mod.items.filter(i => i.local_only);
    const modSynced = modCanvasItems.filter(i => i.local_path).length;
    const modDir = moduleDirGuess(mod, mi, ordered);
    const localDir = mod.local_dir || '';

    h += '<div class="module' + (hasCanvas ? '' : ' not-in-canvas') + (inLayout || !hasCanvas ? '' : ' canvas-only-module')
      + '" data-mi="' + mi + '" data-dir="' + esc(localDir) + '" data-inlayout="' + (dragMod ? '1' : '0') + '">';
    h += '<div class="module-header"' + (dragMod ? ' draggable="true" title="Drag to reorder modules"' : '') + '>';
    if (dragMod) h += '<span class="drag-handle">&#x2807;</span>';
    const modDelData = esc(JSON.stringify({ target: 'module', module_id: mod.id, local_dir: localDir || (hasCanvas ? modDir : '') }));
    h += '<input type="checkbox" class="item-cb mod-cb" onclick="event.stopPropagation();onCheckChanged()" data-kind="delete" data-delete="' + modDelData.replace(/"/g, '&quot;') + '" title="Select module for delete"> ';
    h += '<span class="toggle" id="toggle-' + mi + '" onclick="toggleModule(' + mi + ')">&#9660;</span> ';
    h += '<span onclick="toggleModule(' + mi + ')" style="flex:1;cursor:pointer">' + esc(mod.name) + '</span>';
    if (hasCanvas && online) {
      const pubCls = mod.published ? 'published' : 'draft';
      const pubLbl = mod.published ? 'Published' : 'Draft';
      const togglePubMod = !mod.published;
      h += ' <span class="pub-badge ' + pubCls + ' pub-toggle" onclick="event.stopPropagation();doSetPublished({target:\'module\',module_id:' + mod.id + ',published:' + togglePubMod + '})" title="Click to ' + (togglePubMod ? 'publish' : 'unpublish') + '">' + pubLbl + '</span>';
    } else if (!hasCanvas && online) {
      h += ' <span class="pub-badge new" title="This folder is a module locally, but Canvas has no module for it yet. Syncing creates it.">Not in Canvas yet</span>';
    }
    if (hasCanvas && !inLayout && ordered) {
      h += ' <span class="pub-badge canvas-only" title="Exists only in Canvas. Import its items to bring it into the course folder.">Canvas only</span>';
    }

    // "Import Module" button if there are canvas-only items (exclude local-only)
    const canvasOnlyInMod = modCanvasItems.filter(i => !i.local_path);
    if (online && canvasOnlyInMod.length > 0) {
      h += '<button class="action-btn import-btn module-action-btn" onclick="event.stopPropagation();doImportModule(' + mi + ')" title="Import all '
        + canvasOnlyInMod.length + ' Canvas-only items">Import &#x2193; (' + canvasOnlyInMod.length + ')</button>';
    }

    // Sync the whole module. For a module Canvas does not have yet this is how
    // it gets created: the first file synced makes it.
    const syncPaths = mod.items.filter(i => i.local_path && i.included !== false).map(i => i.local_path!.replace(/\\/g, '/'));
    if (online && syncPaths.length > 0) {
      const label = hasCanvas ? 'Sync all &#x2191;' : 'Create in Canvas &#x2191;';
      const title = hasCanvas
        ? 'Sync all ' + syncPaths.length + ' files in this module, in order'
        : 'Create the module "' + mod.name + '" in Canvas and sync its ' + syncPaths.length + ' files into it, in order';
      h += '<button class="action-btn sync-btn module-action-btn" onclick="event.stopPropagation();doSyncModule(' + mi + ')" title="' + esc(title) + '">' + label + '</button>';
      h += '<div class="module-sync-data" data-mi="' + mi + '" data-paths="' + esc(JSON.stringify(syncPaths)) + '" style="display:none"></div>';
    }

    // "+ New" dropdown
    h += '<div class="new-dropdown" style="position:relative;display:inline-block">';
    h += '<button class="action-btn new-btn module-action-btn" onclick="event.stopPropagation();toggleNewMenu(' + mi + ')" title="Create new item in this module">+ New &#x25BE;</button>';
    h += '<div class="new-menu hidden" id="new-menu-' + mi + '">';
    const escDir = jsArg(modDir);
    const escName = jsArg(mod.name);
    h += '<div class="new-menu-item" onclick="doCreateItem(\'' + escDir + '\',\'page\',\'' + escName + '\')">&#x1F4C4; Page</div>';
    h += '<div class="new-menu-item" onclick="doCreateItem(\'' + escDir + '\',\'assignment\',\'' + escName + '\')">&#x1F4DD; Assignment</div>';
    h += '<div class="new-menu-item" onclick="doCreateItem(\'' + escDir + '\',\'quiz\',\'' + escName + '\')">&#x2753; Quiz</div>';
    h += '<div class="new-menu-item" onclick="doCreateItem(\'' + escDir + '\',\'external_url\',\'' + escName + '\')">&#x1F517; External URL</div>';
    h += '</div></div>';

    // Count
    let countText = online
      ? modSynced + '/' + modCanvasItems.length + ' synced'
      : mod.items.length + ' files';
    if (online && modLocalOnly.length > 0) {
      countText += ' + ' + modLocalOnly.length + ' local';
    }
    h += '<span class="count">' + countText + '</span>';
    h += '</div>';
    h += '<div class="module-items" id="items-' + mi + '" data-dir="' + esc(localDir) + '">';

    for (const item of mod.items) {
      const isLocalOnly = !!item.local_only;
      const hasLocal = !!item.local_path;
      const icon = (isLocalOnly || item.offline) ? '&#x1F4C4;' : (TYPE_ICONS[item.type] || '&#x1F4E6;');
      const excluded = item.included === false;

      const status = excluded ? {
        cls: 'excluded',
        text: 'Not in order',
        title: 'Matched to a local file that the course layout does not include, so a sync leaves it alone. '
          + 'Add it to the module to keep it in sync.',
      } : syncStatus(item);
      const clickCls = item.html_url ? ' clickable' : '';
      const safePath = hasLocal ? esc(item.local_path!.replace(/\\/g, '/')) : '';
      const draggableRow = ordered && inLayout && hasLocal && !excluded
        && localDir !== '' && item.local_path!.split('/')[0] === localDir;
      // Title links to Canvas, or to the file when there is no Canvas item
      let titleClick = '';
      if (item.html_url) {
        titleClick = ' onclick="openUrl(\'' + esc(item.html_url).replace(/'/g, "\\'") + '\')"';
      } else if (hasLocal) {
        titleClick = ' onclick="openFile(\'' + safePath.replace(/'/g, "\\'") + '\')"';
      }

      // Badge text
      const badgeText = item.offline ? 'Local' : isLocalOnly ? 'Not synced' : item.type;
      const badgeCls = (isLocalOnly || item.offline) ? ' not-synced-badge' : '';

      // Build import data for canvas-only items
      const importDataObj = (!hasLocal && !isLocalOnly) ? {
        module_dir: modDir,
        module_name: mod.name,
        item_type: item.type,
        content_id: item.content_id || null,
        page_url: item.page_url || null,
        title: item.title,
        published: item.published,
        indent: item.indent,
        external_url: item.external_url || '',
      } : null;

      const dataKind = !online ? 'local' : (hasLocal && !excluded) ? 'sync' : hasLocal ? 'local' : 'import';
      const dataVal = hasLocal ? safePath : (importDataObj ? esc(JSON.stringify(importDataObj)) : '');
      // Build delete payload for this item
      let deleteObj: Record<string, unknown>;
      if (isLocalOnly || item.offline) {
        deleteObj = { target: 'local_file', local_path: hasLocal ? item.local_path : '' };
      } else if (item.module_item_id) {
        deleteObj = { target: 'item', module_id: mod.id, item_id: item.module_item_id, local_path: hasLocal ? item.local_path : '' };
      } else {
        deleteObj = { target: 'local_file', local_path: hasLocal ? item.local_path : '' };
      }
      const deleteVal = esc(JSON.stringify(deleteObj));
      h += '<div class="item' + clickCls + (draggableRow ? ' drag-item' : '') + '"'
        + (draggableRow ? ' draggable="true" data-path="' + safePath + '"' : '')
        + ' data-kind="' + dataKind + '" data-value="' + dataVal.replace(/"/g, '&quot;') + '">';
      h += '<span class="cb-cell"><input type="checkbox" class="item-cb" onclick="event.stopPropagation();onCheckChanged()" data-kind="' + dataKind + '" data-value="' + dataVal.replace(/"/g, '&quot;') + '" data-delete="' + deleteVal.replace(/"/g, '&quot;') + '"></span>';
      h += '<span class="icon">' + icon + '</span>';
      // A rollup target carries its button inside the title cell, at the
      // right edge, so it sits left of the type badge without adding a grid
      // column that every other row would have to leave empty.
      const ru = hasLocal ? rollups.get(rollupKey(item.local_path!)) : undefined;
      if (ru) {
        h += '<span class="title has-rollup"><span class="title-text"' + titleClick + '>' + esc(item.title) + '</span>'
          + '<span class="rollup-ctl" data-rollup="' + esc(rollupKey(item.local_path!)) + '">'
          + renderRollupCtl(ru) + '</span></span>';
      } else {
        h += '<span class="title"' + titleClick + '>' + esc(item.title) + '</span>';
      }
      h += '<span class="type-badge' + badgeCls + '">' + badgeText + '</span>';
      const updatedText = item.updated_at ? relativeTime(item.updated_at) : '';
      const updatedFull = item.updated_at || '';
      h += '<span class="updated" title="' + esc(updatedFull) + '">' + updatedText + '</span>';
      const pathText = hasLocal ? esc(item.local_path!) : (item.external_url ? esc(item.external_url) : '');
      if (hasLocal) {
        h += '<span class="local-path clickable" title="' + pathText + '" onclick="openFile(\'' + safePath.replace(/'/g, "\\'") + '\')">' + pathText + '</span>';
      } else {
        h += '<span class="local-path" title="' + pathText + '">' + pathText + '</span>';
      }

      h += '<span class="status ' + status.cls + '" title="' + esc(status.title) + '">'
        + status.text + '</span>';

      // Action buttons
      h += '<span class="actions">';
      if (online) {
        // Publish/unpublish toggle for Canvas items (not local-only)
        if (!isLocalOnly && item.module_item_id) {
          const togglePub = !item.published;
          // Words, not a green or grey circle. It sits next to the sync status,
          // and a row should not need colour vision to be read.
          const pubTitle = item.published ? 'Published. Click to unpublish.' : 'Draft. Click to publish.';
          const pubBtnCls = item.published ? 'pub-on' : 'pub-off';
          h += '<button class="action-btn pub-btn ' + pubBtnCls + '" onclick="event.stopPropagation();doSetPublished({target:\'item\',module_id:'
            + mod.id + ',item_id:' + item.module_item_id + ',published:' + togglePub + '})" title="' + pubTitle + '">'
            + (item.published ? 'Published' : 'Draft') + '</button>';
        }
        // Only offered where Canvas is known to differ. Everywhere else it
        // would spend a Canvas round trip to say "nothing changed".
        if (hasLocal && (status.cls === 'canvas-newer' || status.cls === 'check-canvas')) {
          h += '<button class="action-btn diff-btn" onclick="event.stopPropagation();doDiff(\''
            + safePath.replace(/'/g, "\\'") + '\')" title="Show what changed in Canvas, side by side with your local file">Diff</button>';
        }
        if (excluded && ordered) {
          h += '<button class="action-btn new-btn" onclick="event.stopPropagation();boardOp({op:\'include-item\',path:\''
            + safePath.replace(/'/g, "\\'") + '\'})" title="List the file in this module\'s _order.yml so the sync keeps it">+ Add</button>';
        } else if (isLocalOnly || hasLocal) {
          // Has local file: Sync button
          h += '<button class="action-btn sync-btn" onclick="event.stopPropagation();doSync(\''
            + safePath.replace(/'/g, "\\'") + '\')" title="' + (hasCanvas ? 'Upload to Canvas' : 'Create the module in Canvas and upload this file') + '">Sync &#x2191;</button>';
        } else {
          // Canvas-only: Import button
          const importData = esc(JSON.stringify(importDataObj));
          h += '<button class="action-btn import-btn" onclick="event.stopPropagation();doImport(\''
            + importData.replace(/'/g, '&#39;') + '\')" title="Import from Canvas">Import &#x2193;</button>';
        }
      }
      h += '</span>';
      h += '</div>';
      if (ru) {
        h += '<div class="rollup-summary" data-ctx="row" data-rollup="' + esc(rollupKey(item.local_path!)) + '">'
          + renderRollupSummary(ru, 'row') + '</div>';
      }
    }
    if (dragMod && mod.items.length === 0) {
      h += '<div class="drop-empty">Empty. Drag items here, or use + New.</div>';
    }

    h += '</div>';

    // Files in the folder the sync leaves out: PDFs and notes nobody listed.
    if (ordered && inLayout && mod.unlisted && mod.unlisted.length > 0) {
      h += '<div class="unlisted"><span class="unlisted-label" title="These files are in the module folder but not in its _order.yml, '
        + 'and do not declare a canvas.type, so the sync skips them.">Not synced:</span>';
      for (const f of mod.unlisted) {
        const rel = jsArg(localDir + '/' + f);
        h += '<span class="unlisted-file"><span class="clickable" onclick="openFile(\'' + rel + '\')">' + esc(f) + '</span>'
          + '<button class="action-btn new-btn" onclick="boardOp({op:\'include-item\',path:\'' + rel + '\'})" title="Add to the module, last">+ Add</button></span>';
      }
      h += '</div>';
    }
    h += '</div>';

    // Store module import data as data attributes on hidden elements
    if (canvasOnlyInMod.length > 0) {
      const moduleImportItems = canvasOnlyInMod.map(item => ({
        module_dir: modDir,
        module_name: mod.name,
        item_type: item.type,
        content_id: item.content_id || null,
        page_url: item.page_url || null,
        title: item.title,
        published: item.published,
        indent: item.indent,
        external_url: item.external_url || '',
      }));
      h += '<div class="module-import-data" data-mi="' + mi + '" data-items="'
        + esc(JSON.stringify(moduleImportItems)) + '" style="display:none"></div>';
    }
  }
  h += '</div>';

  // Folders with content that the course does not list
  const unlistedDirs = data.layout?.unlisted_dirs || [];
  if (ordered && unlistedDirs.length > 0) {
    h += '<div class="module orphan-module"><div class="module-header">'
      + '<span style="flex:1">Folders not in the course</span>'
      + '<span class="count">not listed in _modules.yml, so not synced</span></div><div class="side-list">';
    for (const d of unlistedDirs) {
      h += '<div class="side-row"><span>&#x1F4C1; ' + esc(d) + '</span>'
        + '<button class="action-btn new-btn" onclick="boardOp({op:\'add-module\',dir:\'' + jsArg(d) + '\'})" title="List it in _modules.yml as the last module">Add to course</button></div>';
    }
    h += '</div></div>';
  }

  // Files synced outside any module
  const rootItems = data.layout?.root_items || [];
  if (rootItems.length > 0) {
    h += '<div class="module orphan-module"><div class="module-header"><span style="flex:1">Outside modules</span>'
      + '<span class="count">synced as pages without a module</span></div><div class="side-list">';
    for (const f of rootItems) {
      h += '<div class="side-row"><span class="clickable" onclick="openFile(\'' + jsArg(f) + '\')">&#x1F4C4; ' + esc(f) + '</span>'
        + (online ? '<button class="action-btn sync-btn" onclick="doSync(\'' + jsArg(f) + '\')">Sync &#x2191;</button>' : '') + '</div>';
    }
    h += '</div></div>';
  }

  // Batch action bar
  h += '<div id="batch-bar" class="batch-bar hidden">';
  h += '<span id="batch-count">0 selected</span>';
  h += '<button class="action-btn sync-btn" onclick="doBatchSync()" id="batch-sync-btn" style="display:none">Sync selected &#x2191;</button>';
  h += '<button class="action-btn import-btn" onclick="doBatchImport()" id="batch-import-btn" style="display:none">Import selected &#x2193;</button>';
  h += '<button class="action-btn delete-btn" onclick="doBatchDelete()" id="batch-delete-btn">Delete selected &#x1F5D1;</button>';
  h += '<button class="action-btn" onclick="clearSelection()">Clear</button>';
  h += '</div>';

  return h;
}

function wrapHtml(body: string): string {
  return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:var(--vscode-font-family);color:var(--vscode-foreground);background:var(--vscode-editor-background);padding:16px 24px;line-height:1.5}
.header{position:sticky;top:0;z-index:200;background:var(--vscode-editor-background);display:flex;align-items:center;justify-content:space-between;margin-bottom:20px;padding:12px 0;border-bottom:1px solid var(--vscode-widget-border)}
.header h1{font-size:18px;font-weight:600}
.header .subtitle{font-size:12px;color:var(--vscode-descriptionForeground);margin-top:2px}
.refresh-btn{background:var(--vscode-button-background);color:var(--vscode-button-foreground);border:none;padding:6px 14px;border-radius:4px;cursor:pointer;font-size:12px}
.refresh-btn:hover{background:var(--vscode-button-hoverBackground)}
.dot{width:10px;height:10px;border-radius:50%;display:inline-block}
.dot.local{background:#198754}.dot.canvas-only{background:#dc3545}.dot.not-synced{background:#0d6efd}.dot.canvas-newer{background:#ffc107}.dot.local-newer{background:#20c997}.dot.unpublished{background:#6c757d}
.module{margin-bottom:16px;border:1px solid var(--vscode-widget-border);border-radius:6px;overflow:hidden}
.module-header{background:var(--vscode-sideBar-background);padding:10px 14px;font-weight:600;font-size:14px;display:flex;align-items:center;gap:8px;cursor:pointer;user-select:none}
.module-header:hover{background:var(--vscode-list-hoverBackground)}
.module-header .toggle{font-size:10px;transition:transform 0.15s}
.module-header .toggle.collapsed{transform:rotate(-90deg)}
.module-header .count{font-size:11px;color:var(--vscode-descriptionForeground);font-weight:400;margin-left:auto}
.pub-badge{font-size:10px;padding:1px 6px;border-radius:3px;font-weight:400}
.pub-badge.published{background:rgba(25,135,84,0.15);color:#198754}
.pub-badge.draft{background:rgba(108,117,125,0.15);color:#6c757d}
.module-items{padding:4px 0;display:grid;grid-template-columns:22px 24px 1fr 80px 70px minmax(100px,280px) 96px auto;align-items:center;row-gap:1px;min-height:8px}.module-items.hidden{display:none}
.item{display:grid;grid-column:1/-1;grid-template-columns:subgrid;align-items:center;cursor:default;font-size:13px}
.item>*{padding:4px 0}
.item:hover{background:var(--vscode-list-hoverBackground)}
.item.drag-item{cursor:grab}
.item.dragging,.module.dragging{opacity:0.45}
.module.drop-target{outline:2px dashed var(--vscode-focusBorder);outline-offset:-2px}
.drag-handle{cursor:grab;color:var(--vscode-descriptionForeground);font-size:14px;padding-right:2px}
.module-header[draggable="true"]{cursor:grab}
.drop-empty{grid-column:1/-1;padding:8px 14px;font-size:12px;color:var(--vscode-descriptionForeground);font-style:italic}
.hint{font-size:12px;color:var(--vscode-descriptionForeground);margin:-6px 0 14px}
.header-btns{display:flex;gap:8px}
.refresh-btn:disabled{opacity:0.45;cursor:default}
.refresh-btn.secondary{background:var(--vscode-button-secondaryBackground);color:var(--vscode-button-secondaryForeground)}
.banner{display:flex;gap:12px;align-items:flex-start;padding:10px 14px;border-radius:6px;margin-bottom:12px;border:1px solid}
.banner.error{background:rgba(220,53,69,0.10);border-color:rgba(220,53,69,0.5)}
.banner.warning{background:rgba(255,193,7,0.10);border-color:rgba(255,193,7,0.5)}
.banner.info{background:rgba(13,110,253,0.08);border-color:rgba(13,110,253,0.4)}
.banner-icon{font-size:16px;line-height:1.4}
.banner-text{flex:1;font-size:13px}
.banner-msg{font-weight:600}
.banner-action{color:var(--vscode-descriptionForeground);white-space:pre-line;margin-top:2px}
.banner-btns{display:flex;gap:6px;flex-wrap:wrap}
.pub-badge.new{background:rgba(13,110,253,0.15);color:#6ea8fe}
.pub-badge.canvas-only{background:rgba(220,53,69,0.16);color:#ff6b7a}
.module.not-in-canvas{border-style:dashed}
.item .status.excluded{background:var(--vscode-badge-background);color:var(--vscode-badge-foreground);font-weight:400}
.unlisted{display:flex;flex-wrap:wrap;gap:6px 14px;align-items:center;padding:6px 14px 8px 52px;font-size:12px;color:var(--vscode-descriptionForeground);border-top:1px dashed var(--vscode-widget-border)}
.unlisted-file{display:inline-flex;gap:6px;align-items:center}
.clickable{cursor:pointer}
.side-list{padding:4px 0}
.side-row{display:flex;justify-content:space-between;align-items:center;padding:4px 14px;font-size:13px}
.side-row:hover{background:var(--vscode-list-hoverBackground)}
.item.clickable{cursor:pointer}
.item .icon{font-size:14px;text-align:center;padding-left:4px}
.item .title{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;padding-left:6px;padding-right:8px}
.item .type-badge{font-size:10px;padding:2px 8px;border-radius:3px;background:var(--vscode-badge-background);color:var(--vscode-badge-foreground);text-align:center;width:80px;justify-self:end}
.item .status{font-size:10px;text-align:center;white-space:nowrap;padding:2px 6px;border-radius:3px;justify-self:end;font-weight:600;letter-spacing:0.02em}
.item .status.in-sync{color:var(--vscode-descriptionForeground);font-weight:400}
.item .status.local-newer{background:rgba(32,201,151,0.16);color:#20c997}
.item .status.canvas-newer{background:rgba(255,193,7,0.18);color:#ffc107}
.item .status.check-canvas{background:rgba(255,193,7,0.10);color:#d9a406;font-weight:400}
.item .status.canvas-only{background:rgba(220,53,69,0.16);color:#ff6b7a}
.item .status.not-synced{background:rgba(13,110,253,0.16);color:#6ea8fe}
.item .status.unknown{background:var(--vscode-badge-background);color:var(--vscode-badge-foreground)}
.type-badge.not-synced-badge{background:rgba(13,110,253,0.15);color:#6ea8fe}
.item .updated{font-size:10px;color:var(--vscode-descriptionForeground);text-align:right;white-space:nowrap;padding-right:4px}
.item .local-path{font-size:11px;color:var(--vscode-descriptionForeground);padding-left:10px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:280px;text-align:right}
.item .local-path.clickable{cursor:pointer;text-decoration:underline;text-decoration-style:dotted}
.item .local-path.clickable:hover{color:var(--vscode-textLink-foreground)}
.item .actions{display:flex;gap:4px;padding-right:10px;padding-left:6px;justify-self:end}
.action-btn{background:none;border:1px solid var(--vscode-button-secondaryBackground, #333);color:var(--vscode-foreground);padding:2px 8px;border-radius:3px;cursor:pointer;font-size:11px;white-space:nowrap;display:inline-flex;align-items:center;gap:3px}
.action-btn:hover{background:var(--vscode-button-secondaryHoverBackground, #444)}
.action-btn.import-btn{border-color:#0d6efd;color:#6ea8fe}
.action-btn.import-btn:hover{background:rgba(13,110,253,0.15)}
.action-btn.sync-btn{border-color:#198754;color:#75b798}
.action-btn.sync-btn:hover{background:rgba(25,135,84,0.15)}
.action-btn.diff-btn{border-color:#ffc107;color:#ffc107}
.action-btn.diff-btn:hover{background:rgba(255,193,7,0.15)}
.action-btn.delete-btn{border-color:#dc3545;color:#ea868f}
.action-btn.delete-btn:hover{background:rgba(220,53,69,0.15)}
.mod-cb{cursor:pointer;width:14px;height:14px;accent-color:#dc3545;margin-right:2px}
.unmatched-section{margin-top:24px;border:1px solid var(--vscode-widget-border);border-radius:6px;overflow:hidden}
.unmatched-section .section-header{background:var(--vscode-sideBar-background);padding:10px 14px;font-weight:600;font-size:14px;color:var(--vscode-descriptionForeground)}
.unmatched-item{padding:5px 14px 5px 20px;font-size:13px;color:var(--vscode-descriptionForeground);cursor:pointer}
.unmatched-item:hover{background:var(--vscode-list-hoverBackground)}
.loading,.error-msg{padding:40px;text-align:center;font-size:14px}
.error-msg{color:var(--vscode-errorForeground);white-space:pre-wrap}
.stats{display:flex;gap:20px;margin-bottom:16px;font-size:13px}
.stat{display:flex;align-items:center;gap:6px}
.stat .num{font-weight:600;font-size:18px}
.cb-cell{display:flex;align-items:center;justify-content:center}
.item-cb{cursor:pointer;width:14px;height:14px;accent-color:#0d6efd}
.module-action-btn{font-size:11px;padding:2px 8px;margin-left:4px}
.new-btn{border-color:#6c757d;color:var(--vscode-foreground)}
.new-btn:hover{background:rgba(108,117,125,0.15)}
.new-menu{position:fixed;z-index:1000;background:var(--vscode-menu-background,var(--vscode-sideBar-background));border:1px solid var(--vscode-widget-border);border-radius:4px;padding:4px 0;min-width:160px;box-shadow:0 4px 12px rgba(0,0,0,0.3)}
.new-menu.hidden{display:none}
.new-menu-item{padding:6px 14px;font-size:12px;cursor:pointer;white-space:nowrap}
.new-menu-item:hover{background:var(--vscode-list-hoverBackground)}
.orphan-module{border-color:rgba(13,110,253,0.3)}
.pub-toggle{cursor:pointer}.pub-toggle:hover{opacity:0.7}
.pub-btn{font-size:10px;padding:2px 6px;min-width:76px;font-weight:600;justify-content:center}
.pub-btn.pub-on{border-color:#198754;color:#75b798;background:rgba(25,135,84,0.12)}
.pub-btn.pub-on:hover{background:rgba(25,135,84,0.22)}
.pub-btn.pub-off{border-color:var(--vscode-descriptionForeground);color:var(--vscode-foreground);background:none;font-weight:400}
.pub-btn.pub-off:hover{background:var(--vscode-list-hoverBackground)}
.batch-bar{position:fixed;bottom:0;left:0;right:0;background:var(--vscode-sideBar-background);border-top:2px solid var(--vscode-focusBorder);padding:10px 24px;display:flex;align-items:center;gap:12px;z-index:100;font-size:13px}
.batch-bar.hidden{display:none}
#batch-count{font-weight:600;min-width:90px}
.item .title.has-rollup{display:flex;align-items:center;gap:8px;min-width:0}
.item .title-text{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.rollup-summary{grid-column:1/-1;padding:2px 10px 8px 52px;font-size:12px}
${ROLLUP_CSS}
</style>
</head>
<body>
<div id="app">${body}</div>
<script>
const vscode=acquireVsCodeApi();
function toggleModule(i){var el=document.getElementById("items-"+i),t=document.getElementById("toggle-"+i);el.classList.toggle("hidden");t.classList.toggle("collapsed")}
function openFile(p){vscode.postMessage({type:"openFile",path:p})}
function refresh(){vscode.postMessage({type:"refresh"})}
function openUrl(u){vscode.postMessage({type:"openUrl",url:u})}
function doSync(p){vscode.postMessage({type:"sync",localPath:p})}
function doSetPublished(d){vscode.postMessage({type:"setPublished",data:d})}
function doCreateModule(){vscode.postMessage({type:"createModule"})}
function checkCanvas(){vscode.postMessage({type:"refresh",withDrift:true})}
function doDiff(p){vscode.postMessage({type:"diff",localPath:p})}
function doImport(jsonStr){try{var d=JSON.parse(jsonStr);vscode.postMessage({type:"import",itemData:d})}catch(e){console.error(e)}}
function toggleNewMenu(mi){
  var menu=document.getElementById("new-menu-"+mi);
  var wasHidden=menu.classList.contains("hidden");
  document.querySelectorAll(".new-menu").forEach(function(m){m.classList.add("hidden");});
  if(wasHidden){
    var btn=menu.previousElementSibling;
    var r=btn.getBoundingClientRect();
    menu.classList.remove("hidden");
    var mh=menu.offsetHeight||180;
    var vh=window.innerHeight;
    var top=(r.bottom+mh+4<=vh)?(r.bottom+2):Math.max(4,r.top-mh-2);
    menu.style.top=top+"px";
    menu.style.left=Math.max(4,r.right-160)+"px";
  }
}
function doCreateItem(dir,type,name){
  document.querySelectorAll(".new-menu").forEach(function(m){m.classList.add("hidden");});
  vscode.postMessage({type:"createItem",moduleDir:dir,itemType:type,moduleName:name});
}
function openConfig(){vscode.postMessage({type:"openConfig"})}
function convertLayout(){vscode.postMessage({type:"convert"})}
function boardOp(op){vscode.postMessage({type:"boardOp",op:op})}
function doSyncModule(mi){
  var el=document.querySelector('.module-sync-data[data-mi="'+mi+'"]');
  if(!el)return;
  try{vscode.postMessage({type:"batchSync",paths:JSON.parse(el.getAttribute('data-paths'))});}catch(e){console.error(e)}
}

// ── Drag and drop ──
// Rows move live while dragging; on drop the new place is read back from
// the DOM and sent as one board edit. A drop anywhere else refreshes, which
// puts everything back.
var drag=null;   // {kind:'item'|'module', el, from, fromIndex, dropped}
function dragRows(container){return Array.prototype.slice.call(container.querySelectorAll(':scope > .item.drag-item'));}
function layoutModules(){return Array.prototype.slice.call(document.querySelectorAll('#modules > .module[data-inlayout="1"]'));}
document.addEventListener('dragstart',function(e){
  var t=e.target;
  if(!t||!t.closest)return;
  var row=t.closest('.item.drag-item');
  if(row){
    var c=row.parentElement;
    drag={kind:'item',el:row,from:c.getAttribute('data-dir'),fromIndex:dragRows(c).indexOf(row),dropped:false};
    e.dataTransfer.effectAllowed='move';
    e.dataTransfer.setData('text/plain',row.getAttribute('data-path'));
    document.querySelectorAll('.rollup-summary').forEach(function(x){x.style.display='none'});
  }else{
    var hdr=t.closest('.module-header[draggable="true"]');
    if(!hdr)return;
    var mod=hdr.parentElement;
    drag={kind:'module',el:mod,fromIndex:layoutModules().indexOf(mod),dropped:false};
    e.dataTransfer.effectAllowed='move';
    e.dataTransfer.setData('text/plain',mod.getAttribute('data-dir'));
  }
  setTimeout(function(){if(drag)drag.el.classList.add('dragging')},0);
});
function rowAfter(container,y){
  var rows=Array.prototype.slice.call(container.querySelectorAll(':scope > .item:not(.dragging)'));
  for(var i=0;i<rows.length;i++){var r=rows[i].getBoundingClientRect();if(y<r.top+r.height/2)return rows[i];}
  return null;
}
document.addEventListener('dragover',function(e){
  if(!drag)return;
  var t=e.target;if(!t||!t.closest)return;
  var mod=t.closest('#modules > .module[data-inlayout="1"]');
  if(!mod)return;
  if(drag.kind==='item'){
    var c=mod.querySelector('.module-items');
    e.preventDefault();
    if(c.classList.contains('hidden'))return;
    var empty=c.querySelector('.drop-empty');if(empty)empty.remove();
    var after=rowAfter(c,e.clientY);
    if(after!==drag.el&&(after?after.previousElementSibling!==drag.el:c.lastElementChild!==drag.el)){
      if(after)c.insertBefore(drag.el,after);else c.appendChild(drag.el);
    }
  }else{
    e.preventDefault();
    if(mod===drag.el)return;
    var r=mod.getBoundingClientRect();
    var before=e.clientY<r.top+r.height/2;
    var parent=mod.parentElement;
    if(before)parent.insertBefore(drag.el,mod);else parent.insertBefore(drag.el,mod.nextElementSibling);
  }
});
document.addEventListener('drop',function(e){
  if(!drag)return;
  e.preventDefault();
  drag.dropped=true;
  if(drag.kind==='item'){
    var c=drag.el.parentElement;
    var to=c.getAttribute('data-dir');
    var idx=dragRows(c).indexOf(drag.el);
    if(to===drag.from&&idx===drag.fromIndex){drag.el.classList.remove('dragging');drag=null;return;}
    boardOp({op:'move-item',path:drag.el.getAttribute('data-path'),to_dir:to,index:idx});
  }else{
    var i=layoutModules().indexOf(drag.el);
    if(i===drag.fromIndex){drag.el.classList.remove('dragging');drag=null;return;}
    boardOp({op:'move-module',dir:drag.el.getAttribute('data-dir'),index:i});
  }
  drag=null;
});
document.addEventListener('dragend',function(){
  if(drag&&!drag.dropped){refresh();}
  if(drag)drag.el.classList.remove('dragging');
  drag=null;
});
document.addEventListener("click",function(e){
  if(!e.target.closest(".new-dropdown")){
    document.querySelectorAll(".new-menu").forEach(function(m){m.classList.add("hidden");});
  }
});
function doImportModule(mi){
  var el=document.querySelector('.module-import-data[data-mi="'+mi+'"]');
  if(!el){console.error('No import data for module',mi);return;}
  try{
    var items=JSON.parse(el.getAttribute('data-items'));
    vscode.postMessage({type:"importModule",items:items});
  }catch(e){console.error('doImportModule error',e);}
}
function onCheckChanged(){
  var cbs=document.querySelectorAll('.item-cb:checked');
  var bar=document.getElementById('batch-bar');
  var countEl=document.getElementById('batch-count');
  var syncBtn=document.getElementById('batch-sync-btn');
  var importBtn=document.getElementById('batch-import-btn');
  var deleteBtn=document.getElementById('batch-delete-btn');
  var syncCount=0,importCount=0;
  cbs.forEach(function(cb){if(cb.dataset.kind==='sync')syncCount++;else if(cb.dataset.kind==='import')importCount++;});
  var total=cbs.length;
  if(total===0){bar.classList.add('hidden');return;}
  bar.classList.remove('hidden');
  countEl.textContent=total+' selected';
  syncBtn.style.display=syncCount>0?'inline-flex':'none';
  if(syncCount>0)syncBtn.textContent='Sync '+syncCount+' \\u2191';
  importBtn.style.display=importCount>0?'inline-flex':'none';
  if(importCount>0)importBtn.textContent='Import '+importCount+' \\u2193';
  deleteBtn.textContent='Delete '+total;
}
function doBatchSync(){
  var cbs=document.querySelectorAll('.item-cb:checked[data-kind="sync"]');
  var paths=[];cbs.forEach(function(cb){paths.push(cb.dataset.value);});
  if(paths.length===0)return;
  vscode.postMessage({type:"batchSync",paths:paths});
  clearSelection();
}
function doBatchImport(){
  var cbs=document.querySelectorAll('.item-cb:checked[data-kind="import"]');
  var items=[];cbs.forEach(function(cb){try{items.push(JSON.parse(cb.dataset.value))}catch(e){}});
  if(items.length===0)return;
  vscode.postMessage({type:"batchImport",items:items});
  clearSelection();
}
function doBatchDelete(){
  var cbs=document.querySelectorAll('.item-cb:checked');
  var items=[];
  cbs.forEach(function(cb){
    var raw=cb.getAttribute('data-delete');
    if(!raw)return;
    try{items.push(JSON.parse(raw));}catch(e){}
  });
  if(items.length===0)return;
  vscode.postMessage({type:"batchDelete",items:items});
}
function clearSelection(){
  document.querySelectorAll('.item-cb:checked').forEach(function(cb){cb.checked=false;});
  onCheckChanged();
}
${ROLLUP_SCRIPT}
</script>
</body>
</html>`;
}
