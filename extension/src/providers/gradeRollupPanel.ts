import * as vscode from 'vscode';
import * as path from 'path';
import { getWorkspaceRoot } from '../config/configLoader';
import {
  rollups, loadRollups, handleRollup, subscribeRollups,
  renderRollupCtl, renderRollupSummary, esc, ROLLUP_CSS, ROLLUP_SCRIPT,
} from './rollups';

// ── Grade Rollup Panel ──────────────────────────────────────────────
//
// Every rollup in the course in one place. Opening it reads local files only,
// so before anything is pressed it is a configuration inspector: the target,
// each requirement, and whether each has a Canvas id. Those are the things
// that break, and all of them are visible offline. Canvas is asked per rollup,
// with the same button the Module Structure panel puts in the target's row.

let currentPanel: vscode.WebviewPanel | undefined;

export async function openGradeRollupPanel(extensionPath: string): Promise<void> {
  if (currentPanel) {
    currentPanel.reveal();
    await refresh(extensionPath);
    return;
  }

  currentPanel = vscode.window.createWebviewPanel(
    'cqs.gradeRollup',
    'Grade Rollup',
    vscode.ViewColumn.One,
    { enableScripts: true, retainContextWhenHidden: true }
  );

  const panel = currentPanel;
  const sub = subscribeRollups((m) => { panel.webview.postMessage(m); });

  panel.webview.onDidReceiveMessage(async (msg) => {
    if (msg.type === 'rollup') {
      await handleRollup(extensionPath, msg.action, msg.target);
    } else if (msg.type === 'openFile') {
      const ws = getWorkspaceRoot();
      if (ws && msg.path) vscode.window.showTextDocument(vscode.Uri.file(path.join(ws, msg.path)));
    } else if (msg.type === 'refresh') {
      await refresh(extensionPath);
    }
  });
  panel.onDidDispose(() => { currentPanel = undefined; sub.dispose(); });

  panel.webview.html = wrapHtml('<div class="loading">Reading rollups from frontmatter…</div>');
  await refresh(extensionPath);
}

async function refresh(extensionPath: string): Promise<void> {
  const ok = await loadRollups(extensionPath);
  if (!currentPanel) return;
  currentPanel.webview.html = wrapHtml(ok ? renderBody() : renderUnavailable());
}

function renderUnavailable(): string {
  return '<div class="empty"><h2>Grade rollups are not available</h2>'
    + '<p>The installed CanvasQuartoSync has no <code>rollup.py</code>, or no course folder is open. '
    + 'Update the tool, then press Refresh.</p>'
    + '<button class="refresh-btn" onclick="refresh()">Refresh</button></div>';
}

const EXAMPLE = `---
title: "Laboration"
canvas:
  type: assignment
  grading_type: pass_fail
  points: 0
  submission_types: [none]
  omit_from_final_grade: true
  rollup:
    requires:
      - 01_Lab_One.qmd
      - 02_Lab_Two.qmd
    pass_at: 1
---`;

function renderBody(): string {
  const list = [...rollups.values()].sort((a, b) => a.decl.target.localeCompare(b.decl.target));

  let h = '<div class="header"><div><h1>Grade Rollup</h1>';
  h += '<div class="subtitle">' + list.length + ' rollup' + (list.length === 1 ? '' : 's')
    + ' &middot; read from frontmatter, nothing asked of Canvas until you press a button</div></div>';
  h += '<button class="refresh-btn" onclick="refresh()">Refresh</button></div>';

  if (list.length === 0) {
    h += '<div class="empty">';
    h += '<p>A <b>rollup</b> marks one assignment for every student who has passed several others. '
      + 'Use it when a records system such as LADOK wants <b>one</b> result for an examination module, '
      + 'but the course examines it in several assignments.</p>';
    h += '<p>Declare it in the frontmatter of the assignment that holds the result. '
      + 'Paths in <code>requires</code> are relative to that file:</p>';
    h += '<pre>' + esc(EXAMPLE) + '</pre>';
    h += '<p class="dim">Sync the target and its requirements once so they have Canvas ids, then press Refresh. '
      + 'Grades are only ever raised, never withdrawn.</p>';
    h += '</div>';
    return h;
  }

  for (const st of list) {
    const key = st.decl.target;
    h += '<div class="card">';
    h += '<div class="card-head"><span class="card-title">' + esc(st.decl.name) + '</span>';
    h += '<span class="rollup-ctl" data-rollup="' + esc(key) + '">' + renderRollupCtl(st) + '</span></div>';
    h += '<div class="card-body rollup-summary" data-ctx="card" data-rollup="' + esc(key) + '">'
      + renderRollupSummary(st, 'card') + '</div>';
    h += '</div>';
  }
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
body{font-family:var(--vscode-font-family);color:var(--vscode-foreground);background:var(--vscode-editor-background);padding:16px 24px;line-height:1.5;font-size:13px}
.header{display:flex;align-items:center;justify-content:space-between;margin-bottom:20px;padding:12px 0;border-bottom:1px solid var(--vscode-widget-border)}
.header h1{font-size:18px;font-weight:600}
.subtitle,.dim{color:var(--vscode-descriptionForeground)}
.subtitle{font-size:12px;margin-top:2px}
.refresh-btn{background:var(--vscode-button-background);color:var(--vscode-button-foreground);border:none;padding:6px 14px;border-radius:4px;cursor:pointer;font-size:12px}
.refresh-btn:hover{background:var(--vscode-button-hoverBackground)}
.card{margin-bottom:16px;border:1px solid var(--vscode-widget-border);border-radius:6px;overflow:hidden}
.card-head{background:var(--vscode-sideBar-background);padding:10px 14px;display:flex;align-items:center;gap:12px}
.card-title{font-weight:600;font-size:14px;flex:1}
.card-body{padding:10px 14px;font-size:12px}
.loading{padding:40px;text-align:center;font-size:14px}
.empty{max-width:640px}
.empty p{margin:10px 0}
.empty pre{background:var(--vscode-textCodeBlock-background,rgba(128,128,128,0.12));padding:10px 14px;border-radius:4px;font-family:var(--vscode-editor-font-family);font-size:12px;user-select:all}
code{font-family:var(--vscode-editor-font-family)}
${ROLLUP_CSS}
</style>
</head>
<body>
${body}
<script>
const vscode=acquireVsCodeApi();
function refresh(){vscode.postMessage({type:"refresh"})}
function openFile(p){vscode.postMessage({type:"openFile",path:p})}
${ROLLUP_SCRIPT}
</script>
</body>
</html>`;
}
