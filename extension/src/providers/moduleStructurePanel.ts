import * as vscode from 'vscode';
import * as path from 'path';
import { spawn } from 'child_process';
import { resolvePython, resolveCqsRoot } from '../python/venvResolver';
import { getWorkspaceRoot } from '../config/configLoader';
import { setSyncing } from './statusBar';

// ── Module Structure Panel ──────────────────────────────────────────
//
// Fetches the Canvas module structure and displays it in a webview,
// showing which items exist locally and which are Canvas-only.
// All HTML is rendered server-side (in TypeScript) to avoid webview
// messaging / document.write issues.

let currentPanel: vscode.WebviewPanel | undefined;
const log = vscode.window.createOutputChannel('CQS Module Structure');

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
  local_only?: boolean;
}

interface Module {
  name: string;
  id: number;
  published: boolean;
  items: ModuleItem[];
  local_dir?: string;
}

interface LocalOnlyModule {
  dir_name: string;
  files: string[];
}

interface StructureData {
  course_name: string;
  course_code: string;
  course_id: number;
  modules: Module[];
  local_only_modules?: LocalOnlyModule[];
  unmatched_local_files?: string[]; // backward compat
}

export async function openModuleStructurePanel(extensionPath: string): Promise<void> {
  log.appendLine('[open] called');

  if (currentPanel) {
    currentPanel.reveal();
    await refreshPanel(extensionPath);
    return;
  }

  currentPanel = vscode.window.createWebviewPanel(
    'cqs.moduleStructure',
    'Canvas Module Structure',
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
    } else if (msg.type === 'refresh') {
      await refreshPanel(extensionPath);
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
      await handleCreateItem(extensionPath, msg.moduleDir, msg.itemType);
    } else if (msg.type === 'createModule') {
      await handleCreateModule(extensionPath);
    }
  });

  currentPanel.onDidDispose(() => { currentPanel = undefined; });

  // Show loading, then fetch and render
  currentPanel.webview.html = wrapHtml('<div class="loading">Fetching module structure from Canvas...</div>');
  await refreshPanel(extensionPath);
}

async function refreshPanel(extensionPath: string): Promise<void> {
  if (!currentPanel) return;
  const result = await fetchStructure(extensionPath);
  if (!currentPanel) return;

  if (result.type === 'error') {
    currentPanel.webview.html = wrapHtml(
      '<div class="error-msg">' + esc(result.message || 'Unknown error') + '</div>'
    );
  } else if (result.data) {
    currentPanel.webview.html = wrapHtml(renderBody(result.data));
  }
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

async function handleCreateItem(extensionPath: string, moduleDir: string, itemType: string): Promise<void> {
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

  // Determine next file index
  const existing = fs.readdirSync(modPath).filter(f => /^\d{2,}_/.test(f)).sort();
  let nextIdx = 1;
  if (existing.length > 0) {
    const m = existing[existing.length - 1].match(/^(\d+)/);
    if (m) nextIdx = parseInt(m[1], 10) + 1;
  }

  const prefix = nextIdx.toString().padStart(2, '0');
  const label = TYPE_LABELS[itemType] || 'Item';
  const filename = `${prefix}_New_${label}.qmd`;
  const filePath = path.join(modPath, filename);

  fs.writeFileSync(filePath, template, 'utf-8');
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

// ── Fetch from Python ───────────────────────────────────────────────

async function fetchStructure(
  extensionPath: string
): Promise<{ type: string; data?: StructureData; message?: string }> {
  const workspaceRoot = getWorkspaceRoot();
  if (!workspaceRoot) return { type: 'error', message: 'No workspace folder open.' };

  const pythonPath = resolvePython();
  if (!pythonPath) return { type: 'error', message: 'Python venv not found. Run install.ps1.' };

  const cqsRoot = resolveCqsRoot(extensionPath);
  const scriptPath = path.join(cqsRoot, 'sync_to_canvas.py');
  const args = [scriptPath, workspaceRoot, '--module-structure'];

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
        resolve({ type: 'error', message: 'Python exit ' + code + ': ' + stderr.slice(0, 500) });
        return;
      }
      const line = stdout.split('\n').find(l => l.trim().startsWith('MODULE_STRUCTURE_JSON:'));
      if (!line) {
        resolve({ type: 'error', message: (stderr || stdout).slice(0, 500) || 'No output from Python script.' });
        return;
      }
      try {
        const data = JSON.parse(line.trim().replace('MODULE_STRUCTURE_JSON:', ''));
        resolve({ type: 'structure', data });
      } catch (e) {
        resolve({ type: 'error', message: 'JSON parse error: ' + e });
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

function renderBody(data: StructureData): string {
  // Compute counts excluding local-only items from canvas totals
  const canvasItems = data.modules.reduce((s, m) => s + m.items.filter(i => !i.local_only).length, 0);
  const syncedItems = data.modules.reduce((s, m) => s + m.items.filter(i => i.local_path && !i.local_only).length, 0);
  const canvasOnly = canvasItems - syncedItems;
  const notSyncedItems = data.modules.reduce((s, m) => s + m.items.filter(i => i.local_only).length, 0);

  let h = '';

  // Header
  h += '<div class="header"><div>';
  const courseTitle = data.course_code
    ? esc(data.course_code) + ' &mdash; ' + esc(data.course_name)
    : esc(data.course_name);
  h += '<h1>' + courseTitle + '</h1>';
  const infoParts: string[] = [];
  infoParts.push('ID: ' + data.course_id);
  if ((data as any).total_students != null) infoParts.push((data as any).total_students + ' students');
  if ((data as any).term) infoParts.push('Term: ' + esc((data as any).term));
  if ((data as any).workflow_state) infoParts.push(esc((data as any).workflow_state));
  infoParts.push(data.modules.length + ' modules');
  infoParts.push(canvasItems + ' items');
  h += '<div class="subtitle">' + infoParts.join(' &middot; ') + '</div>';
  h += '</div><div style="display:flex;gap:8px"><button class="refresh-btn" onclick="doCreateModule()" title="Create a new Canvas module">+ New Module</button><button class="refresh-btn" onclick="refresh()">Refresh</button></div></div>';

  // Stats
  h += '<div class="stats">';
  h += '<div class="stat"><span class="num" style="color:#198754">' + syncedItems + '</span> synced</div>';
  h += '<div class="stat"><span class="num" style="color:#dc3545">' + canvasOnly + '</span> Canvas-only</div>';
  if (notSyncedItems > 0) {
    h += '<div class="stat"><span class="num" style="color:#0d6efd">' + notSyncedItems + '</span> not synced</div>';
  }
  h += '</div>';

  // Legend
  h += '<div class="legend">';
  h += '<div class="legend-item"><span class="dot local"></span> In sync</div>';
  h += '<div class="legend-item"><span class="dot local-newer"></span> Local newer</div>';
  h += '<div class="legend-item"><span class="dot canvas-newer"></span> Canvas newer</div>';
  h += '<div class="legend-item"><span class="dot canvas-only"></span> Canvas-only</div>';
  h += '<div class="legend-item"><span class="dot not-synced"></span> Not synced</div>';
  h += '</div>';

  // Modules
  for (let mi = 0; mi < data.modules.length; mi++) {
    const mod = data.modules[mi];
    const modCanvasItems = mod.items.filter(i => !i.local_only);
    const modLocalOnly = mod.items.filter(i => i.local_only);
    const modSynced = modCanvasItems.filter(i => i.local_path).length;
    const pubCls = mod.published ? 'published' : 'draft';
    const pubLbl = mod.published ? 'Published' : 'Draft';

    // Find local module dir for import/create
    const modDirGuess = mod.local_dir
      || mod.items.find(i => i.local_path)?.local_path?.split('/')[0]
      || ((mi + 1).toString().padStart(2, '0') + '_' + mod.name.replace(/[^a-zA-Z0-9åäöÅÄÖ _-]/g, '').replace(/\s+/g, ' ').trim());

    h += '<div class="module">';
    h += '<div class="module-header">';
    h += '<span class="toggle" id="toggle-' + mi + '" onclick="toggleModule(' + mi + ')">&#9660;</span> ';
    h += '<span onclick="toggleModule(' + mi + ')" style="flex:1;cursor:pointer">' + esc(mod.name) + '</span>';
    const togglePubMod = !mod.published;
    h += ' <span class="pub-badge ' + pubCls + ' pub-toggle" onclick="event.stopPropagation();doSetPublished({target:\'module\',module_id:' + mod.id + ',published:' + togglePubMod + '})" title="Click to ' + (togglePubMod ? 'publish' : 'unpublish') + '">' + pubLbl + '</span>';

    // "Import Module" button if there are canvas-only items (exclude local-only)
    const canvasOnlyInMod = modCanvasItems.filter(i => !i.local_path);
    if (canvasOnlyInMod.length > 0) {
      h += '<button class="action-btn import-btn module-action-btn" onclick="event.stopPropagation();doImportModule(' + mi + ')" title="Import all '
        + canvasOnlyInMod.length + ' Canvas-only items">Import &#x2193; (' + canvasOnlyInMod.length + ')</button>';
    }

    // "+ New" dropdown
    h += '<div class="new-dropdown" style="position:relative;display:inline-block">';
    h += '<button class="action-btn new-btn module-action-btn" onclick="event.stopPropagation();toggleNewMenu(' + mi + ')" title="Create new item in this module">+ New &#x25BE;</button>';
    h += '<div class="new-menu hidden" id="new-menu-' + mi + '">';
    const escDir = esc(modDirGuess).replace(/'/g, "\\'");
    h += '<div class="new-menu-item" onclick="doCreateItem(\'' + escDir + '\',\'page\')">&#x1F4C4; Page</div>';
    h += '<div class="new-menu-item" onclick="doCreateItem(\'' + escDir + '\',\'assignment\')">&#x1F4DD; Assignment</div>';
    h += '<div class="new-menu-item" onclick="doCreateItem(\'' + escDir + '\',\'quiz\')">&#x2753; Quiz</div>';
    h += '<div class="new-menu-item" onclick="doCreateItem(\'' + escDir + '\',\'external_url\')">&#x1F517; External URL</div>';
    h += '</div></div>';

    // Count
    let countText = modSynced + '/' + modCanvasItems.length + ' synced';
    if (modLocalOnly.length > 0) {
      countText += ' + ' + modLocalOnly.length + ' local';
    }
    h += '<span class="count">' + countText + '</span>';
    h += '</div>';
    h += '<div class="module-items" id="items-' + mi + '">';

    for (const item of mod.items) {
      const isLocalOnly = !!item.local_only;
      const hasLocal = !!item.local_path;
      const icon = isLocalOnly ? '&#x1F4C4;' : (TYPE_ICONS[item.type] || '&#x1F4E6;');

      // Determine dot color based on sync state
      // Compare Canvas updated_at and local mtime against last_synced_at
      let dotCls: string;
      if (isLocalOnly) {
        dotCls = 'not-synced';
      } else if (!hasLocal) {
        dotCls = 'canvas-only';
      } else if (item.last_synced_at) {
        const syncTime = new Date(item.last_synced_at).getTime();
        const canvasTime = item.updated_at ? new Date(item.updated_at).getTime() : 0;
        const localTime = item.local_mtime ? new Date(item.local_mtime).getTime() : 0;
        const canvasChanged = canvasTime > syncTime + 60000;
        const localChanged = localTime > syncTime + 60000;
        if (canvasChanged && localChanged) {
          dotCls = 'canvas-newer';
        } else if (canvasChanged) {
          dotCls = 'canvas-newer';
        } else if (localChanged) {
          dotCls = 'local-newer';
        } else {
          dotCls = 'local';
        }
      } else if (item.updated_at && item.local_mtime) {
        const ct = new Date(item.updated_at).getTime();
        const lt = new Date(item.local_mtime).getTime();
        if (Math.abs(ct - lt) < 120000) {
          dotCls = 'local';
        } else if (ct > lt) {
          dotCls = 'canvas-newer';
        } else {
          dotCls = 'local-newer';
        }
      } else {
        dotCls = 'local';
      }
      const unpubCls = (item.published === false && !isLocalOnly) ? ' unpublished' : '';
      const clickCls = item.html_url ? ' clickable' : '';
      const safePath = hasLocal ? esc(item.local_path!.replace(/\\/g, '/')) : '';
      // Title always links to Canvas
      let titleClick = '';
      if (item.html_url) {
        titleClick = ' onclick="openUrl(\'' + esc(item.html_url).replace(/'/g, "\\'") + '\')"';
      }

      // Badge text
      const badgeText = isLocalOnly ? 'Not synced' : item.type;
      const badgeCls = isLocalOnly ? ' not-synced-badge' : '';

      // Build import data for canvas-only items
      const importDataObj = (!hasLocal && !isLocalOnly) ? {
        module_dir: modDirGuess,
        item_type: item.type,
        content_id: item.content_id || null,
        page_url: item.page_url || null,
        title: item.title,
        published: item.published,
        indent: item.indent,
        external_url: item.external_url || '',
      } : null;

      // 7 grid cells: checkbox, dot, icon, title, badge, path, actions
      const dataKind = hasLocal ? 'sync' : 'import';
      const dataVal = hasLocal ? safePath : (importDataObj ? esc(JSON.stringify(importDataObj)) : '');
      h += '<div class="item' + clickCls + '" data-kind="' + dataKind + '" data-value="' + dataVal.replace(/"/g, '&quot;') + '">';
      h += '<span class="cb-cell"><input type="checkbox" class="item-cb" onclick="event.stopPropagation();onCheckChanged()" data-kind="' + dataKind + '" data-value="' + dataVal.replace(/"/g, '&quot;') + '"></span>';
      h += '<span class="status-dot ' + dotCls + unpubCls + '"></span>';
      h += '<span class="icon">' + icon + '</span>';
      h += '<span class="title"' + titleClick + '>' + esc(item.title) + '</span>';
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

      // Action buttons
      h += '<span class="actions">';
      // Publish/unpublish toggle for Canvas items (not local-only)
      if (!isLocalOnly && item.module_item_id) {
        const togglePub = !item.published;
        const pubIcon = item.published ? '&#x1F441;' : '&#x1F441;&#xFE0F;&#x200D;&#x1F5E8;';
        const pubTitle = item.published ? 'Unpublish' : 'Publish';
        const pubBtnCls = item.published ? 'pub-on' : 'pub-off';
        h += '<button class="action-btn pub-btn ' + pubBtnCls + '" onclick="event.stopPropagation();doSetPublished({target:\'item\',module_id:'
          + mod.id + ',item_id:' + item.module_item_id + ',published:' + togglePub + '})" title="' + pubTitle + '">'
          + (item.published ? '&#x1F7E2;' : '&#x26AA;') + '</button>';
      }
      if (isLocalOnly || hasLocal) {
        // Has local file: Sync button
        h += '<button class="action-btn sync-btn" onclick="event.stopPropagation();doSync(\''
          + safePath.replace(/'/g, "\\'") + '\')" title="Upload to Canvas">Sync &#x2191;</button>';
      } else {
        // Canvas-only: Import button
        const importData = esc(JSON.stringify(importDataObj));
        h += '<button class="action-btn import-btn" onclick="event.stopPropagation();doImport(\''
          + importData.replace(/'/g, '&#39;') + '\')" title="Import from Canvas">Import &#x2193;</button>';
      }
      h += '</span>';
      h += '</div>';
    }

    h += '</div></div>';

    // Store module import data as data attributes on hidden elements
    if (canvasOnlyInMod.length > 0) {
      const moduleImportItems = canvasOnlyInMod.map(item => ({
        module_dir: modDirGuess,
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

  // Local-only modules (no Canvas match)
  if (data.local_only_modules && data.local_only_modules.length > 0) {
    h += '<div class="module orphan-module">';
    h += '<div class="module-header">';
    h += '<span class="toggle" id="toggle-orphan" onclick="toggleModule(\'orphan\')">&#9660;</span> ';
    h += '<span onclick="toggleModule(\'orphan\')" style="flex:1;cursor:pointer">Local Only (no Canvas module)</span>';
    const orphanCount = data.local_only_modules.reduce((s, m) => s + m.files.length, 0);
    h += '<span class="count">' + orphanCount + ' files</span>';
    h += '</div>';
    h += '<div class="module-items" id="items-orphan">';
    for (const om of data.local_only_modules) {
      for (const f of om.files) {
        const safeF = esc(f.replace(/\\/g, '/'));
        h += '<div class="item clickable">';
        h += '<span class="cb-cell"></span>';
        h += '<span class="status-dot not-synced"></span>';
        h += '<span class="icon">&#x1F4C4;</span>';
        h += '<span class="title" onclick="openFile(\'' + safeF.replace(/'/g, "\\'") + '\')">' + esc(f) + '</span>';
        h += '<span class="type-badge not-synced-badge">No module</span>';
        h += '<span class="updated"></span>';
        h += '<span class="local-path"></span>';
        h += '<span class="actions"></span>';
        h += '</div>';
      }
    }
    h += '</div></div>';
  }

  // Batch action bar
  h += '<div id="batch-bar" class="batch-bar hidden">';
  h += '<span id="batch-count">0 selected</span>';
  h += '<button class="action-btn sync-btn" onclick="doBatchSync()" id="batch-sync-btn" style="display:none">Sync selected &#x2191;</button>';
  h += '<button class="action-btn import-btn" onclick="doBatchImport()" id="batch-import-btn" style="display:none">Import selected &#x2193;</button>';
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
.legend{display:flex;gap:16px;margin-bottom:16px;font-size:12px;color:var(--vscode-descriptionForeground)}
.legend-item{display:flex;align-items:center;gap:4px}
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
.module-items{padding:4px 0;display:grid;grid-template-columns:22px 12px 24px 1fr 80px 70px minmax(100px,280px) auto;align-items:center;row-gap:1px}.module-items.hidden{display:none}
.item{display:contents;cursor:default;font-size:13px}
.item>*{padding:4px 0}
.item:hover>*{background:var(--vscode-list-hoverBackground)}
.item.clickable{cursor:pointer}
.item .icon{font-size:14px;text-align:center;padding-left:4px}
.item .title{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;padding-left:6px;padding-right:8px}
.item .type-badge{font-size:10px;padding:2px 8px;border-radius:3px;background:var(--vscode-badge-background);color:var(--vscode-badge-foreground);text-align:center;width:80px;justify-self:end}
.item .status-dot{width:8px;height:8px;border-radius:50%;justify-self:center}
.item .status-dot.local{background:#198754}.item .status-dot.canvas-only{background:#dc3545}.item .status-dot.not-synced{background:#0d6efd}.item .status-dot.canvas-newer{background:#ffc107}.item .status-dot.local-newer{background:#20c997}.item .status-dot.unpublished{opacity:0.5}
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
.pub-btn{font-size:12px;padding:1px 4px;min-width:24px;border:none}
.pub-btn.pub-on{color:#198754}.pub-btn.pub-off{color:#6c757d}
.batch-bar{position:fixed;bottom:0;left:0;right:0;background:var(--vscode-sideBar-background);border-top:2px solid var(--vscode-focusBorder);padding:10px 24px;display:flex;align-items:center;gap:12px;z-index:100;font-size:13px}
.batch-bar.hidden{display:none}
#batch-count{font-weight:600;min-width:90px}
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
function doCreateItem(dir,type){
  document.querySelectorAll(".new-menu").forEach(function(m){m.classList.add("hidden");});
  vscode.postMessage({type:"createItem",moduleDir:dir,itemType:type});
}
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
  var syncCount=0,importCount=0;
  cbs.forEach(function(cb){if(cb.dataset.kind==='sync')syncCount++;else importCount++;});
  var total=syncCount+importCount;
  if(total===0){bar.classList.add('hidden');return;}
  bar.classList.remove('hidden');
  countEl.textContent=total+' selected';
  syncBtn.style.display=syncCount>0?'inline-flex':'none';
  if(syncCount>0)syncBtn.textContent='Sync '+syncCount+' \\u2191';
  importBtn.style.display=importCount>0?'inline-flex':'none';
  if(importCount>0)importBtn.textContent='Import '+importCount+' \\u2193';
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
function clearSelection(){
  document.querySelectorAll('.item-cb:checked').forEach(function(cb){cb.checked=false;});
  onCheckChanged();
}
</script>
</body>
</html>`;
}
