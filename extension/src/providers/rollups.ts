import * as vscode from 'vscode';
import * as path from 'path';
import * as fs from 'fs';
import { spawn } from 'child_process';
import { resolvePython, resolveCqsRoot } from '../python/venvResolver';
import { getWorkspaceRoot } from '../config/configLoader';
import { setSyncing } from './statusBar';

// ── Grade rollups ───────────────────────────────────────────────────
//
// Shared by the Module Structure panel (a button in the target's row) and the
// Grade Rollup panel (every rollup in the course). Both read and drive the
// same state, so a check run in one shows up in the other.
//
// Loading only reads frontmatter (rollup.py with no flags). Canvas is asked
// who qualifies when a button is pressed, and grades are written only after a
// modal confirm that names the count. The write is limited to the students
// that confirm showed (--students), so a student who qualified in between is
// never marked unseen.

const log = vscode.window.createOutputChannel('CQS Grade Rollup');

// Shapes printed by `rollup.py --json`. See handlers/rollup.py.
export interface RollupRequirement {
  path: string;
  declared_as: string;
  exists: boolean;
  target_id: number | null;
  title: string;
}

export interface RollupStudent {
  id: number;
  name: string;
  sortable_name: string;
}

export interface RollupStatus {
  students: number;
  complete: number;
  already: number;
  to_mark: RollupStudent[];
  conflicts: RollupStudent[];
  missing: Record<string, number>;
}

export interface Rollup {
  name: string;
  target: string;
  target_id: number | null;
  requires: RollupRequirement[];
  pass_at: number;
  grading_type: string | null;
  points: number | null;
  problems: string[];
  status?: RollupStatus | null;
}

export interface RollupState {
  decl: Rollup;
  status?: RollupStatus;
  checkedAt?: Date;
  busy?: 'status' | 'setup' | 'apply';
  error?: string;
  // What the line under a Module Structure row shows, if anything.
  view?: 'status' | 'setup';
}

// Keyed by the target's path, as rollup.py reports it. Kept across refreshes
// so a status someone waited for does not vanish when a panel reloads.
export const rollups = new Map<string, RollupState>();

export interface RollupUpdate {
  type: 'rollupUpdate';
  target: string;
  ctl: string;
  row: string;
  card: string;
}

const listeners = new Set<(m: RollupUpdate) => void>();

/** A panel subscribes to hear about every change to a rollup's state. */
export function subscribeRollups(fn: (m: RollupUpdate) => void): vscode.Disposable {
  listeners.add(fn);
  return new vscode.Disposable(() => listeners.delete(fn));
}

export function rollupKey(p: string): string {
  return p.replace(/\\/g, '/').normalize('NFC');
}

export function esc(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

async function runRollup(
  extensionPath: string,
  args: string[],
  showBusy: boolean
): Promise<{ code: number | null; doc: any; stderr: string } | undefined> {
  const ws = getWorkspaceRoot();
  const pythonPath = resolvePython();
  if (!ws || !pythonPath) return undefined;
  const scriptPath = path.join(resolveCqsRoot(extensionPath), 'rollup.py');
  // An installed tool older than rollups: no button, rather than an error.
  if (!fs.existsSync(scriptPath)) return undefined;

  if (showBusy) setSyncing(true);
  return new Promise((resolve) => {
    let stdout = '';
    let stderr = '';
    const proc = spawn(pythonPath, [scriptPath, ws, ...args, '--json'],
      { cwd: ws, env: { ...process.env, PYTHONIOENCODING: 'utf-8' } });
    proc.stdout?.on('data', (d: Buffer) => { stdout += d.toString(); });
    proc.stderr?.on('data', (d: Buffer) => { stderr += d.toString(); });
    proc.on('close', (code) => {
      if (showBusy) setSyncing(false);
      log.appendLine('[rollup] ' + args.join(' ') + ' exit=' + code);
      if (stderr.trim()) log.appendLine(stderr.trim());
      // --json logs to stderr, so stdout is the document or nothing. A
      // non-zero exit can still carry one: offline mode exits 1 on problems.
      let doc: any;
      try { doc = stdout.trim() ? JSON.parse(stdout.trim()) : undefined; } catch { doc = undefined; }
      resolve({ code, doc, stderr: stderr.trim() });
    });
    proc.on('error', (err) => {
      if (showBusy) setSyncing(false);
      resolve({ code: null, doc: undefined, stderr: err.message });
    });
  });
}

function failureText(res: { code: number | null; stderr: string } | undefined): string {
  if (!res) return 'rollup.py was not found. Update CanvasQuartoSync.';
  return res.stderr || ('rollup.py exited with code ' + res.code + ' and said nothing.');
}

/**
 * Re-read every declaration from frontmatter. Returns false when the tool
 * could not be asked at all (no workspace, no venv, a tool without rollup.py).
 */
export async function loadRollups(extensionPath: string): Promise<boolean> {
  const res = await runRollup(extensionPath, [], false);
  if (!res?.doc?.rollups) {
    rollups.clear();
    return false;
  }
  const seen = new Set<string>();
  for (const decl of res.doc.rollups as Rollup[]) {
    const key = rollupKey(decl.target);
    seen.add(key);
    const prev = rollups.get(key);
    rollups.set(key, { ...prev, decl, busy: undefined });
  }
  for (const key of [...rollups.keys()]) {
    if (!seen.has(key)) rollups.delete(key);
  }
  return true;
}

function postRollup(key: string): void {
  const st = rollups.get(key);
  if (!st) return;
  const msg: RollupUpdate = {
    type: 'rollupUpdate',
    target: key,
    ctl: renderRollupCtl(st),
    row: renderRollupSummary(st, 'row'),
    card: renderRollupSummary(st, 'card'),
  };
  for (const fn of listeners) fn(msg);
}

/** Ask Canvas who qualifies. Leaves the result, or the error, on the state. */
async function checkRollupStatus(extensionPath: string, key: string): Promise<boolean> {
  const st = rollups.get(key);
  if (!st) return false;
  st.busy = 'status';
  st.view = 'status';
  postRollup(key);

  const res = await runRollup(extensionPath, ['--status', '--only', st.decl.target], true);
  st.busy = undefined;
  const r: Rollup | undefined = res?.doc?.rollups?.[0];
  if (!r) {
    st.error = failureText(res);
    postRollup(key);
    return false;
  }
  st.error = undefined;
  st.decl = { ...r, status: undefined };
  if (r.status) {
    st.status = r.status;
    st.checkedAt = new Date();
  } else {
    // Problems found on the way, e.g. a requirement never synced.
    st.status = undefined;
    st.view = 'setup';
  }
  postRollup(key);
  return !!r.status;
}

export async function handleRollup(extensionPath: string, action: string, target: string): Promise<void> {
  const key = rollupKey(target);
  const st = rollups.get(key);
  if (!st || st.busy) return;

  if (action === 'close') {
    st.view = undefined;
    st.error = undefined;
    postRollup(key);
    return;
  }

  if (action === 'setup') {
    st.busy = 'setup';
    st.view = 'setup';
    postRollup(key);
    const res = await runRollup(extensionPath, ['--only', st.decl.target], false);
    st.busy = undefined;
    const r: Rollup | undefined = res?.doc?.rollups?.[0];
    if (r) {
      st.decl = r;
      st.error = undefined;
    } else {
      st.error = failureText(res);
    }
    postRollup(key);
    return;
  }

  // Both remaining actions need Canvas, which is pointless with a broken rule.
  if (st.decl.problems.length) {
    st.view = 'setup';
    postRollup(key);
    return;
  }

  if (action === 'status') {
    await checkRollupStatus(extensionPath, key);
    return;
  }

  if (action !== 'apply') return;

  // The count in the confirm has to be current, not whatever was on screen.
  const stale = !st.checkedAt || Date.now() - st.checkedAt.getTime() > 60000;
  if (stale && !(await checkRollupStatus(extensionPath, key))) return;
  const status = st.status!;
  const name = st.decl.name;

  if (status.to_mark.length === 0) {
    vscode.window.showInformationMessage(
      `${name}: nobody to mark. ${status.complete} of ${status.students} qualify, `
      + `${status.already} already marked.`);
    return;
  }

  const n = status.to_mark.length;
  const shown = status.to_mark.slice(0, 12).map(s => '  ' + s.sortable_name);
  if (n > shown.length) shown.push(`  …and ${n - shown.length} more`);
  const what = st.decl.grading_type === 'pass_fail' ? 'complete' : 'with full marks';
  const button = `Mark ${n} ${what}`;
  const choice = await vscode.window.showWarningMessage(
    `Mark ${n} student${n === 1 ? '' : 's'} ${what} in "${name}"?`,
    {
      modal: true,
      detail: 'This writes grades in Canvas.\n\n' + shown.join('\n')
        + '\n\nNo existing grade is lowered or removed.',
    },
    button
  );
  if (choice !== button) return;

  st.busy = 'apply';
  postRollup(key);
  const ids = status.to_mark.map(s => s.id).join(',');
  const res = await runRollup(extensionPath,
    ['--apply', '--only', st.decl.target, '--students', ids], true);
  st.busy = undefined;

  const applied = res?.doc?.applied?.[0];
  if (!res?.doc?.applied) {
    vscode.window.showErrorMessage(`${name}: marking failed. ${failureText(res)}`);
  } else if (!applied) {
    vscode.window.showInformationMessage(`${name}: nobody left to mark.`);
  } else {
    const parts = [`${applied.marked.length} marked`];
    if (applied.failed.length) parts.push(`${applied.failed.length} failed (see the CQS Grade Rollup output)`);
    if (applied.skipped?.length) parts.push(`${applied.skipped.length} skipped, no longer to mark`);
    const text = `${name}: ${parts.join(', ')}.`;
    if (applied.failed.length || applied.skipped?.length) {
      vscode.window.showWarningMessage(text);
    } else {
      vscode.window.showInformationMessage(text);
    }
    for (const f of applied.failed) log.appendLine(`[rollup] FAILED ${f.sortable_name}: ${f.error}`);
  }
  // Show where things stand now, from Canvas, not from what we hoped to write.
  await checkRollupStatus(extensionPath, key);
}

// ── Rendering ───────────────────────────────────────────────────────

function rollupTooltip(st: RollupState): string {
  const d = st.decl;
  const lines: string[] = [];
  if (d.problems.length) {
    lines.push('Problems:', ...d.problems.map(p => '  ⚠ ' + p), '');
  }
  lines.push('Grade rollup: marks this assignment for every student who has passed all of:');
  for (const r of d.requires) lines.push('  • ' + (r.title || r.declared_as));
  lines.push(`A requirement counts as passed at score ≥ ${d.pass_at}, when complete, or when excused.`);
  lines.push('');
  lines.push('Click: who qualifies? Reads Canvas, changes nothing.');
  lines.push('▾  Check setup: offline, are the paths and Canvas ids in place.');
  lines.push('▾  Mark complete…: writes grades, after asking with the count.');
  lines.push('');
  lines.push('Grades are only ever raised, never withdrawn.');
  return lines.join('\n');
}

/** Inner HTML of the split button. Goes in a `.rollup-ctl[data-rollup]`. */
export function renderRollupCtl(st: RollupState): string {
  const broken = st.decl.problems.length > 0;
  const cls = st.busy ? 'busy' : (broken ? 'warn' : 'ok');
  const label = st.busy ? 'Rollup …' : (broken ? 'Rollup ⚠' : 'Rollup ✓');
  const dis = st.busy ? ' disabled' : '';
  let h = '<span class="rollup-split">';
  h += '<button class="ru-btn rollup-main ' + cls + '"' + dis
    + ' onclick="event.stopPropagation();rollupAct(this,\'status\')" title="' + esc(rollupTooltip(st)) + '">'
    + label + '</button>';
  h += '<button class="ru-btn rollup-caret ' + cls + '"' + dis
    + ' onclick="event.stopPropagation();toggleRollupMenu(this)" title="More rollup actions">&#x25BE;</button>';
  h += '<div class="ru-menu hidden">';
  h += '<div class="ru-menu-item" onclick="rollupAct(this,\'status\')">Who qualifies?'
    + '<span class="hint">Reads Canvas, changes nothing</span></div>';
  h += '<div class="ru-menu-item" onclick="rollupAct(this,\'setup\')">Check setup'
    + '<span class="hint">Offline: paths and Canvas ids</span></div>';
  h += '<div class="ru-menu-sep"></div>';
  h += '<div class="ru-menu-item danger" onclick="rollupAct(this,\'apply\')">Mark complete…'
    + '<span class="hint">Writes grades, asks first with the count</span></div>';
  h += '</div></span>';
  return h;
}

function fileLink(rel: string): string {
  return '<span class="ru-link" onclick="openFile(\'' + esc(rel).replace(/'/g, "\\'") + '\')">' + esc(rel) + '</span>';
}

function renderProblems(d: Rollup): string {
  return '<div class="ru-line ru-warn">' + d.problems.map(p => '&#x26A0; ' + esc(p)).join('<br>') + '</div>'
    + '<div class="ru-line ru-dim">Fix the <code>rollup:</code> block in ' + fileLink(d.target)
    + ', or sync what is missing, then use Check setup again.</div>';
}

/** The configuration inspector: every requirement, and what breaks it. */
function renderRequirementTable(d: Rollup): string {
  let h = '<table class="ru-reqs"><tbody>';
  h += '<tr><td>' + (d.target_id ? '&#x2713;' : '&#x26A0;') + '</td><td>Target</td><td>' + fileLink(d.target)
    + '</td><td class="ru-dim">' + (d.target_id ? 'id ' + d.target_id : 'never synced') + '</td></tr>';
  for (const r of d.requires) {
    const ok = r.exists && r.target_id != null;
    const note = !r.exists ? 'file not found' : (r.target_id == null ? 'never synced' : 'id ' + r.target_id);
    h += '<tr><td>' + (ok ? '&#x2713;' : '&#x26A0;') + '</td><td>' + esc(r.title || r.declared_as) + '</td><td>'
      + (r.exists ? fileLink(r.path) : esc(r.declared_as)) + '</td><td class="' + (ok ? 'ru-dim' : 'ru-warn') + '">'
      + note + '</td></tr>';
  }
  h += '</tbody></table>';
  h += '<div class="ru-line ru-dim">A requirement counts as passed at score &ge; ' + d.pass_at
    + ', when graded complete, or when excused.</div>';
  return h;
}

function renderStatusLines(st: RollupState, close: string): string {
  const s = st.status!;
  const d = st.decl;
  const names = (list: RollupStudent[]) => esc(list.map(x => x.sortable_name).join('\n'));
  let h = '<div class="ru-line">';
  h += '<b>' + s.students + '</b> students &middot; ';
  h += '<b>' + s.complete + '</b> qualify &middot; ';
  h += '<b>' + s.already + '</b> already marked &middot; ';
  h += '<span title="' + names(s.to_mark) + '"><b>' + s.to_mark.length + '</b> to mark</span>';
  if (s.conflicts.length) {
    h += ' &middot; <span class="ru-warn" title="' + esc('Hold a pass on this assignment without passing every requirement. '
      + 'Left untouched: a rollup never lowers a grade.\n\n') + names(s.conflicts) + '">'
      + s.conflicts.length + ' conflict' + (s.conflicts.length === 1 ? '' : 's') + '</span>';
  }
  if (s.to_mark.length) {
    h += '<button class="ru-btn ru-mark" onclick="rollupAct(this,\'apply\')" title="Asks first, with the list of names">'
      + 'Mark ' + s.to_mark.length + ' complete</button>';
  }
  if (st.checkedAt) {
    const t = st.checkedAt.toLocaleTimeString('sv-SE', { hour: '2-digit', minute: '2-digit' });
    h += ' <span class="ru-dim">checked ' + t + '</span>';
  }
  h += ' ' + close + '</div>';

  const titles = new Map(d.requires.map(r => [rollupKey(r.path), r.title || r.declared_as]));
  const waiting = Object.entries(s.missing)
    .filter(([, n]) => n > 0)
    .sort((a, b) => b[1] - a[1])
    .map(([p, n]) => esc(titles.get(rollupKey(p)) || p) + ' <b>' + n + '</b>');
  if (waiting.length) {
    h += '<div class="ru-line ru-dim">Still waiting on: ' + waiting.join(' &middot; ') + '</div>';
  }
  return h;
}

/**
 * What goes under the button. `row` is the line under a Module Structure row:
 * empty until asked for, and closable. `card` is the Grade Rollup panel's
 * body: the requirement table is always there, and Canvas results are added
 * below it once checked.
 */
export function renderRollupSummary(st: RollupState, ctx: 'row' | 'card'): string {
  const d = st.decl;
  const close = ctx === 'row'
    ? '<button class="ru-close" onclick="rollupAct(this,\'close\')" title="Hide">&times;</button>'
    : '';
  let busy = '';
  if (st.busy === 'status') busy = '<div class="ru-line ru-dim">Asking Canvas who qualifies…</div>';
  if (st.busy === 'apply') busy = '<div class="ru-line ru-dim">Writing grades…</div>';
  if (st.busy === 'setup') busy = '<div class="ru-line ru-dim">Checking setup…</div>';
  const error = st.error
    ? '<div class="ru-line ru-err">Could not check: ' + esc(st.error.slice(0, 600)) + ' ' + close + '</div>'
    : '';

  if (ctx === 'card') {
    let h = d.problems.length ? renderProblems(d) : '';
    h += renderRequirementTable(d);
    if (busy) return h + busy;
    if (error) return h + error;
    if (st.status && st.view === 'status') h += '<div class="ru-result">' + renderStatusLines(st, '') + '</div>';
    return h;
  }

  if (busy) return busy;
  if (error) return error;
  if (st.view === 'setup' || (st.view === 'status' && d.problems.length)) {
    if (d.problems.length) return renderProblems(d).replace('</div>', ' ' + close + '</div>');
    let h = '<div class="ru-line">Setup OK: target synced (id ' + d.target_id + '), '
      + d.requires.length + ' requirements, passed at score &ge; ' + d.pass_at + ' ' + close + '</div>';
    h += '<div class="ru-line ru-dim">'
      + d.requires.map(r => '<span title="' + esc(r.path + ' · id ' + r.target_id) + '">&#x2713; '
        + esc(r.title || r.declared_as) + '</span>').join(' &middot; ')
      + '</div>';
    return h;
  }
  if (st.view === 'status' && st.status) return renderStatusLines(st, close);
  return '';
}

// ── Webview pieces ──────────────────────────────────────────────────
//
// Both panels include these. Each panel's own script must define
// `vscode` (acquireVsCodeApi) and `openFile(path)`.

export const ROLLUP_CSS = `
.rollup-ctl{flex:none}
.rollup-split{display:inline-flex;position:relative}
.ru-btn{background:none;border:1px solid var(--vscode-button-secondaryBackground,#333);color:var(--vscode-foreground);border-radius:3px;cursor:pointer;white-space:nowrap;display:inline-flex;align-items:center;gap:3px;font-size:10px;padding:1px 6px}
.ru-btn:hover{background:var(--vscode-button-secondaryHoverBackground,#444)}
.rollup-main{border-radius:3px 0 0 3px}
.rollup-caret{border-radius:0 3px 3px 0;border-left:none;padding:1px 4px}
.ru-btn.ok{border-color:#198754;color:#75b798}
.ru-btn.ok:hover{background:rgba(25,135,84,0.15)}
.ru-btn.warn{border-color:#ffc107;color:#ffc107}
.ru-btn.warn:hover{background:rgba(255,193,7,0.15)}
.ru-btn:disabled{opacity:0.6;cursor:progress}
.ru-menu{position:fixed;z-index:1000;background:var(--vscode-menu-background,var(--vscode-sideBar-background));border:1px solid var(--vscode-widget-border);border-radius:4px;padding:4px 0;min-width:230px;box-shadow:0 4px 12px rgba(0,0,0,0.3);font-weight:400}
.ru-menu.hidden{display:none}
.ru-menu-item{padding:6px 14px;font-size:12px;cursor:pointer;white-space:nowrap}
.ru-menu-item:hover{background:var(--vscode-list-hoverBackground)}
.ru-menu-item .hint{display:block;font-size:10px;color:var(--vscode-descriptionForeground)}
.ru-menu-item.danger{color:#ea868f}
.ru-menu-sep{height:1px;background:var(--vscode-widget-border);margin:4px 0}
.rollup-summary:empty{display:none}
.ru-line{display:flex;flex-wrap:wrap;align-items:center;gap:4px;padding:1px 0}
.ru-dim{color:var(--vscode-descriptionForeground)}
.ru-warn{color:#ffc107}
.ru-err{color:var(--vscode-errorForeground);white-space:pre-wrap}
.ru-link{cursor:pointer;text-decoration:underline;text-decoration-style:dotted}
.ru-link:hover{color:var(--vscode-textLink-foreground)}
.ru-mark{margin-left:8px;border-color:#198754;color:#75b798;font-size:11px;padding:2px 8px}
.ru-mark:hover{background:rgba(25,135,84,0.15)}
.ru-close{background:none;border:none;color:var(--vscode-descriptionForeground);cursor:pointer;font-size:14px;line-height:1;margin-left:auto;padding:0 4px}
.ru-close:hover{color:var(--vscode-foreground)}
.ru-reqs{border-collapse:collapse;margin:4px 0;font-size:12px}
.ru-reqs td{padding:2px 14px 2px 0;white-space:nowrap}
.ru-result{margin-top:8px;padding-top:8px;border-top:1px solid var(--vscode-widget-border)}
`;

export const ROLLUP_SCRIPT = `
function rollupAct(el,action){
  document.querySelectorAll(".ru-menu").forEach(function(m){m.classList.add("hidden");});
  var host=el.closest("[data-rollup]");
  if(!host)return;
  vscode.postMessage({type:"rollup",action:action,target:host.dataset.rollup});
}
function toggleRollupMenu(btn){
  var menu=btn.parentElement.querySelector(".ru-menu");
  var wasHidden=menu.classList.contains("hidden");
  document.querySelectorAll(".ru-menu").forEach(function(m){m.classList.add("hidden");});
  if(!wasHidden)return;
  var r=btn.getBoundingClientRect();
  menu.classList.remove("hidden");
  var mh=menu.offsetHeight||140,mw=menu.offsetWidth||230;
  var top=(r.bottom+mh+4<=window.innerHeight)?(r.bottom+2):Math.max(4,r.top-mh-2);
  menu.style.top=top+"px";
  menu.style.left=Math.max(4,r.right-mw)+"px";
}
document.addEventListener("click",function(e){
  if(!e.target.closest(".rollup-split")){
    document.querySelectorAll(".ru-menu").forEach(function(m){m.classList.add("hidden");});
  }
});
window.addEventListener("message",function(e){
  var m=e.data;
  if(!m||m.type!=="rollupUpdate")return;
  document.querySelectorAll(".rollup-ctl[data-rollup]").forEach(function(el){if(el.dataset.rollup===m.target)el.innerHTML=m.ctl;});
  document.querySelectorAll(".rollup-summary[data-rollup]").forEach(function(el){
    if(el.dataset.rollup===m.target)el.innerHTML=(el.dataset.ctx==="card"?m.card:m.row);
  });
});
`;
