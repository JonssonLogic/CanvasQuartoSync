import * as vscode from 'vscode';
import * as fs from 'fs';
import * as path from 'path';
import ICAL from 'ical.js';
import { parse as parseYaml, parseDocument, isMap, isSeq, isScalar, Scalar, YAMLMap } from 'yaml';
import { resolveCourseRoot } from '../config/configLoader';

/**
 * The Calendar panel: the course schedule (schedule.yaml) next to the
 * teacher's Outlook calendars, read from published ICS links.
 *
 * - Outlook calendars are read-only and can be hidden one by one. Their ICS
 *   address is a secret, so it is kept in VS Code's secret storage and never
 *   written into the course folder.
 * - The week is Monday to Friday, numbered the Swedish (ISO 8601) way, and
 *   Swedish public holidays are shown in red.
 * - "Ny återkommande bokning" previews a weekly series before it adds it to
 *   schedule.yaml. Nothing here talks to Canvas.
 */

interface Source {
  id: string;
  name: string;
  color: string;
}

interface CalEvent {
  id: string;
  src: string;
  title: string;
  start: string;      // 'YYYY-MM-DDTHH:mm', or 'YYYY-MM-DD' when allDay
  end: string;        // same shape; for all-day events the last day, inclusive
  allDay: boolean;
  location: string;
  free: boolean;      // shown as "free" in Outlook
  // Course schedule only: where the entry sits in schedule.yaml, so it can be edited.
  idx?: number;
  time?: string;      // the entry's time as written, '09:00-10:00'
  description?: string;
  series?: boolean;   // an old days/start_date entry rather than one date
}

interface Parsed {
  events: ICAL.Event[];
  at: number;
}

const SCHEDULE = 'schedule';
const COLORS = ['#4a90e2', '#a371f7', '#f0883e', '#39c5cf', '#db61a2', '#c9a227'];
const SCHEDULE_COLOR = '#3fb950';
const CACHE_MS = 10 * 60 * 1000;
const DAY_MAP: Record<string, number> = { Sun: 0, Mon: 1, Tue: 2, Wed: 3, Thu: 4, Fri: 5, Sat: 6 };

let panel: vscode.WebviewPanel | undefined;
let ctx: vscode.ExtensionContext;
let courseRoot: string | undefined;
const cache = new Map<string, Parsed>();
const errors = new Map<string, string>();

const log = vscode.window.createOutputChannel('CQS Calendar');

export async function openCalendar(context: vscode.ExtensionContext): Promise<void> {
  ctx = context;
  const root = await resolveCourseRoot();
  if (!root) return;
  courseRoot = root;

  if (panel) {
    panel.title = `Calendar: ${path.basename(root)}`;
    panel.reveal();
    await sendInit();
    return;
  }

  panel = vscode.window.createWebviewPanel(
    'cqs.calendar',
    `Calendar: ${path.basename(root)}`,
    vscode.ViewColumn.One,
    { enableScripts: true, retainContextWhenHidden: true }
  );
  panel.onDidDispose(() => { panel = undefined; });
  panel.webview.onDidReceiveMessage((msg) => { handle(msg).catch((e) => {
    log.appendLine(`[error] ${e instanceof Error ? e.stack : String(e)}`);
    vscode.window.showErrorMessage(`Calendar: ${e instanceof Error ? e.message : String(e)}`);
  }); });
  panel.webview.html = wrapHtml();
}

// ── Sources ─────────────────────────────────────────────────────────

function outlookSources(): Source[] {
  const list = ctx.globalState.get<{ id: string; name: string }[]>('cqs.calendar.sources', []);
  return list.map((s, i) => ({ id: s.id, name: s.name, color: COLORS[i % COLORS.length] }));
}

function hiddenIds(): string[] {
  return ctx.globalState.get<string[]>('cqs.calendar.hidden', []);
}

async function sendInit(): Promise<void> {
  if (!panel) return;
  const sources = [
    { id: SCHEDULE, name: 'Kursschema', color: SCHEDULE_COLOR },
    ...outlookSources(),
  ].map((s) => ({ ...s, visible: !hiddenIds().includes(s.id), error: errors.get(s.id) || '' }));
  panel.webview.postMessage({ type: 'init', sources });
}

async function addSource(): Promise<void> {
  const name = await vscode.window.showInputBox({
    title: 'Outlook-kalender (1/2)', prompt: 'Namn på kalendern', value: 'Outlook', ignoreFocusOut: true,
  });
  if (!name) return;
  const url = await vscode.window.showInputBox({
    title: 'Outlook-kalender (2/2)',
    prompt: 'Klistra in ICS-länken (Outlook på webben: Inställningar > Kalender > Delade kalendrar > Publicera en kalender)',
    password: true, ignoreFocusOut: true,
    validateInput: (v) => /^(https?|webcal):\/\//i.test(v.trim()) ? undefined : 'Länken ska börja med https://',
  });
  if (!url) return;
  const id = 'ol' + Date.now().toString(36);
  await ctx.secrets.store(`cqs.calendar.url.${id}`, url.trim().replace(/^webcal:/i, 'https:'));
  const list = ctx.globalState.get<{ id: string; name: string }[]>('cqs.calendar.sources', []);
  await ctx.globalState.update('cqs.calendar.sources', [...list, { id, name: name.trim() }]);
  await sendInit();
}

async function removeSource(id: string): Promise<void> {
  const list = ctx.globalState.get<{ id: string; name: string }[]>('cqs.calendar.sources', []);
  await ctx.globalState.update('cqs.calendar.sources', list.filter((s) => s.id !== id));
  await ctx.secrets.delete(`cqs.calendar.url.${id}`);
  cache.delete(id);
  errors.delete(id);
  await sendInit();
}

async function loadParsed(id: string, force: boolean): Promise<Parsed | undefined> {
  const hit = cache.get(id);
  if (hit && !force && Date.now() - hit.at < CACHE_MS) return hit;
  const url = await ctx.secrets.get(`cqs.calendar.url.${id}`);
  if (!url) { errors.set(id, 'Länken saknas'); return hit; }
  try {
    const res = await fetch(url, { headers: { 'User-Agent': 'CanvasQuartoSync' } });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const text = await res.text();
    const parsed = parseIcs(text);
    cache.set(id, parsed);
    errors.delete(id);
    return parsed;
  } catch (e) {
    errors.set(id, e instanceof Error ? e.message : String(e));
    log.appendLine(`[fetch] ${id}: ${errors.get(id)}`);
    return hit;
  }
}

export function parseIcs(text: string): Parsed {
  const root = new ICAL.Component(ICAL.parse(text));
  for (const tz of root.getAllSubcomponents('vtimezone')) {
    try { ICAL.TimezoneService.register(tz); } catch { /* already registered */ }
  }
  const masters = new Map<string, ICAL.Event>();
  const exceptions: ICAL.Event[] = [];
  const out: ICAL.Event[] = [];
  for (const v of root.getAllSubcomponents('vevent')) {
    const ev = new ICAL.Event(v);
    if (String(v.getFirstPropertyValue('status') || '').toUpperCase() === 'CANCELLED') continue;
    if (ev.recurrenceId) exceptions.push(ev);
    else { masters.set(ev.uid, ev); out.push(ev); }
  }
  for (const ex of exceptions) {
    const m = masters.get(ex.uid);
    if (m) m.relateException(ex);
    else out.push(ex);        // the series is not in the file: show it alone
  }
  return { events: out, at: Date.now() };
}

// ── Expanding events into a date range ─────────────────────────────

const p2 = (n: number) => String(n).padStart(2, '0');
const ymd = (d: Date) => `${d.getFullYear()}-${p2(d.getMonth() + 1)}-${p2(d.getDate())}`;
const ymdhm = (d: Date) => `${ymd(d)}T${p2(d.getHours())}:${p2(d.getMinutes())}`;

export function expandIcs(src: string, parsed: Parsed, from: Date, to: Date): CalEvent[] {
  const out: CalEvent[] = [];
  const push = (uid: string, s: ICAL.Time, e: ICAL.Time, ev: ICAL.Event, n: number) => {
    const allDay = s.isDate;
    const sd = s.toJSDate();
    let ed = e.toJSDate();
    if (allDay) ed = new Date(ed.getTime() - 24 * 3600 * 1000);   // DTEND is exclusive
    if (ed < from || sd > to) return;
    const busy = String(ev.component.getFirstPropertyValue('x-microsoft-cdo-busystatus') || '').toUpperCase();
    out.push({
      id: `${src}:${uid}:${n}`, src,
      title: ev.summary || '(utan titel)',
      start: allDay ? ymd(sd) : ymdhm(sd),
      end: allDay ? ymd(ed) : ymdhm(ed),
      allDay, location: ev.location || '', free: busy === 'FREE',
    });
  };
  for (const ev of parsed.events) {
    try {
      if (ev.isRecurring()) {
        const it = ev.iterator();
        const limit = ICAL.Time.fromJSDate(new Date(to.getTime() + 24 * 3600 * 1000), false);
        let n = 0;
        for (let next = it.next(); next && n < 3000; next = it.next(), n++) {
          if (next.compare(limit) > 0) break;
          const d = ev.getOccurrenceDetails(next);
          push(ev.uid, d.startDate, d.endDate, d.item, n);
        }
      } else {
        push(ev.uid, ev.startDate, ev.endDate, ev, 0);
      }
    } catch (e) {
      log.appendLine(`[expand] ${src} ${ev.summary}: ${e instanceof Error ? e.message : e}`);
    }
  }
  return out;
}

// ── schedule.yaml ───────────────────────────────────────────────────

function scheduleFile(): string | undefined {
  return courseRoot ? path.join(courseRoot, 'schedule.yaml') : undefined;
}

function dayOf(v: unknown): Date | undefined {
  if (v instanceof Date) return new Date(v.getFullYear(), v.getMonth(), v.getDate());
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(v ?? '').trim());
  return m ? new Date(+m[1], +m[2] - 1, +m[3]) : undefined;
}

function scheduleEvents(from: Date, to: Date): CalEvent[] {
  const file = scheduleFile();
  if (!file || !fs.existsSync(file)) return [];
  let data: { events?: Record<string, unknown>[] } | null;
  try { data = parseYaml(fs.readFileSync(file, 'utf-8')); } catch (e) {
    errors.set(SCHEDULE, `schedule.yaml: ${e instanceof Error ? e.message : e}`);
    return [];
  }
  errors.delete(SCHEDULE);
  const out: CalEvent[] = [];
  (data?.events || []).forEach((ev, i) => {
    const title = String(ev.title || `event ${i + 1}`);
    const m = /^\s*(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})\s*$/.exec(String(ev.time || '12:00-13:00'));
    const [t1, t2] = m ? [m[1].padStart(5, '0'), m[2].padStart(5, '0')] : ['12:00', '13:00'];
    const add = (day: Date, n: number) => {
      if (day < from || day > to) return;
      out.push({
        id: `${SCHEDULE}:${i}:${n}`, src: SCHEDULE, title,
        start: `${ymd(day)}T${t1}`, end: `${ymd(day)}T${t2}`, allDay: false,
        location: String(ev.location || ''), free: false,
        idx: i, time: `${t1}-${t2}`, description: String(ev.description || ''),
        series: Array.isArray(ev.days),
      });
    };
    if (Array.isArray(ev.days)) {
      const s = dayOf(ev.start_date), e = dayOf(ev.end_date);
      if (!s || !e) return;
      const wd = new Set((ev.days as string[]).map((d) => DAY_MAP[d]));
      for (let d = new Date(s), n = 0; d <= e; d.setDate(d.getDate() + 1), n++) {
        if (wd.has(d.getDay())) add(new Date(d), n);
      }
    } else {
      const d = dayOf(ev.date);
      if (d) add(d, 0);
    }
  });
  return out;
}

// ── Editing an entry in schedule.yaml ──────────────────────────────

export interface EditOp {
  kind: 'update' | 'delete';
  idx: number;                                       // position in events
  orig: { title: string; date: string; time: string };   // what the page showed
  fields?: { title: string; date: string; time: string; location?: string; description?: string };
}

const YAML_OUT = { lineWidth: 0, flowCollectionPadding: false };

function quoted(v: string): Scalar {
  const s = new Scalar(v);
  s.type = Scalar.QUOTE_DOUBLE;
  return s;
}

const normTime = (t: unknown) => {
  const m = /^\s*(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})\s*$/.exec(String(t ?? '12:00-13:00'));
  return m ? `${m[1].padStart(5, '0')}-${m[2].padStart(5, '0')}` : String(t);
};

/**
 * schedule.yaml with one entry changed or removed, as the page asked. The
 * YAML is edited as a document, so comments and layout elsewhere survive.
 * An old series entry (days/start_date) is first split into one entry per
 * occasion, so that only the occasion the teacher touched changes.
 * Throws when the entry is no longer the one the page showed.
 */
export function editScheduleText(text: string, op: EditOp): { text: string; split: number } {
  const doc = parseDocument(text.replace(/\r\n/g, '\n'));
  const seq = doc.get('events', true);
  if (!isSeq(seq)) throw new Error('schedule.yaml har ingen events-lista.');
  const stale = () => new Error('schedule.yaml har ändrats sedan kalendern lästes. Kalendern läses om, försök igen.');
  let idx = op.idx;
  const item = seq.items[idx];
  if (!isMap(item)) throw stale();
  if (String(item.get('title') ?? '') !== op.orig.title || normTime(item.get('time')) !== op.orig.time) throw stale();

  let split = 0;
  if (item.has('days')) {
    const s = dayOf(item.get('start_date')), e = dayOf(item.get('end_date'));
    const daysNode = item.get('days');
    const days = (isSeq(daysNode) ? daysNode.items.map((d) => String(isScalar(d) ? d.value : d)) : []);
    const wd = new Set(days.map((d) => DAY_MAP[d]));
    if (!s || !e) throw stale();
    const maps: YAMLMap[] = [];
    let at = -1;
    for (let d = new Date(s); d <= e; d.setDate(d.getDate() + 1)) {
      if (!wd.has(d.getDay())) continue;
      const m = new YAMLMap();
      m.set('title', quoted(String(item.get('title') ?? '')));
      m.set('date', quoted(ymd(d)));
      m.set('time', quoted(normTime(item.get('time'))));
      for (const k of ['location', 'description']) {
        if (item.has(k)) m.set(k, quoted(String(item.get(k) ?? '')));
      }
      if (ymd(d) === op.orig.date) at = maps.length;
      maps.push(m);
    }
    if (at < 0) throw stale();
    maps[0].commentBefore = item.commentBefore;
    maps[0].spaceBefore = item.spaceBefore;
    seq.items.splice(idx, 1, ...maps);
    idx += at;
    split = maps.length;
  } else if (String(item.get('date') ?? '').slice(0, 10) !== op.orig.date) {
    throw stale();
  }

  const target = seq.items[idx] as YAMLMap;
  if (op.kind === 'delete') {
    // A comment above the entry (the note over a recurring booking) moves to
    // the next entry rather than going with it.
    const next = seq.items[idx + 1];
    if (target.commentBefore && isMap(next) && !next.commentBefore) {
      next.commentBefore = target.commentBefore;
      next.spaceBefore = target.spaceBefore;
    }
    seq.items.splice(idx, 1);
  } else {
    const f = op.fields!;
    target.set('title', quoted(f.title));
    target.set('date', quoted(f.date));
    target.set('time', quoted(f.time));
    for (const k of ['location', 'description'] as const) {
      const v = (f[k] || '').trim();
      if (v) target.set(k, quoted(v)); else target.delete(k);
    }
  }
  return { text: doc.toString(YAML_OUT), split };
}

async function editSchedule(op: EditOp): Promise<boolean> {
  const file = scheduleFile();
  if (!file || !fs.existsSync(file)) return false;
  if (op.kind === 'delete') {
    const pick = await vscode.window.showWarningMessage(
      `Ta bort "${op.orig.title}" ${op.orig.date} ${op.orig.time} ur schedule.yaml?`, { modal: true }, 'Ta bort');
    if (pick !== 'Ta bort') return false;
  }
  const res = editScheduleText(fs.readFileSync(file, 'utf-8'), op);
  fs.writeFileSync(file, res.text, 'utf-8');
  if (res.split) {
    vscode.window.showInformationMessage(
      `Serien "${op.orig.title}" delades upp i ${res.split} enskilda bokningar i schedule.yaml.`);
  }
  return true;
}

/** One schedule.yaml entry: a single booking (date) or a weekly series (days). */
export interface Segment {
  title: string; time: string;
  date?: string;
  days?: string[]; start_date?: string; end_date?: string;
  location?: string; description?: string;
}

/**
 * schedule.yaml with the series added at the end of its events list.
 * Edits the text rather than re-dumping the YAML, so comments and the
 * teacher's own layout survive.
 */
export function appendSeriesText(original: string, segments: Segment[], note?: string): string {
  let text = original.replace(/\r\n/g, '\n');
  if (!/^events\s*:/m.test(text)) {
    text = text.replace(/\s*$/, text.trim() ? '\n\n' : '') + 'events:\n';
  } else {
    text = text.replace(/^(events\s*:)[ \t]*\[\s*\][ \t]*$/m, '$1');
  }
  if (!text.endsWith('\n')) text += '\n';
  const lines = text.split('\n');
  lines.pop();                                         // the '' after the last newline
  const start = lines.findIndex((l) => /^events\s*:/.test(l));
  // The events block ends at the next top-level key (not a comment, not a list item).
  let end = lines.length;
  for (let i = start + 1; i < lines.length; i++) {
    if (/^[^\s#-]/.test(lines[i])) { end = i; break; }
  }
  while (end > start + 1 && lines[end - 1].trim() === '') end--;   // keep blank lines after the block
  const indent = lines.slice(start + 1, end).map((l) => /^(\s*)-\s/.exec(l)?.[1]).find((x) => x !== undefined) ?? '  ';
  const q = (s: string) => JSON.stringify(s);
  const block: string[] = [];
  // A recurring booking arrives as one entry per occasion: they sit together
  // under one comment line, without blank lines between them.
  if (note) block.push('', `${indent}# ${note.replace(/\s+/g, ' ')}`);
  for (const s of segments) {
    if (!note) block.push('');
    block.push(`${indent}- title: ${q(s.title)}`);
    if (s.date) {
      block.push(`${indent}  date: ${q(s.date)}`);
    } else {
      block.push(`${indent}  start_date: ${q(s.start_date || '')}`);
      block.push(`${indent}  end_date: ${q(s.end_date || '')}`);
      block.push(`${indent}  days: [${(s.days || []).map(q).join(', ')}]`);
    }
    block.push(`${indent}  time: ${q(s.time)}`);
    if (s.location) block.push(`${indent}  location: ${q(s.location)}`);
    if (s.description) block.push(`${indent}  description: ${q(s.description)}`);
  }
  if (end === start + 1) block.shift();               // no blank line right under "events:"
  lines.splice(end, 0, ...block);
  return lines.join('\n') + '\n';
}

async function appendSeries(segments: Segment[], note?: string): Promise<void> {
  const file = scheduleFile();
  if (!file || !segments.length) return;
  const text = fs.existsSync(file) ? fs.readFileSync(file, 'utf-8') : '';
  fs.writeFileSync(file, appendSeriesText(text, segments, note), 'utf-8');
  const what = segments.length === 1
    ? `"${segments[0].title}" ${segments[0].date} ${segments[0].time}`
    : `${segments.length} bokningar "${segments[0].title}"`;
  vscode.window.showInformationMessage(`Lade till ${what} i schedule.yaml. Inget har skickats till Canvas.`);
}

// ── Messages from the page ──────────────────────────────────────────

async function eventsFor(from: Date, to: Date, force: boolean, onlySchedule = false): Promise<CalEvent[]> {
  let all = scheduleEvents(from, to);
  // The course overview spans every year: Outlook is not expanded for it.
  if (onlySchedule) return all;
  const sources = outlookSources();
  const parsed = await Promise.all(sources.map((s) => loadParsed(s.id, force)));
  sources.forEach((s, i) => { if (parsed[i]) all = all.concat(expandIcs(s.id, parsed[i]!, from, to)); });
  return all;
}

async function handle(msg: Record<string, unknown>): Promise<void> {
  if (!panel) return;
  const post = (m: unknown) => panel?.webview.postMessage(m);
  switch (msg.type) {
    case 'ready':
      await sendInit();
      break;
    case 'range': {
      const from = dayOf(msg.from)!, to = dayOf(msg.to)!;
      to.setHours(23, 59, 59);
      const events = await eventsFor(from, to, !!msg.force, !!msg.onlySchedule);
      await sendInit();                                   // carries any fetch errors
      post({ type: 'events', tag: msg.tag, from: msg.from, to: msg.to, events });
      break;
    }
    case 'toggle': {
      const h = new Set(hiddenIds());
      if (msg.visible) h.delete(String(msg.id)); else h.add(String(msg.id));
      await ctx.globalState.update('cqs.calendar.hidden', [...h]);
      break;
    }
    // A new or removed calendar changes what the current view shows.
    case 'addSource': await addSource(); post({ type: 'reload' }); break;
    case 'removeSource': await removeSource(String(msg.id)); post({ type: 'reload' }); break;
    case 'openSchedule': {
      const f = scheduleFile();
      if (f) {
        if (!fs.existsSync(f)) fs.writeFileSync(f, 'events:\n', 'utf-8');
        await vscode.window.showTextDocument(vscode.Uri.file(f), { viewColumn: vscode.ViewColumn.Beside });
      }
      break;
    }
    case 'createSeries':
      await appendSeries(msg.segments as Segment[], msg.note ? String(msg.note) : undefined);
      post({ type: 'created' });
      break;
    case 'editEvent':
      try {
        const done = await editSchedule(msg.op as EditOp);
        post({ type: done ? 'created' : 'reload' });
      } catch (e) {
        vscode.window.showErrorMessage(e instanceof Error ? e.message : String(e));
        post({ type: 'reload' });
      }
      break;
  }
}

// ── The page ────────────────────────────────────────────────────────

const CSS = String.raw`
:root{--red:#e5484d}
*{box-sizing:border-box}
body{margin:0;font-family:var(--vscode-font-family);font-size:13px;color:var(--vscode-foreground);background:var(--vscode-editor-background);height:100vh;display:flex;flex-direction:column}
button,input,select{font:inherit;color:var(--vscode-input-foreground);background:var(--vscode-input-background);border:1px solid var(--vscode-input-border,#555);border-radius:3px;padding:3px 8px}
button{cursor:pointer;background:var(--vscode-button-secondaryBackground);color:var(--vscode-button-secondaryForeground);border-color:transparent}
button:hover{background:var(--vscode-button-secondaryHoverBackground)}
button.primary{background:var(--vscode-button-background);color:var(--vscode-button-foreground)}
button.primary:hover{background:var(--vscode-button-hoverBackground)}
button:disabled{opacity:.45;cursor:default}
.bar{display:flex;align-items:center;gap:8px;padding:8px 12px;flex-wrap:wrap;border-bottom:1px solid var(--vscode-panel-border)}
.bar h2{margin:0 8px;font-size:16px;font-weight:600}
.sp{flex:1}
.seg button{border-radius:0}.seg button:first-child{border-radius:3px 0 0 3px}.seg button:last-child{border-radius:0 3px 3px 0}
.seg button.on{background:var(--vscode-button-background);color:var(--vscode-button-foreground)}
.srcs{display:flex;gap:6px;padding:6px 12px;flex-wrap:wrap;border-bottom:1px solid var(--vscode-panel-border);align-items:center}
.chip{display:inline-flex;align-items:center;gap:6px;padding:2px 8px;border-radius:12px;border:1px solid var(--vscode-panel-border);cursor:pointer;user-select:none}
.chip .dot{width:10px;height:10px;border-radius:50%}
.chip.off{opacity:.45}.chip.off .dot{background:transparent!important;border:2px solid currentColor}
.chip .x{opacity:.5;margin-left:2px}.chip .x:hover{opacity:1}
.chip.err{border-color:var(--red)}
.main{flex:1;display:flex;min-height:0}
.cal{flex:1;overflow:auto;min-width:0;position:relative}
.side{width:340px;border-left:1px solid var(--vscode-panel-border);padding:12px;overflow:auto}
.side h3{margin:0 0 8px}
.side label{display:block;margin:8px 0 2px;opacity:.8}
.side input[type=text],.side input[type=number],.side input[type=date],.side input[type=time]{width:100%}
.row{display:flex;gap:6px;align-items:center}
.days label{display:inline-flex;gap:3px;margin:0 8px 0 0;align-items:center}
table.pv{width:100%;border-collapse:collapse;margin-top:8px;font-size:12px}
table.pv td,table.pv th{padding:2px 4px;text-align:left;border-bottom:1px solid var(--vscode-panel-border)}
tr.hol td{color:var(--red);text-decoration:line-through}
tr.clash td:last-child{color:var(--vscode-editorWarning-foreground)}
.wk{display:grid;grid-template-columns:44px repeat(5,1fr);min-width:640px}
.wk .h{padding:6px 8px;border-bottom:1px solid var(--vscode-panel-border);border-left:1px solid var(--vscode-panel-border);position:sticky;top:0;background:var(--vscode-editor-background);z-index:3}
.wk .h.today b{background:var(--vscode-button-background);color:var(--vscode-button-foreground);border-radius:10px;padding:0 6px}
.wk .h small{display:block;color:var(--red);font-size:11px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.wnum{color:var(--vscode-descriptionForeground);font-size:11px;padding:6px 4px;position:sticky;top:0;background:var(--vscode-editor-background);z-index:3;border-bottom:1px solid var(--vscode-panel-border)}
.allday{border-left:1px solid var(--vscode-panel-border);border-bottom:1px solid var(--vscode-panel-border);padding:2px;min-height:22px}
.tcol{position:relative;border-left:1px solid var(--vscode-panel-border)}
.tcol.hol,.allday.hol,.cell.hol{background:rgba(229,72,77,.10)}
.hl{position:absolute;left:0;right:0;border-top:1px solid var(--vscode-panel-border);opacity:.5}
.tl{font-size:10px;color:var(--vscode-descriptionForeground);text-align:right;padding-right:4px;position:relative}
.tl span{position:absolute;right:4px;top:-7px}
.ev{position:absolute;border-radius:3px;padding:1px 4px;overflow:hidden;font-size:11px;line-height:1.25;border-left:3px solid;background:rgba(128,128,128,.18);cursor:default}
.ev b{font-weight:600}
.ev.free{opacity:.55;border-left-style:dotted}
.ev.ghost{border:1px dashed var(--vscode-button-background);border-left-width:3px;background:rgba(55,148,255,.18);z-index:2}
.ad{border-radius:3px;padding:0 4px;margin:1px 0;font-size:11px;border-left:3px solid;background:rgba(128,128,128,.18);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.mo{display:grid;grid-template-columns:36px repeat(5,1fr);min-width:640px}
.mo .dh{padding:4px 8px;font-weight:600;border-bottom:1px solid var(--vscode-panel-border);position:sticky;top:0;background:var(--vscode-editor-background);z-index:2}
.mh{grid-column:1/-1;position:sticky;top:25px;z-index:2;padding:6px 8px;font-weight:600;font-size:14px;background:var(--vscode-editor-background);border-bottom:1px solid var(--vscode-panel-border);color:var(--vscode-textLink-foreground)}
.cell.alt{background:rgba(128,128,128,.05)}
.cell{min-height:max(72px,calc((100vh - 190px) / 9.5));border-left:1px solid var(--vscode-panel-border);border-bottom:1px solid var(--vscode-panel-border);padding:2px 3px;overflow:hidden}
.cell.other{opacity:.45}
.cell .dn{font-size:11px;display:flex;gap:4px}.cell .dn.today b{background:var(--vscode-button-background);color:var(--vscode-button-foreground);border-radius:8px;padding:0 5px}
.cell .dn i{color:var(--red);font-style:normal;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.cell .more{font-size:11px;color:var(--vscode-textLink-foreground);cursor:pointer}
.wn{padding:4px;font-size:11px;color:var(--vscode-descriptionForeground);border-bottom:1px solid var(--vscode-panel-border);cursor:pointer}
.msg{padding:16px;opacity:.7}
.err{color:var(--vscode-errorForeground)}
.side textarea{width:100%;resize:vertical;font:inherit;color:var(--vscode-input-foreground);background:var(--vscode-input-background);border:1px solid var(--vscode-input-border,#555);border-radius:3px;padding:4px 6px}
.coll{display:block;width:100%;text-align:left;margin-top:12px;background:transparent;color:var(--vscode-foreground);border:none;border-top:1px solid var(--vscode-panel-border);border-radius:0;padding:8px 0 4px;font-weight:600}
.coll:hover{background:transparent;color:var(--vscode-textLink-foreground)}
.recbox{padding-left:12px;border-left:2px solid var(--vscode-panel-border)}
.tcol{cursor:cell;user-select:none}
.cell{cursor:pointer}
.co{padding:8px 16px 24px;max-width:1100px}
.cosum{padding:6px 0 10px;opacity:.85}
.cot{border-collapse:collapse;width:100%}
.cot td{padding:5px 8px;border-bottom:1px solid var(--vscode-panel-border);vertical-align:top}
.cow td{padding-top:16px;font-weight:600;border-bottom:2px solid var(--vscode-panel-border)}
.cow a{cursor:pointer;color:var(--vscode-textLink-foreground)}
.cow span{font-weight:400;opacity:.75}
.cow.now td{color:var(--vscode-textLink-activeForeground,var(--vscode-textLink-foreground))}
.cor{cursor:pointer}
.cor:hover{background:var(--vscode-list-hoverBackground)}
.cor.past{opacity:.55}
.cor .dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px}
.cd{white-space:nowrap;width:150px}.ct{white-space:nowrap;width:100px}.cl{width:160px}
.cc{opacity:.75;max-width:280px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.cor i.red{color:var(--red);font-style:normal;font-size:11px}
.ev.mine{cursor:move}
.ev .rs{position:absolute;left:0;right:0;bottom:0;height:6px;cursor:ns-resize}
.ev.mine:hover .rs{border-bottom:2px solid var(--vscode-focusBorder)}
.ad[data-id]{cursor:pointer}
.pop{position:fixed;z-index:50;min-width:220px;max-width:340px;padding:10px 12px;background:var(--vscode-editorWidget-background,var(--vscode-editor-background));color:var(--vscode-editorWidget-foreground,var(--vscode-foreground));border:1px solid var(--vscode-editorWidget-border,var(--vscode-panel-border));border-radius:6px;box-shadow:0 4px 16px rgba(0,0,0,.35);display:flex;flex-direction:column;gap:4px;font-size:12px}
.pop.menu{padding:4px;min-width:170px;gap:0}
.pop.menu button{background:transparent;color:inherit;text-align:left;border-radius:3px;padding:5px 10px}
.pop.menu button:hover{background:var(--vscode-list-hoverBackground)}
.pop hr{border:none;border-top:1px solid var(--vscode-panel-border);margin:3px 0;width:100%}
.pop .pt{display:flex;gap:6px;align-items:center;font-size:13px}
.pop .dot{width:10px;height:10px;border-radius:50%;flex:none}
.pop .pd{white-space:pre-wrap;margin-top:4px;padding-top:4px;border-top:1px solid var(--vscode-panel-border)}
.pop .ps{opacity:.65;font-size:11px;margin-top:2px}
.note{font-size:12px;opacity:.85;margin:0 0 6px}
.mnav{width:236px;flex:none;border-right:1px solid var(--vscode-panel-border);padding:6px 8px;overflow:auto}
.mnav.collapsed{width:auto;padding:6px 4px}
.mmb{background:transparent;border:none;padding:0 6px;font-size:15px;cursor:pointer;color:var(--vscode-foreground);min-width:20px;display:inline-block}
.mmb:hover{background:var(--vscode-toolbar-hoverBackground)}
.mm{margin-bottom:14px}
.mmh{display:flex;justify-content:space-between;align-items:center;margin-bottom:4px}
.mmt{border-collapse:collapse;width:100%;font-size:12px;text-align:center}
.mmt th{font-weight:400;color:var(--vscode-descriptionForeground);padding:2px 0;border-bottom:1px solid var(--vscode-panel-border)}
.mmt th:first-child,.mwn{color:var(--vscode-descriptionForeground);border-right:1px solid var(--vscode-panel-border);font-size:11px}
.mmt td{padding:3px 0;cursor:pointer;border-radius:2px}
.mmt td:hover{background:var(--vscode-list-hoverBackground)}
.mmt td.oth{opacity:.4}
.mmt td.we{color:var(--vscode-descriptionForeground)}
.mmt td.red{color:var(--red);font-weight:600}
.mmt tr.cur td:not(.mwn),.mmt td.sel{background:var(--vscode-list-inactiveSelectionBackground,rgba(128,128,128,.3))}
.mmt td.tod{background:var(--vscode-button-background)!important;color:var(--vscode-button-foreground);font-weight:700}
`;

const SCRIPT = String.raw`
var vscode = acquireVsCodeApi();
var DOW = ['Mån','Tis','Ons','Tors','Fre'];
var DOWFULL = ['Måndag','Tisdag','Onsdag','Torsdag','Fredag'];
var DAYKEY = ['Mon','Tue','Wed','Thu','Fri'];
var MON = ['januari','februari','mars','april','maj','juni','juli','augusti','september','oktober','november','december'];
var PX = 48;

function p2(n){return (n<10?'0':'')+n}
function P(s){var a=s.substr(0,10).split('-');return new Date(+a[0],+a[1]-1,+a[2])}
function F(d){return d.getFullYear()+'-'+p2(d.getMonth()+1)+'-'+p2(d.getDate())}
function addDays(s,n){var d=P(s);d.setDate(d.getDate()+n);return F(d)}
function dow(s){return (P(s).getDay()+6)%7}            // Monday = 0
function monday(s){return addDays(s,-dow(s))}
function isoWeek(s){var d=P(s);d.setDate(d.getDate()-((d.getDay()+6)%7)+3);var y=d.getFullYear();var j4=new Date(y,0,4);
  return {w:1+Math.round(((d-j4)/864e5-3+((j4.getDay()+6)%7))/7),y:y}}
function mondayOfWeek(y,w){var j4=F(new Date(y,0,4));return addDays(monday(j4),(w-1)*7)}
function esc(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;')}
var TODAY=F(new Date());

// ---- Swedish public holidays (red days) ----
var holCache={};
function easter(y){var a=y%19,b=Math.floor(y/100),c=y%100,d=Math.floor(b/4),e=b%4,f=Math.floor((b+8)/25),g=Math.floor((b-f+1)/3),
  h=(19*a+b-d-g+15)%30,i=Math.floor(c/4),k=c%4,l=(32+2*e+2*i-h-k)%7,m=Math.floor((a+11*h+22*l)/451),
  mo=Math.floor((h+l-7*m+114)/31),da=((h+l-7*m+114)%31)+1;return F(new Date(y,mo-1,da))}
function holidays(y){
  if(holCache[y])return holCache[y];
  var H={},e=easter(y);
  function add(s,n){H[s]=n}
  add(y+'-01-01','Nyårsdagen');add(y+'-01-06','Trettondedag jul');
  add(addDays(e,-2),'Långfredagen');add(e,'Påskdagen');add(addDays(e,1),'Annandag påsk');
  add(y+'-05-01','Första maj');add(addDays(e,39),'Kristi himmelsfärd');add(addDays(e,49),'Pingstdagen');
  add(y+'-06-06','Nationaldagen');
  for(var d=19;d<=25;d++){var s=y+'-06-'+p2(d);if(dow(s)===4){add(s,'Midsommarafton');add(addDays(s,1),'Midsommardagen')}}
  for(var k=0;k<7;k++){var t=F(new Date(y,9,31+k));if(dow(t)===5){add(t,'Alla helgons dag')}}
  add(y+'-12-24','Julafton');add(y+'-12-25','Juldagen');add(y+'-12-26','Annandag jul');add(y+'-12-31','Nyårsafton');
  holCache[y]=H;return H}
function holiday(s){return holidays(+s.substr(0,4))[s]||''}

// ---- state ----
var S={byId:{},navHidden:!!((vscode.getState()||{}).navHidden),navMonth:'',view:'week',anchor:TODAY,sources:[],events:[],loadedKey:'',preview:null,dialog:false,loading:true};
function vis(id){for(var i=0;i<S.sources.length;i++)if(S.sources[i].id===id)return S.sources[i].visible;return true}
function color(id){for(var i=0;i<S.sources.length;i++)if(S.sources[i].id===id)return S.sources[i].color;return '#888'}
var ALL={from:'2000-01-01',to:'2099-12-31'};
function range(){
  if(S.view==='course')return ALL;
  if(S.view==='week'){var m=monday(S.anchor);return {from:m,to:addDays(m,4)}}
  // The month view scrolls: it holds every week from S.mFrom to the end of S.mTo.
  if(!S.mFrom)monthWindow(S.anchor);
  var e=P(S.mTo);var last=new Date(e.getFullYear(),e.getMonth()+1,0);
  return {from:monday(S.mFrom),to:addDays(monday(F(last)),4)}
}
function load(force){
  syncNav();var r=range();S.loading=true;
  vscode.postMessage({type:'range',tag:'view',from:r.from,to:r.to,force:!!force,onlySchedule:S.view==='course'});
  render();
}
function nav(n){
  if(S.view==='course')return;
  if(S.view==='week')S.anchor=addDays(S.anchor,7*n);
  else{S.anchor=addMonths(S.anchor,n);showMonth(S.anchor);return}
  load();
}
function goto(s,v){
  S.anchor=s;if(v)S.view=v;
  if(S.view==='month'){showMonth(s);return}
  load();
}
// Month view: a window of months that grows as the teacher scrolls.
function monthWindow(c){S.mFrom=addMonths(monthStart(c),-1);S.mTo=addMonths(monthStart(c),4)}
function showMonth(c){
  var m=monthStart(c);
  if(!S.mFrom||m<S.mFrom||m>S.mTo)monthWindow(m);
  S.anchor=m;S.scrollTo=m;load();
}

// ---- mini-month navigator (left), Outlook style ----
function monthStart(s){return s.substr(0,8)+'01'}
function addMonths(s,n){var d=P(monthStart(s));d.setMonth(d.getMonth()+n);return F(d)}
function syncNav(){
  var m=monthStart(S.anchor);
  if(!S.navMonth||(m!==S.navMonth&&m!==addMonths(S.navMonth,1)))S.navMonth=m;
}
function navShift(n){S.navMonth=addMonths(S.navMonth,n);render()}
function toggleNav(){S.navHidden=!S.navHidden;try{vscode.setState({navHidden:S.navHidden})}catch(e){}render()}
function miniMonth(ms,first){
  var d0=P(ms),mo=d0.getMonth(),shown=range();
  var h='<div class="mm"><div class="mmh">'+(first?'<button class="mmb" onclick="navShift(-1)" title="Föregående månad">‹</button>':'<span class="mmb"></span>')+
    '<b>'+MON[mo][0].toUpperCase()+MON[mo].substr(1)+' '+d0.getFullYear()+'</b>'+
    (first?'<button class="mmb" onclick="navShift(1)" title="Nästa månad">›</button>':'<span class="mmb"></span>')+'</div>';
  h+='<table class="mmt"><tr><th>v</th><th>Må</th><th>Ti</th><th>On</th><th>To</th><th>Fr</th><th>Lö</th><th>Sö</th></tr>';
  var w=monday(ms);
  for(var r=0;r<6;r++,w=addDays(w,7)){
    var inWeek=S.view==='week'&&w===monday(S.anchor);
    h+='<tr class="'+(inWeek?'cur':'')+'"><td class="mwn" onclick="goto(\''+w+'\',\'week\')" title="Visa vecka '+isoWeek(w).w+'">'+isoWeek(w).w+'</td>';
    for(var i=0;i<7;i++){
      var d=addDays(w,i),dd=P(d),n=holiday(d);
      var cls=[];
      if(dd.getMonth()!==mo)cls.push('oth');
      if(i>4)cls.push('we');
      if(n)cls.push('red');
      if(d===TODAY)cls.push('tod');
      if(S.view==='month'&&d>=shown.from&&d<=shown.to&&dd.getMonth()===P(S.anchor).getMonth())cls.push('sel');
      h+='<td class="'+cls.join(' ')+'" onclick="goto(\''+d+'\',\'week\')"'+(n?' title="'+esc(n)+'"':'')+'>'+dd.getDate()+'</td>';
    }
    h+='</tr>';
  }
  return h+'</table></div>';
}
function miniNav(){
  if(S.navHidden)return '<div class="mnav collapsed"><button class="mmb" onclick="toggleNav()" title="Visa månadsöversikt">»</button></div>';

  return '<div class="mnav"><div style="text-align:right"><button class="mmb" onclick="toggleNav()" title="Dölj">«</button></div>'+miniMonth(S.navMonth,true)+miniMonth(addMonths(S.navMonth,1),false)+'</div>';
}

// ---- events per day ----
function onDay(day){
  var out=[];
  S.events.forEach(function(e){
    if(!vis(e.src))return;
    var sd=e.start.substr(0,10),ed=e.end.substr(0,10);
    if(sd<=day&&day<=ed)out.push(e);
  });
  return out;
}
function mins(t){return +t.substr(11,2)*60+ +t.substr(14,2)}
function segs(day,list){
  var out=[];
  list.forEach(function(e){
    if(e.allDay)return;
    var s=e.start.substr(0,10)<day?0:mins(e.start);
    var en=e.end.substr(0,10)>day?1440:mins(e.end);
    if(en<=s)en=s+30;
    out.push({e:e,s:s,en:en});
  });
  out.sort(function(a,b){return a.s-b.s||b.en-a.en});
  var cluster=[],cEnd=-1,lanes=[];
  function flush(){cluster.forEach(function(x){x.n=lanes.length});cluster=[];lanes=[];cEnd=-1}
  out.forEach(function(x){
    if(x.s>=cEnd)flush();
    var l=0;while(lanes[l]!==undefined&&lanes[l]>x.s)l++;
    lanes[l]=x.en;x.l=l;cluster.push(x);cEnd=Math.max(cEnd,x.en);
  });
  flush();
  return out;
}
function ghost(date,t1,t2,title,loc){return {id:'g',src:'ghost',ghost:true,title:title||'(ny bokning)',start:date+'T'+t1,end:date+'T'+t2,allDay:false,location:loc||'',free:false}}
function allEvents(){
  var ev=S.events.slice(),D=S.form;
  if(!S.dialog||!D)return ev;
  // While a booking is edited, its draft replaces it on screen.
  if(D.edit)ev=ev.filter(function(e){return e.id!==D.edit.id});
  if(D.rec&&S.preview){
    S.preview.items.forEach(function(it){if(!it.skip)ev.push(ghost(it.date,S.preview.t1,S.preview.t2,S.preview.title,S.preview.location))});
  }else if(D.date&&D.t1&&D.t2&&D.t2>D.t1){
    ev.push(ghost(D.date,D.t1,D.t2,D.title,D.location));
  }
  return ev;
}

// ---- rendering ----
function hm(t){return t.substr(11,5)}
function evHtml(x,H0){
  var e=x.e,top=(x.s-H0*60)*PX/60,h=Math.max(16,(x.en-x.s)*PX/60-1);
  var c=e.ghost?'':'border-left-color:'+color(e.src)+';';
  var w=100/x.n,own=!e.ghost&&e.src==='schedule';
  return '<div class="ev'+(e.free?' free':'')+(e.ghost?' ghost':'')+(own?' mine':'')+'"'+(e.ghost?'':' data-id="'+esc(e.id)+'"')+' style="'+c+'top:'+top+'px;height:'+h+'px;left:'+(x.l*w)+'%;width:calc('+w+'% - 2px)" title="'+esc(e.title+'\n'+hm(e.start)+'-'+hm(e.end)+(e.location?'\n'+e.location:'')+(own?'\n\nDra för att flytta, dubbelklicka för att redigera':''))+'"><b>'+esc(hm(e.start))+'</b> '+esc(e.title)+(h>34&&e.location?'<br>'+esc(e.location):'')+(own?'<div class="rs" title="Dra för att ändra sluttid"></div>':'')+'</div>';
}
function weekView(){
  var m=monday(S.anchor),all=allEvents(),saved=S.events;S.events=all;
  var days=[0,1,2,3,4].map(function(i){return addDays(m,i)});
  var H0=7,H1=19,anyAllDay=false;
  var per=days.map(function(d){var l=onDay(d);l.forEach(function(e){if(e.allDay)anyAllDay=true});return segs(d,l)});
  per.forEach(function(sg){sg.forEach(function(x){H0=Math.min(H0,Math.floor(x.s/60));H1=Math.max(H1,Math.ceil(x.en/60))})});
  curH0=H0;curH1=H1;
  var wk=isoWeek(m);
  var h='<div class="wk"><div class="wnum">v.'+wk.w+'</div>';
  days.forEach(function(d,i){var n=holiday(d);
    h+='<div class="h'+(d===TODAY?' today':'')+'" '+(n?'style="color:var(--red)"':'')+'>'+DOW[i]+' <b>'+P(d).getDate()+'</b> '+MON[P(d).getMonth()].substr(0,3)+(n?'<small>'+esc(n)+'</small>':'')+'</div>'});
  if(anyAllDay){
    h+='<div class="allday"></div>';
    days.forEach(function(d){var l=onDay(d).filter(function(e){return e.allDay});
      h+='<div class="allday'+(holiday(d)?' hol':'')+'">'+l.map(function(e){return '<div class="ad" data-id="'+esc(e.id)+'" style="border-left-color:'+color(e.src)+'" title="'+esc(e.title)+'">'+esc(e.title)+'</div>'}).join('')+'</div>'});
  }
  var tl='<div class="tl-col" style="position:relative;height:'+((H1-H0)*PX)+'px">';
  for(var hh=H0;hh<H1;hh++)tl+='<div class="tl" style="position:absolute;top:'+((hh-H0)*PX)+'px;right:0;left:0"><span>'+p2(hh)+'</span></div>';
  tl+='</div>';
  h+=tl;
  days.forEach(function(d,i){
    var g='';for(var hh=H0;hh<H1;hh++)g+='<div class="hl" style="top:'+((hh-H0)*PX)+'px"></div>';
    if(d===TODAY){var n=new Date();var t=n.getHours()*60+n.getMinutes();if(t>=H0*60&&t<=H1*60)g+='<div style="position:absolute;left:0;right:0;top:'+((t-H0*60)*PX/60)+'px;border-top:2px solid var(--red);z-index:4"></div>'}
    h+='<div class="tcol'+(holiday(d)?' hol':'')+'" data-day="'+d+'" style="height:'+((H1-H0)*PX)+'px">'+g+per[i].map(function(x){return evHtml(x,H0)}).join('')+'</div>';
  });
  S.events=saved;
  return h+'</div>';
}
function monthView(){
  var r=range(),all=allEvents(),saved=S.events;S.events=all;
  // A month heading goes above the week that holds the month's first weekday.
  var firsts={};
  for(var m=S.mFrom;m<=S.mTo;m=addMonths(m,1)){var f=m;while(dow(f)>4)f=addDays(f,1);firsts[f]=m}
  var h='<div class="mo"><div class="dh"></div>'+DOWFULL.map(function(n){return '<div class="dh">'+n+'</div>'}).join('');
  for(var w=r.from;w<=r.to;w=addDays(w,7)){
    for(var k=0;k<5;k++){var fm=firsts[addDays(w,k)];
      if(fm){var md=P(fm);h+='<div class="mh" id="m-'+fm.substr(0,7)+'" data-month="'+fm+'">'+MON[md.getMonth()][0].toUpperCase()+MON[md.getMonth()].substr(1)+' '+md.getFullYear()+'</div>'}}
    h+='<div class="wn" onclick="goto(\''+w+'\',\'week\')" title="Visa veckan">'+isoWeek(w).w+'</div>';
    for(var i=0;i<5;i++){
      var d=addDays(w,i),dd=P(d),n=holiday(d);
      var l=onDay(d).sort(function(a,b){return (b.allDay?1:0)-(a.allDay?1:0)||a.start.localeCompare(b.start)});
      h+='<div class="cell'+(n?' hol':'')+(dd.getMonth()%2?' alt':'')+'" data-day="'+d+'" title="Klicka för ny bokning"><div class="dn'+(d===TODAY?' today':'')+'"><b>'+dd.getDate()+(dd.getDate()===1||d===r.from?' '+MON[dd.getMonth()].substr(0,3):'')+'</b>'+(n?'<i>'+esc(n)+'</i>':'')+'</div>';
      l.slice(0,4).forEach(function(e){
        h+='<div class="ad"'+(e.ghost?'':' data-id="'+esc(e.id)+'"')+' style="border-left-color:'+(e.ghost?'var(--vscode-button-background)':color(e.src))+(e.free?';opacity:.55':'')+(e.ghost?';border-style:dashed':'')+'" title="'+esc(e.title)+'">'+(e.allDay?'':'<b>'+hm(e.start)+'</b> ')+esc(e.title)+'</div>'});
      if(l.length>4)h+='<div class="more" onclick="goto(\''+d+'\',\'week\')">+'+(l.length-4)+' till</div>';
      h+='</div>';
    }
  }
  S.events=saved;
  return h+'</div>';
}
// The heading shows the month at the top of the scrolled view.
function monthAtTop(cal){
  var hs=cal.querySelectorAll?cal.querySelectorAll('.mh'):[],top=cal.scrollTop+40,cur=null;
  for(var i=0;i<hs.length;i++){if(hs[i].offsetTop<=top)cur=hs[i].getAttribute('data-month');else break}
  return cur;
}
document.addEventListener('scroll',function(ev){
  var cal=ev.target;
  if(S.view!=='month'||!cal||!cal.classList||!cal.classList.contains('cal'))return;
  var m=monthAtTop(cal);
  if(m&&m!==S.anchor){S.anchor=m;var t=document.querySelector('.bar h2');if(t)t.textContent=title()}
  if(S.extending)return;
  if(cal.scrollTop<120){S.extending=true;S.keepBottom=cal.scrollHeight-cal.scrollTop;S.mFrom=addMonths(S.mFrom,-2);load()}
  else if(cal.scrollTop+cal.clientHeight>cal.scrollHeight-120){S.extending=true;S.mTo=addMonths(S.mTo,2);load()}
},true);
// ---- course overview: every booking in schedule.yaml, week by week ----
function courseView(){
  var list=S.events.filter(function(e){return e.src==='schedule'}).sort(function(a,b){return a.start.localeCompare(b.start)});
  if(!list.length)return '<div class="msg">Inga bokningar i schedule.yaml än. Klicka och dra i veckovyn, eller använd ＋ Ny bokning.</div>';
  var mins_=0;list.forEach(function(e){if(!e.allDay)mins_+=Math.max(0,mins(e.end)-mins(e.start))});
  var first=list[0].start.substr(0,10),last=list[list.length-1].start.substr(0,10);
  var left=list.filter(function(e){return e.start.substr(0,10)>=TODAY}).length;
  var h='<div class="co"><div class="cosum"><b>'+list.length+'</b> bokningar · <b>'+String(Math.round(mins_/6)/10).replace('.',',')+'</b> timmar · '+
    'v.'+isoWeek(first).w+' '+isoWeek(first).y+' – v.'+isoWeek(last).w+' '+isoWeek(last).y+' · '+left+' kvar från idag</div>';
  var cur='',tw=monday(TODAY);
  h+='<table class="cot">';
  list.forEach(function(e){
    var d=e.start.substr(0,10),w=monday(d);
    if(w!==cur){
      cur=w;var wk=isoWeek(w),f=addDays(w,4),a=P(w),b=P(f);
      var n=list.filter(function(x){return monday(x.start.substr(0,10))===w}).length;
      h+='<tr class="cow'+(w===tw?' now':'')+'"><td colspan="5"><a onclick="goto(\''+w+'\',\'week\')" title="Visa veckan">Vecka '+wk.w+'</a>'+
        '<span> · '+a.getDate()+(a.getMonth()!==b.getMonth()?' '+MON[a.getMonth()].substr(0,3):'')+' – '+b.getDate()+' '+MON[b.getMonth()]+' '+b.getFullYear()+' · '+n+' st'+(w===tw?' · denna vecka':'')+'</span></td></tr>';
    }
    var hol=holiday(d),past=d<TODAY;
    var c=(e.description||'').split('\n')[0];
    h+='<tr class="cor'+(past?' past':'')+'" data-id="'+esc(e.id)+'" title="Klicka för detaljer, dubbelklicka för att redigera">'+
      '<td class="cd"><span class="dot" style="background:'+color(e.src)+'"></span>'+DOW[dow(d)]+' '+P(d).getDate()+' '+MON[P(d).getMonth()].substr(0,3)+(hol?' <i class="red">'+esc(hol)+'</i>':'')+'</td>'+
      '<td class="ct">'+(e.allDay?'heldag':hm(e.start)+'–'+hm(e.end))+'</td>'+
      '<td class="cn"><b>'+esc(e.title)+'</b></td>'+
      '<td class="cl">'+esc(e.location||'')+'</td>'+
      '<td class="cc">'+esc(c)+((e.description||'').indexOf('\n')>=0?' …':'')+'</td></tr>';
  });
  return h+'</table></div>';
}
function title(){
  if(S.view==='course')return 'Kursöversikt';
  if(S.view==='week'){var m=monday(S.anchor),f=addDays(m,4),a=P(m),b=P(f),w=isoWeek(m);
    return 'Vecka '+w.w+' · '+a.getDate()+(a.getMonth()!==b.getMonth()?' '+MON[a.getMonth()].substr(0,3):'')+' – '+b.getDate()+' '+MON[b.getMonth()]+' '+b.getFullYear()}
  var d=P(S.anchor);return MON[d.getMonth()][0].toUpperCase()+MON[d.getMonth()].substr(1)+' '+d.getFullYear();
}
function srcBar(){
  var h=S.sources.map(function(s){
    return '<span class="chip'+(s.visible?'':' off')+(s.error?' err':'')+'" onclick="toggle(\''+s.id+'\')" title="'+esc(s.error||'Visa/dölj')+'"><span class="dot" style="background:'+s.color+'"></span>'+esc(s.name)+(s.error?' ⚠':'')+
    (s.id!=='schedule'?'<span class="x" onclick="event.stopPropagation();removeSrc(\''+s.id+'\')" title="Ta bort">✕</span>':'')+'</span>'}).join('');
  return h+'<button onclick="vscode.postMessage({type:\'addSource\'})">+ Outlook-kalender</button>';
}
function dialog(){
  if(!S.dialog)return '';
  var D=S.form;
  var h='<div class="side"><h3>'+(D.edit?'Redigera bokning':'Ny bokning')+'</h3>';
  if(D.edit&&D.edit.series)h+='<p class="note">Ingår i en serie i schedule.yaml. När du sparar eller tar bort delas serien upp i enskilda bokningar, och bara den här ändras.</p>';
  h+='<label>Titel</label><input type="text" id="f_title" value="'+esc(D.title)+'" placeholder="t.ex. Föreläsning 3">';
  h+='<label>'+(D.rec?'Första dag':'Datum')+'</label><input type="date" id="f_date" value="'+D.date+'" onchange="formChanged(\'date\')">';
  h+='<label>Tid</label><div class="row"><input type="time" id="f_t1" step="1800" value="'+D.t1+'" onchange="formChanged(\'t1\')"><span>–</span><input type="time" id="f_t2" step="1800" value="'+D.t2+'" onchange="formChanged(\'t2\')"></div>';
  h+='<label>Plats</label><input type="text" id="f_loc" value="'+esc(D.location)+'">';
  h+='<label>Kommentar</label><textarea id="f_desc" rows="4" placeholder="Visas som beskrivning på händelsen i Canvas">'+esc(D.comment)+'</textarea>';
  if(!D.edit)h+='<button class="coll" onclick="toggleRec()">'+(D.rec?'▾':'▸')+' Återkommande</button>';
  if(D.rec&&!D.edit){
    h+='<div class="recbox">';
    h+='<label>Dagar</label><div class="days">'+DAYKEY.map(function(k,i){return '<label><input type="checkbox" class="f_day" value="'+k+'"'+(D.days.indexOf(k)>=0?' checked':'')+' onchange="formChanged()">'+DOW[i]+'</label>'}).join('')+'</div>';
    h+='<label>Period</label><div class="seg"><button class="'+(D.mode==='date'?'on':'')+'" onclick="setMode(\'date\')">Datum</button><button class="'+(D.mode==='week'?'on':'')+'" onclick="setMode(\'week\')">Veckor</button></div>';
    if(D.mode==='date'){
      h+='<label>Från</label><input type="date" id="f_from" value="'+D.from+'" onchange="formChanged()"><label>Till</label><input type="date" id="f_to" value="'+D.to+'" onchange="formChanged()">';
    }else{
      h+='<label>Från vecka / år</label><div class="row"><input type="number" id="f_w1" min="1" max="53" value="'+D.w1+'" onchange="formChanged()"><input type="number" id="f_y1" value="'+D.y1+'" onchange="formChanged()"></div>';
      h+='<label>Till vecka / år</label><div class="row"><input type="number" id="f_w2" min="1" max="53" value="'+D.w2+'" onchange="formChanged()"><input type="number" id="f_y2" value="'+D.y2+'" onchange="formChanged()"></div>';
    }
    h+='<label style="display:flex;gap:6px;align-items:center"><input type="checkbox" id="f_skip"'+(D.skip?' checked':'')+' onchange="formChanged()"> Hoppa över röda dagar</label>';
    h+='</div>';
  }
  if(D.edit)h+='<div class="row" style="margin-top:12px"><button class="primary" onclick="doSave()">Spara</button><button onclick="doDeleteEdit()">Ta bort</button><button onclick="closeDialog()">Avbryt</button></div>';
  else h+='<div class="row" style="margin-top:12px">'+(D.rec?'<button class="primary" onclick="doPreview()">Förhandsgranska</button>':'<button class="primary" onclick="doCreateSingle()">Lägg till i schedule.yaml</button>')+'<button onclick="closeDialog()">Avbryt</button></div>';
  if(D.error)h+='<p class="err">'+esc(D.error)+'</p>';
  if(D.rec&&S.preview){
    var pv=S.preview,ok=pv.items.filter(function(i){return !i.skip}).length;
    h+='<p><b>'+ok+'</b> tillfällen skapas'+(pv.items.length-ok?', '+(pv.items.length-ok)+' hoppas över':'')+'. Förhandsgranskningen syns även i kalendern.</p>';
    h+='<table class="pv"><tr><th>v.</th><th>Dag</th><th>Datum</th><th></th></tr>'+pv.items.map(function(i){
      return '<tr class="'+(i.hol&&i.skip?'hol':'')+(i.clash.length?' clash':'')+'"><td>'+i.week+'</td><td>'+DOW[dow(i.date)]+'</td><td>'+i.date+'</td><td>'+(i.hol?esc(i.hol)+(i.skip?' (hoppas över)':''):'')+(i.clash.length?'⚠ '+esc(i.clash.join(', ')):'')+'</td></tr>'}).join('')+'</table>';
    h+='<div class="row" style="margin-top:12px"><button class="primary" onclick="doCreate()"'+(ok?'':' disabled')+'>Lägg till i schedule.yaml</button></div>';
  }
  return h+'</div>';
}
function render(){
  // A redraw replaces the form; keep what the teacher has typed so far.
  if(S.dialog&&S.form&&!S.fresh&&document.getElementById('f_title'))readForm();
  S.fresh=false;
  var body;
  if(S.loading&&!S.events.length)body='<div class="msg">Hämtar kalendrar…</div>';
  else body=S.view==='week'?weekView():S.view==='month'?monthView():courseView();
  var oldCal=document.querySelector?document.querySelector('.cal'):null;
  var keepTop=oldCal&&S.lastView===S.view?oldCal.scrollTop:0;S.lastView=S.view;
  document.getElementById('app').innerHTML=
   '<div class="bar">'+(S.view==='course'?'':'<button onclick="nav(-1)">◀</button><button onclick="goto(TODAY)">Idag</button><button onclick="nav(1)">▶</button>')+'<h2>'+esc(title())+'</h2><span class="sp"></span>'+
   '<span class="seg"><button class="'+(S.view==='week'?'on':'')+'" onclick="goto(S.anchor,\'week\')">Vecka</button><button class="'+(S.view==='month'?'on':'')+'" onclick="goto(S.anchor,\'month\')">Månad</button><button class="'+(S.view==='course'?'on':'')+'" onclick="goto(S.anchor,\'course\')" title="Alla bokningar i kursen">Kurs</button></span>'+
   '<button class="primary" onclick="openDialog()" title="Eller klicka och dra i veckovyn">＋ Ny bokning</button><button onclick="load(true)" title="Hämta Outlook igen">⟳</button><button onclick="vscode.postMessage({type:\'openSchedule\'})">schedule.yaml</button></div>'+
   '<div class="srcs">'+srcBar()+'</div>'+
   '<div class="main">'+miniNav()+'<div class="cal">'+body+'</div>'+dialog()+'</div>';
  var cal=document.querySelector?document.querySelector('.cal'):null;
  if(!cal)return;
  if(S.view==='month'&&S.scrollTo){
    var mh=document.getElementById('m-'+S.scrollTo.substr(0,7));
    if(mh){cal.scrollTop=Math.max(0,mh.offsetTop-30);S.scrollTo=null}
  }else if(S.view==='month'&&S.keepBottom!=null){
    cal.scrollTop=cal.scrollHeight-S.keepBottom;S.keepBottom=null;   // months added above: stay put
  }else cal.scrollTop=keepTop;
}

// ---- actions ----
function toggle(id){S.sources.forEach(function(s){if(s.id===id){s.visible=!s.visible;vscode.postMessage({type:'toggle',id:id,visible:s.visible})}});render()}
function removeSrc(id){vscode.postMessage({type:'removeSource',id:id})}
function hhmm(m){return p2(Math.floor(m/60))+':'+p2(m%60)}
function toMin(t){return +t.substr(0,2)*60+ +t.substr(3,2)}
// Recurrence defaults that follow the chosen day: that weekday, and that
// week and year as both the first and the last week, for the teacher to extend.
function recDefaults(D){
  var d=D.date,k=dow(d),a=isoWeek(d);
  D.days=[DAYKEY[Math.min(k,4)]];D.from=d;D.to=d;D.w1=a.w;D.y1=a.y;D.w2=a.w;D.y2=a.y;
}
/** Open the booking box for a day and a time span in minutes. */
function openBooking(date,s,e){
  var old=S.dialog&&S.form?(document.getElementById('f_title')?readForm():S.form):null;
  if(dow(date)>4)date=addDays(monday(date),4);
  var D={title:old?old.title:'',location:old?old.location:'',comment:old?old.comment:'',
    date:date,t1:hhmm(s),t2:hhmm(e),rec:old&&!old.edit?old.rec:false,mode:old?old.mode:'week',skip:true,error:''};
  recDefaults(D);
  S.form=D;S.preview=null;S.dialog=true;S.fresh=true;render();   // fresh: the old inputs on screen must not overwrite D
  var t=document.getElementById('f_title');if(t&&t.focus)t.focus();
}
function openDialog(){
  var r=range(),d=(TODAY>=r.from&&TODAY<=r.to)?TODAY:r.from;
  openBooking(d,8*60+15,10*60);
}
function closeDialog(){S.dialog=false;S.preview=null;S.form=null;render()}
function readForm(){
  var D=S.form;
  function v(id){var e=document.getElementById(id);return e?e.value:''}
  D.title=v('f_title');D.location=v('f_loc');D.comment=v('f_desc');D.date=v('f_date')||D.date;D.t1=v('f_t1');D.t2=v('f_t2');
  if(D.rec&&document.getElementById('f_skip')){
    D.skip=document.getElementById('f_skip').checked;
    D.days=[].slice.call(document.querySelectorAll('.f_day')).filter(function(c){return c.checked}).map(function(c){return c.value});
    if(D.mode==='date'){D.from=v('f_from');D.to=v('f_to')}
    else{D.w1=+v('f_w1');D.y1=+v('f_y1');D.w2=+v('f_w2');D.y2=+v('f_y2')}
  }
  return D;
}
// A changed date or time moves the draft in the calendar; a stale preview goes.
function formChanged(what){
  var D=readForm();D.error='';
  if(what==='t1'&&D.t1&&D.t2&&D.t2<=D.t1){D.t2=hhmm(Math.min(1439,toMin(D.t1)+60))}
  if(what==='date'&&D.date){var prev=D.from;var keep=D.rec&&prev&&D.days.length>1;var days=D.days;recDefaults(D);if(keep)D.days=days;
    if(D.date<range().from||D.date>range().to){S.preview=null;goto(D.date);return}}
  S.preview=null;render();
}
function toggleRec(){var D=readForm();D.rec=!D.rec;D.error='';S.preview=null;render()}
function doCreateSingle(){
  var D=readForm();D.error='';
  if(!D.title.trim())D.error='Skriv en titel.';
  else if(!D.date)D.error='Välj ett datum.';
  else if(!D.t1||!D.t2||D.t2<=D.t1)D.error='Sluttiden måste vara efter starttiden.';
  if(D.error){render();return}
  vscode.postMessage({type:'createSeries',segments:[{title:D.title.trim(),date:D.date,time:D.t1+'-'+D.t2,location:D.location.trim(),description:D.comment.trim()}]});
}
function setMode(m){readForm();var D=S.form;
  if(m==='week'&&D.mode==='date'&&D.from&&D.to){var a=isoWeek(D.from),b=isoWeek(D.to);D.w1=a.w;D.y1=a.y;D.w2=b.w;D.y2=b.y}
  if(m==='date'&&D.mode==='week'){D.from=mondayOfWeek(D.y1,D.w1);D.to=addDays(mondayOfWeek(D.y2,D.w2),4)}
  D.mode=m;S.preview=null;render()}
function period(D){
  if(D.mode==='week'){return {from:mondayOfWeek(D.y1,D.w1),to:addDays(mondayOfWeek(D.y2,D.w2),4)}}
  return {from:D.from,to:D.to};
}
function doPreview(){
  var D=readForm();D.error='';
  var pr=period(D);
  if(!D.title.trim())D.error='Skriv en titel.';
  else if(!D.days.length)D.error='Välj minst en dag.';
  else if(!pr.from||!pr.to||pr.to<pr.from)D.error='Slutdatum måste vara efter startdatum.';
  else if(!D.t1||!D.t2||D.t2<=D.t1)D.error='Sluttiden måste vara efter starttiden.';
  else if(pr.to>addDays(pr.from,800))D.error='Perioden är för lång.';
  if(D.error){S.preview=null;render();return}
  vscode.postMessage({type:'range',tag:'preview',from:pr.from,to:pr.to});
}
function buildPreview(events){
  var D=S.form,pr=period(D),items=[];
  for(var d=pr.from;d<=pr.to;d=addDays(d,1)){
    var k=dow(d);if(k>4||D.days.indexOf(DAYKEY[k])<0)continue;
    var hol=holiday(d),clash=[];
    events.forEach(function(e){
      if(e.src==='schedule'||e.allDay||e.free||!vis(e.src))return;
      var sd=e.start.substr(0,10),ed=e.end.substr(0,10);
      if(d<sd||d>ed)return;
      var s=sd<d?0:mins(e.start),en=ed>d?1440:mins(e.end);
      var a=+D.t1.substr(0,2)*60+ +D.t1.substr(3,2),b=+D.t2.substr(0,2)*60+ +D.t2.substr(3,2);
      if(s<b&&en>a)clash.push(e.title);
    });
    items.push({date:d,week:isoWeek(d).w,hol:hol,skip:!!(hol&&D.skip),clash:clash});
  }
  if(!items.length){D.error='Inga datum matchar valda dagar i perioden.';S.preview=null;render();return}
  S.preview={items:items,title:D.title,t1:D.t1,t2:D.t2,location:D.location};
  goto(items[0].date);
}
// One schedule.yaml entry per occasion, never a days/start_date series: a
// single occasion can then be cancelled by deleting its entry alone.
function doCreate(){
  var D=S.form,pv=S.preview;
  var kept=pv.items.filter(function(it){return !it.skip});
  var segs=kept.map(function(it){
    return {title:D.title.trim(),date:it.date,time:D.t1+'-'+D.t2,location:D.location.trim(),description:D.comment.trim()};
  });
  var w1=isoWeek(kept[0].date),w2=isoWeek(kept[kept.length-1].date);
  var days=DAYKEY.filter(function(k,i){return kept.some(function(r){return dow(r.date)===i})}).map(function(k){return DOW[DAYKEY.indexOf(k)].toLowerCase()});
  var note=D.title.trim()+': '+kept.length+' tillfällen, '+days.join(' och ')+' '+D.t1+'-'+D.t2+', v.'+w1.w+(w1.y!==w2.y?' '+w1.y:'')+' till v.'+w2.w+' '+w2.y;
  vscode.postMessage({type:'createSeries',segments:segs,note:note});
}

// ---- click and drag in the week view: a new booking, snapped to half hours ----
var curH0=7,curH1=19,drag=null;
function slotAt(col,y){
  var r=col.getBoundingClientRect();
  var m=curH0*60+(y-r.top)/PX*60;
  m=Math.floor(m/30)*30;
  return Math.max(curH0*60,Math.min(curH1*60-30,m));
}
function dragSpan(){return {s:Math.min(drag.a,drag.b),e:Math.max(drag.a,drag.b)+30}}
function showDrag(){
  var el=document.getElementById('dragsel');
  if(!el){el=document.createElement('div');el.id='dragsel';el.className='ev ghost'}
  if(el.parentNode!==drag.col)drag.col.appendChild(el);
  var sp=dragSpan();
  el.style.top=((sp.s-curH0*60)*PX/60)+'px';el.style.height=((sp.e-sp.s)*PX/60-1)+'px';
  el.style.left='0';el.style.width='calc(100% - 2px)';
  el.innerHTML='<b>'+hhmm(sp.s)+' – '+hhmm(sp.e)+'</b>';
}
// The minute under the pointer, unsnapped, in a day column.
function rawMin(col,y){var r=col.getBoundingClientRect();return curH0*60+(y-r.top)/PX*60}
function evOf(el){var n=el&&el.closest&&el.closest('[data-id]');return n?S.byId[n.getAttribute('data-id')]:null}
function mine(e){return e&&e.src==='schedule'}

// Moving or stretching an existing booking: a dashed copy follows the pointer.
function showMove(){
  var el=document.getElementById('dragsel');
  if(!el){el=document.createElement('div');el.id='dragsel';el.className='ev ghost'}
  if(el.parentNode!==drag.col)drag.col.appendChild(el);
  el.style.top=((drag.s-curH0*60)*PX/60)+'px';el.style.height=((drag.en-drag.s)*PX/60-1)+'px';
  el.style.left='0';el.style.width='calc(100% - 2px)';
  el.innerHTML='<b>'+hhmm(drag.s)+' – '+hhmm(drag.en)+'</b> '+esc(drag.e.title);
  if(drag.el)drag.el.style.opacity='.35';
}

document.addEventListener('mousedown',function(ev){
  if(ev.button!==0||!ev.target.closest)return;
  if(!ev.target.closest('.pop'))hidePop();
  var e=evOf(ev.target);
  if(e){
    var evEl=ev.target.closest('.ev'),col=evEl&&evEl.closest('.tcol');
    // Course bookings in the week view can be moved and stretched; anything
    // else only opens its details on click.
    if(!mine(e)||!col||S.view!=='week'){drag={mode:'click',e:e,x:ev.clientX,y:ev.clientY};return}
    ev.preventDefault();
    var s=mins(e.start),en=mins(e.end);
    drag={mode:ev.target.closest('.rs')?'resize':'move',e:e,el:evEl,col:col,day:col.getAttribute('data-day'),
      s0:s,en0:en,s:s,en:en,grab:rawMin(col,ev.clientY)-s,x:ev.clientX,y:ev.clientY,moved:false};
    return;
  }
  var tc=ev.target.closest('.tcol');
  if(!tc)return;
  ev.preventDefault();
  var m=slotAt(tc,ev.clientY);
  drag={mode:'new',col:tc,day:tc.getAttribute('data-day'),a:m,b:m};
  showDrag();
});
document.addEventListener('mousemove',function(ev){
  if(!drag)return;
  if(drag.mode==='new'){var m=slotAt(drag.col,ev.clientY);if(m!==drag.b){drag.b=m;showDrag()}return}
  if(drag.mode==='click')return;
  if(!drag.moved&&Math.abs(ev.clientX-drag.x)+Math.abs(ev.clientY-drag.y)<5)return;
  drag.moved=true;
  if(drag.mode==='resize'){
    var end=Math.round(rawMin(drag.col,ev.clientY)/30)*30;
    drag.en=Math.max(drag.s0+30,Math.min(24*60,end));
  }else{
    var under=document.elementFromPoint?document.elementFromPoint(ev.clientX,ev.clientY):null;
    var col=(under&&under.closest&&under.closest('.tcol'))||drag.col;
    if(col!==drag.col){drag.col=col;drag.day=col.getAttribute('data-day')}
    var len=drag.en0-drag.s0;
    var st=Math.round((rawMin(col,ev.clientY)-drag.grab)/30)*30;
    st=Math.max(0,Math.min(24*60-len,st));
    drag.s=st;drag.en=st+len;
  }
  showMove();
});
document.addEventListener('mouseup',function(ev){
  if(!drag)return;
  var d=drag;drag=null;
  if(d.mode==='new'){var sp={s:Math.min(d.a,d.b),e:Math.max(d.a,d.b)+30};openBooking(d.day,sp.s,sp.e);return}
  if(d.mode==='click'||!d.moved){showPop(d.e,ev.clientX,ev.clientY,false);return}
  var day=d.day,t=hhmm(d.s)+'-'+hhmm(d.en);
  if(day===d.e.start.substr(0,10)&&t===d.e.time){render();return}
  saveEvent(d.e,{title:d.e.title,date:day,time:t,location:d.e.location,description:d.e.description||''});
});
document.addEventListener('dblclick',function(ev){
  var e=evOf(ev.target);
  if(e){hidePop();if(mine(e))openEdit(e);else showPop(e,ev.clientX,ev.clientY,false)}
});
document.addEventListener('contextmenu',function(ev){
  var e=evOf(ev.target);
  if(!e)return;
  ev.preventDefault();
  showPop(e,ev.clientX,ev.clientY,true);
});
document.addEventListener('click',function(ev){
  if(!ev.target.closest||ev.target.closest('[data-id]')||ev.target.closest('.pop'))return;
  var cell=ev.target.closest('.cell');
  if(cell&&!ev.target.closest('.more'))openBooking(cell.getAttribute('data-day'),9*60,10*60);
});
document.addEventListener('keydown',function(ev){
  if(ev.key!=='Escape')return;
  if(document.getElementById('pop')){hidePop();return}
  if(S.dialog&&!drag)closeDialog();
});

// ---- details card (click) and menu (right-click) ----
function hidePop(){var p=document.getElementById('pop');if(p&&p.parentNode)p.parentNode.removeChild(p)}
function srcName(id){for(var i=0;i<S.sources.length;i++)if(S.sources[i].id===id)return S.sources[i].name;return ''}
function showPop(e,x,y,menu){
  hidePop();
  var p=document.createElement('div');p.id='pop';p.className='pop'+(menu?' menu':'');
  var when=DOWFULL[dow(e.start.substr(0,10))]||'';
  when+=' '+P(e.start).getDate()+' '+MON[P(e.start).getMonth()]+(e.allDay?'':', '+hm(e.start)+'–'+hm(e.end));
  var h='';
  if(menu){
    h+=mine(e)?'<button onclick="popEdit()">✎ Redigera</button><button onclick="popDelete()">🗑 Ta bort</button><hr><button onclick="popInfo()">Visa detaljer</button>'
              :'<button onclick="popInfo()">Visa detaljer</button>';
  }else{
    h+='<div class="pt"><span class="dot" style="background:'+color(e.src)+'"></span><b>'+esc(e.title)+'</b></div>';
    h+='<div>'+esc(when)+'</div>';
    if(e.location)h+='<div>📍 '+esc(e.location)+'</div>';
    if(e.description)h+='<div class="pd">'+esc(e.description)+'</div>';
    h+='<div class="ps">'+esc(srcName(e.src))+(mine(e)?'':' · skrivskyddad')+(e.series?' · del av en serie':'')+'</div>';
    if(mine(e))h+='<div class="row" style="margin-top:8px"><button class="primary" onclick="popEdit()">✎ Redigera</button><button onclick="popDelete()">Ta bort</button></div>';
  }
  p.innerHTML=h;
  document.body.appendChild(p);
  S.popEvent=e;
  var w=p.offsetWidth||260,hh=p.offsetHeight||120,vw=window.innerWidth||1200,vh=window.innerHeight||800;
  p.style.left=Math.max(4,Math.min(x+6,vw-w-8))+'px';
  p.style.top=Math.max(4,Math.min(y+6,vh-hh-8))+'px';
}
function popEdit(){var e=S.popEvent;hidePop();if(e)openEdit(e)}
function popDelete(){var e=S.popEvent;hidePop();if(e)deleteEvent(e)}
function popInfo(){var e=S.popEvent;if(!e)return;var p=document.getElementById('pop');var x=p?parseInt(p.style.left,10):100,y=p?parseInt(p.style.top,10):100;showPop(e,x-6,y-6,false)}

// ---- editing a booking ----
function origOf(e){return {title:e.title,date:e.start.substr(0,10),time:e.time}}
function saveEvent(e,fields){vscode.postMessage({type:'editEvent',op:{kind:'update',idx:e.idx,orig:origOf(e),fields:fields}})}
function deleteEvent(e){vscode.postMessage({type:'editEvent',op:{kind:'delete',idx:e.idx,orig:origOf(e)}})}
function openEdit(e){
  var D={title:e.title,location:e.location||'',comment:e.description||'',date:e.start.substr(0,10),t1:hm(e.start),t2:hm(e.end),
    rec:false,mode:'week',skip:true,error:'',edit:e};
  recDefaults(D);
  S.form=D;S.preview=null;S.dialog=true;S.fresh=true;
  if(D.date<range().from||D.date>range().to){goto(D.date)}else render();
  var t=document.getElementById('f_title');if(t&&t.focus)t.focus();
}
function doSave(){
  var D=readForm();D.error='';
  if(!D.title.trim())D.error='Skriv en titel.';
  else if(!D.date)D.error='Välj ett datum.';
  else if(!D.t1||!D.t2||D.t2<=D.t1)D.error='Sluttiden måste vara efter starttiden.';
  if(D.error){render();return}
  saveEvent(D.edit,{title:D.title.trim(),date:D.date,time:D.t1+'-'+D.t2,location:D.location.trim(),description:D.comment.trim()});
}
function doDeleteEdit(){deleteEvent(S.form.edit)}

window.addEventListener('message',function(ev){
  var m=ev.data;
  if(m.type==='init'){S.sources=m.sources;render()}
  else if(m.type==='events'){
    if(m.tag==='preview'){buildPreview(m.events)}
    else{var r=range();if(m.from===r.from&&m.to===r.to){S.extending=false;S.events=m.events;S.byId={};m.events.forEach(function(e){S.byId[e.id]=e});S.loading=false;render()}}
  }else if(m.type==='created'){hidePop();S.dialog=false;S.preview=null;S.form=null;load()}
  else if(m.type==='reload'){hidePop();load()}
});
vscode.postMessage({type:'ready'});
load();
`;

function wrapHtml(): string {
  return `<!DOCTYPE html>
<html lang="sv"><head><meta charset="UTF-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline';">
<style>${CSS}</style></head>
<body><div id="app" style="display:flex;flex-direction:column;height:100vh"><div class="msg">Hämtar kalendrar…</div></div>
<script>${SCRIPT}</script></body></html>`;
}
