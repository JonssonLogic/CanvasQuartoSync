import type { Comment } from '../types/comments';

// ─── Constants ───────────────────────────────────────────────────────

const BLOCK_START = '<!-- === MDVIEWER COMMENTS ===';
const BLOCK_END = '=== END MDVIEWER COMMENTS === -->';

const INSTRUCTION_HEADER = `Review comments on this document. Each comment targets a specific text passage
identified by its section heading path, paragraph number, and exact text match.
To address a comment: locate the target text in the indicated section and paragraph,
apply the suggested change, then remove that comment entry from this block.
When multiple comments share the same section and paragraph, consider them together
as changes for one comment may affect the text referenced by another.
Delete this entire block once all comments are resolved.`;

// ─── Section Map ─────────────────────────────────────────────────────

export interface SectionInfo {
  /** Full heading path, e.g. "## Methods > ### Data Collection" */
  path: string;
  /** The heading text including ## markers */
  heading: string;
  /** Heading level (1-6) */
  level: number;
  /** Start offset in content (start of heading line) */
  start: number;
  /** End offset in content (start of next same/higher-level heading, or end of doc) */
  end: number;
  /** Paragraphs within this section (split by blank lines), each with start/end offsets */
  paragraphs: { text: string; start: number; end: number }[];
}

/**
 * Build a map of all sections in the document.
 * Each section spans from its heading to the next heading of same or higher level.
 */
export function buildSectionMap(content: string): SectionInfo[] {
  const headingRegex = /^(#{1,6})\s+(.+)$/gm;
  const headings: { level: number; text: string; fullLine: string; offset: number }[] = [];

  let match;
  while ((match = headingRegex.exec(content)) !== null) {
    headings.push({
      level: match[1].length,
      text: match[2].trim(),
      fullLine: match[0],
      offset: match.index,
    });
  }

  // Build parent chain for heading paths
  const sections: SectionInfo[] = [];
  const parentStack: { level: number; text: string }[] = [];

  for (let i = 0; i < headings.length; i++) {
    const h = headings[i];

    // Maintain parent stack — pop headings of same or deeper level
    while (parentStack.length > 0 && parentStack[parentStack.length - 1].level >= h.level) {
      parentStack.pop();
    }

    const pathParts = [...parentStack.map(p => p.text), `${'#'.repeat(h.level)} ${h.text}`];
    const path = pathParts.join(' > ');

    parentStack.push({ level: h.level, text: `${'#'.repeat(h.level)} ${h.text}` });

    // Content of this section starts after the heading line
    const headingLineEnd = content.indexOf('\n', h.offset);
    const contentStart = headingLineEnd === -1 ? content.length : headingLineEnd + 1;

    // Find where this section ends — next heading of same or higher level
    let sectionEnd = content.length;
    for (let j = i + 1; j < headings.length; j++) {
      if (headings[j].level <= h.level) {
        sectionEnd = headings[j].offset;
        break;
      }
    }

    const sectionContent = content.slice(contentStart, sectionEnd);
    const paragraphs = splitIntoParagraphs(sectionContent, contentStart);

    sections.push({
      path,
      heading: h.fullLine,
      level: h.level,
      start: h.offset,
      end: sectionEnd,
      paragraphs,
    });
  }

  // Handle preamble (content before any heading)
  if (headings.length === 0 || headings[0].offset > 0) {
    const preambleEnd = headings.length > 0 ? headings[0].offset : content.length;
    const preambleContent = content.slice(0, preambleEnd);
    const paragraphs = splitIntoParagraphs(preambleContent, 0);
    if (paragraphs.length > 0 && paragraphs.some(p => p.text.trim())) {
      sections.unshift({
        path: '(preamble)',
        heading: '',
        level: 0,
        start: 0,
        end: preambleEnd,
        paragraphs,
      });
    }
  }

  return sections;
}

function splitIntoParagraphs(
  text: string,
  baseOffset: number
): { text: string; start: number; end: number }[] {
  const paragraphs: { text: string; start: number; end: number }[] = [];
  // Split on one or more blank lines
  const parts = text.split(/\n\s*\n/);
  let pos = 0;

  for (const part of parts) {
    const trimmed = part.trim();
    if (!trimmed) {
      pos += part.length + 1; // +1 for the newline we split on
      continue;
    }
    // Find actual position in original text
    const idx = text.indexOf(part, pos);
    if (idx !== -1) {
      paragraphs.push({
        text: trimmed,
        start: baseOffset + idx,
        end: baseOffset + idx + part.length,
      });
      pos = idx + part.length;
    }
  }

  return paragraphs;
}

// ─── Extract ─────────────────────────────────────────────────────────

/**
 * Extract the MDVIEWER COMMENTS block from raw file content.
 * Returns clean content (block removed) and parsed comments.
 */
export function extractComments(rawContent: string): {
  cleanContent: string;
  comments: Comment[];
} {
  const blockStartIdx = rawContent.lastIndexOf(BLOCK_START);
  if (blockStartIdx === -1) {
    return { cleanContent: rawContent, comments: [] };
  }

  const blockEndIdx = rawContent.indexOf(BLOCK_END, blockStartIdx);
  if (blockEndIdx === -1) {
    return { cleanContent: rawContent, comments: [] };
  }

  const blockContent = rawContent.slice(
    blockStartIdx + BLOCK_START.length,
    blockEndIdx
  );

  // Remove the block (and any preceding blank line) from content
  let cleanEnd = blockStartIdx;
  // Trim trailing whitespace/newlines before the block
  while (cleanEnd > 0 && (rawContent[cleanEnd - 1] === '\n' || rawContent[cleanEnd - 1] === '\r')) {
    cleanEnd--;
  }
  const cleanContent = rawContent.slice(0, cleanEnd) +
    rawContent.slice(blockEndIdx + BLOCK_END.length);

  const comments = parseCommentEntries(blockContent);
  return { cleanContent: cleanContent.trimEnd() + (cleanContent.trimEnd().length > 0 ? '\n' : ''), comments };
}

function parseCommentEntries(blockContent: string): Comment[] {
  const comments: Comment[] = [];
  // Match [comment:ID] ... until next [comment:] or end
  const entryRegex = /\[comment:([a-f0-9]+)\]\s*(.*)/g;
  const entries: { id: string; headerLine: string; startIdx: number }[] = [];

  let match;
  while ((match = entryRegex.exec(blockContent)) !== null) {
    entries.push({
      id: match[1],
      headerLine: match[2],
      startIdx: match.index + match[0].length,
    });
  }

  for (let i = 0; i < entries.length; i++) {
    const entry = entries[i];
    // Find where this entry's body ends (start of next [comment:...] line, or end of block)
    const nextEntryStart = i + 1 < entries.length
      ? blockContent.lastIndexOf('\n', entries[i + 1].startIdx - entries[i + 1].headerLine.length - entry.id.length - 12)
      : blockContent.length;

    const rawBody = blockContent.slice(entry.startIdx, nextEntryStart);

    // Parse header line attributes
    const header = entry.headerLine;
    const section = extractQuoted(header, 'section:') ?? '(preamble)';
    const paragraph = extractInt(header, 'paragraph:') ?? 1;
    const paraStart = extractQuoted(header, 'paraStart:') ?? undefined;
    const target = extractQuoted(header, 'target:') ?? '';
    const paraOffset = extractInt(header, 'paraOffset:') ?? undefined;
    const context = extractQuoted(header, 'context:') ?? '';

    // Parse context into before/after using {t} marker
    let contextBefore = '';
    let contextAfter = '';
    const tIdx = context.indexOf('{t}');
    if (tIdx !== -1) {
      contextBefore = context.slice(0, tIdx);
      contextAfter = context.slice(tIdx + 3);
    }

    // Parse body lines — everything indented, except the date line
    const bodyLines = rawBody.split('\n');
    const contentLines: string[] = [];
    let dateStr = '';

    for (const line of bodyLines) {
      const trimmed = line.trim();
      if (!trimmed) continue;
      const dateMatch = trimmed.match(/^\((\d{4}-\d{2}-\d{2})\)$/);
      if (dateMatch) {
        dateStr = dateMatch[1];
      } else {
        contentLines.push(trimmed.replace(/--\\>/g, '-->'));
      }
    }

    comments.push({
      id: entry.id,
      section,
      paragraph,
      ...(paraStart !== undefined && { paraStart }),
      targetText: target,
      ...(paraOffset !== undefined && { targetOffsetInPara: paraOffset }),
      contextBefore,
      contextAfter,
      body: contentLines.join('\n'),
      createdAt: dateStr || new Date().toISOString().slice(0, 10),
      updatedAt: dateStr || new Date().toISOString().slice(0, 10),
    });
  }

  return comments;
}

function extractQuoted(str: string, prefix: string): string | null {
  const idx = str.indexOf(prefix);
  if (idx === -1) return null;
  const start = str.indexOf('"', idx + prefix.length);
  if (start === -1) return null;
  // Find closing quote (handle escaped quotes)
  let end = start + 1;
  while (end < str.length) {
    if (str[end] === '\\' && end + 1 < str.length) {
      end += 2; // skip escaped char
      continue;
    }
    if (str[end] === '"') break;
    end++;
  }
  return str.slice(start + 1, end).replace(/\\(["\\nr>])/g, (_, ch) =>
    ch === 'n' ? '\n' : ch === 'r' ? '\r' : ch
  );
}

function extractInt(str: string, prefix: string): number | null {
  const idx = str.indexOf(prefix);
  if (idx === -1) return null;
  const rest = str.slice(idx + prefix.length);
  const match = rest.match(/^(\d+)/);
  return match ? parseInt(match[1], 10) : null;
}

// ─── Serialize ───────────────────────────────────────────────────────

/**
 * Serialize comments back into the file content.
 * Strips any existing comment block, then appends a new one if comments exist.
 */
export function serializeComments(
  originalRawContent: string,
  comments: Comment[]
): string {
  // Strip existing block
  const { cleanContent } = extractComments(originalRawContent);
  const base = cleanContent.trimEnd();

  if (comments.length === 0) {
    return base + '\n';
  }

  const lines: string[] = [];
  lines.push('');
  lines.push(BLOCK_START);
  lines.push(INSTRUCTION_HEADER);

  for (const c of comments) {
    lines.push('');
    const context = `${escapeQuoted(c.contextBefore)}{t}${escapeQuoted(c.contextAfter)}`;
    const paraStartPart = c.paraStart ? ` paraStart:"${escapeQuoted(c.paraStart)}"` : '';
    const paraOffsetPart = c.targetOffsetInPara !== undefined ? ` paraOffset:${c.targetOffsetInPara}` : '';
    const header = `[comment:${c.id}] section:"${escapeQuoted(c.section)}" paragraph:${c.paragraph}${paraStartPart} target:"${escapeQuoted(c.targetText)}"${paraOffsetPart} context:"${context}"`;
    lines.push(header);
    // Indent body lines
    for (const bodyLine of c.body.split('\n')) {
      lines.push(`  ${bodyLine.replace(/-->/g, '--\\>')}`);
    }
    lines.push(`  (${c.updatedAt})`);
  }

  lines.push(BLOCK_END);

  return base + '\n' + lines.join('\n') + '\n';
}

/**
 * Escape a value for a quoted header field. Newlines are escaped so the header
 * stays on one line (context often spans lines), and `-->` is broken up so it
 * can't close the surrounding HTML comment early and leak the block into the page.
 */
function escapeQuoted(s: string): string {
  return s
    .replace(/\\/g, '\\\\')
    .replace(/"/g, '\\"')
    .replace(/\r/g, '\\r')
    .replace(/\n/g, '\\n')
    .replace(/-->/g, '--\\>');
}

// ─── Anchor ──────────────────────────────────────────────────────────

/**
 * Resolve comment positions in the clean content using the layered fallback strategy.
 * Sets `_offset` on each comment, or marks it as `orphaned`.
 */
export function anchorComments(
  cleanContent: string,
  comments: Comment[]
): Comment[] {
  const sections = buildSectionMap(cleanContent);

  return comments.map((comment) => {
    const resolved = {
      ...comment,
      orphaned: false,
      _offset: undefined as number | undefined,
      _end: undefined as number | undefined,
    };
    const exact = (offset: number) => {
      resolved._offset = offset;
      resolved._end = offset + comment.targetText.length;
      return resolved;
    };

    // Layer 1: Full path — section → paragraph (by index) → target
    const section = sections.find((s) => s.path === comment.section);
    if (section) {
      const para = section.paragraphs[comment.paragraph - 1];
      if (para) {
        const absIdx = findTargetInPara(cleanContent, para, comment.targetText, comment.targetOffsetInPara);
        if (absIdx !== -1) return exact(absIdx);
      }

      // Layer 1.5: Paragraph fingerprint (paraStart) — handles shifted paragraph indices
      if (comment.paraStart) {
        const needle = comment.paraStart.slice(0, 30);
        const fingerprintPara = section.paragraphs.find((p) =>
          p.text.replace(/\s+/g, ' ').trim().startsWith(needle)
        );
        if (fingerprintPara) {
          const absIdx = findTargetInPara(cleanContent, fingerprintPara, comment.targetText, comment.targetOffsetInPara);
          if (absIdx !== -1) return exact(absIdx);
        }
      }

      // Layer 2: Section + target (paragraph index may be wrong)
      const sectionContent = cleanContent.slice(section.start, section.end);
      const targetIdx = sectionContent.indexOf(comment.targetText);
      if (targetIdx !== -1) return exact(section.start + targetIdx);
    }

    // Layer 3: Global target + context
    const globalMatches = findAllOccurrences(cleanContent, comment.targetText);
    if (globalMatches.length === 1) return exact(globalMatches[0]);
    if (globalMatches.length > 1) {
      // Use context to disambiguate
      const contextStr = comment.contextBefore + comment.targetText + comment.contextAfter;
      for (const offset of globalMatches) {
        const start = Math.max(0, offset - comment.contextBefore.length - 10);
        const end = Math.min(cleanContent.length, offset + comment.targetText.length + comment.contextAfter.length + 10);
        const window = cleanContent.slice(start, end);
        if (window.includes(contextStr) || window.includes(comment.contextBefore.slice(-30) + comment.targetText)) {
          return exact(offset);
        }
      }
      // Fallback: take first match
      return exact(globalMatches[0]);
    }

    // Layer 3.25: Target saved as rendered text, e.g. "Across the layers," for
    // `**Across the layers**,` or a selection spanning table cells. Match it
    // against the source with markdown syntax projected out.
    const para = section?.paragraphs[comment.paragraph - 1];
    const approx = para ? para.start + (comment.targetOffsetInPara ?? 0) : section?.start ?? -1;
    const span = findRenderedTextInSource(comment.targetText, cleanContent, approx);
    if (span) {
      resolved._offset = span.start;
      resolved._end = span.end;
      return resolved;
    }

    // Layer 3.5: Normalized fallback — strip markdown delimiters and collapse whitespace,
    // then retry the global search. Catches cases where the source was lightly reformatted
    // (e.g. **word** → word, $expr$ → expr, or extra whitespace changes).
    const normalizedTarget = normalizeForSearch(comment.targetText);
    if (normalizedTarget && normalizedTarget !== comment.targetText) {
      const normalizedContent = normalizeForSearch(cleanContent);
      const normMatches = findAllOccurrences(normalizedContent, normalizedTarget);
      if (normMatches.length >= 1) {
        // Map the normalized offset back to the original content offset.
        // The normalized content may be shorter, so we do a forward scan to find the
        // corresponding position in the original.
        const normOffset = normMatches[0];
        resolved._offset = mapNormalizedOffset(cleanContent, normalizedContent, normOffset);
        return resolved;
      }
    }

    // Layer 4: Not found — orphaned
    resolved.orphaned = true;
    return resolved;
  });
}

/**
 * Strip markdown delimiters ($, *, _, `, ~) and collapse whitespace.
 * Used for fuzzy fallback matching when exact search fails.
 */
function normalizeForSearch(text: string): string {
  return text
    .replace(/[$*_`~]/g, '')
    .replace(/\s+/g, ' ')
    .trim();
}

/**
 * Given a character offset in a normalized string, find the corresponding
 * offset in the original string by scanning both in parallel.
 */
function mapNormalizedOffset(original: string, normalized: string, normOffset: number): number {
  let o = 0; // original index
  let n = 0; // normalized index
  const stripped = /[$*_`~\s]/;
  while (o < original.length && n < normOffset) {
    const ch = original[o];
    if (!stripped.test(ch)) {
      n++;
    } else if (ch === ' ' || ch === '\t' || ch === '\n' || ch === '\r') {
      // Collapsed whitespace: skip all consecutive whitespace in original,
      // but only advance n once (for the single space it maps to).
      if (n < normOffset && normalized[n] === ' ') n++;
      while (o + 1 < original.length && /\s/.test(original[o + 1])) o++;
    }
    o++;
  }
  return Math.min(o, original.length);
}

/**
 * Find the best absolute offset of `targetText` within `para`, optionally using
 * `targetOffsetInPara` to pick the closest occurrence when multiple exist.
 * Returns -1 if not found.
 */
function findTargetInPara(
  cleanContent: string,
  para: { text: string; start: number; end: number },
  targetText: string,
  targetOffsetInPara?: number
): number {
  if (!targetText) return -1;
  const occurrences: number[] = [];
  let pos = 0;
  while (true) {
    const i = para.text.indexOf(targetText, pos);
    if (i === -1) break;
    occurrences.push(i);
    pos = i + 1;
  }
  if (occurrences.length === 0) return -1;

  let bestParaOffset: number;
  if (occurrences.length === 1 || targetOffsetInPara === undefined) {
    bestParaOffset = occurrences[0];
  } else {
    // Pick the occurrence whose index in the paragraph is closest to targetOffsetInPara
    bestParaOffset = occurrences.reduce((best, cur) =>
      Math.abs(cur - targetOffsetInPara) < Math.abs(best - targetOffsetInPara) ? cur : best
    );
  }

  return cleanContent.indexOf(targetText, para.start + bestParaOffset);
}

function findAllOccurrences(text: string, search: string): number[] {
  if (!search) return [];
  const results: number[] = [];
  let pos = 0;
  while (true) {
    const idx = text.indexOf(search, pos);
    if (idx === -1) break;
    results.push(idx);
    pos = idx + 1;
  }
  return results;
}

// ─── Source Projection ───────────────────────────────────────────────
//
// A selection in the rendered view rarely matches the markdown source
// verbatim: `**Across the layers**,` renders as "Across the layers,", and a
// selection across table cells skips `](url) |`. The projection is the source
// with markdown syntax removed and whitespace collapsed, keeping for every
// projected character its offset in the source. Rendered text is matched
// against the projection, and highlights are injected only around the
// visible runs of a source span so no <mark> ever straddles syntax.

const SYNTAX = 0;   // not rendered as text: `**`, `[`, `](url)`, tags, attrs
const VISIBLE = 1;  // rendered as text
const SEPARATOR = 2; // not rendered, but separates words: `|`, list markers, fence lines

export interface SourceProjection {
  /** Source with syntax removed and whitespace collapsed to single spaces */
  text: string;
  /** text[i] comes from content[map[i]] */
  map: number[];
  kind: Uint8Array;
  /** 1 where a <mark> can't be injected (inside code blocks, display math) */
  noInject: Uint8Array;
  /** For characters of an atomic inline (code span, inline math): the atom's [start, end) */
  atomStart: Int32Array;
  atomEnd: Int32Array;
}

let projectionCache: { content: string; projection: SourceProjection } | null = null;

export function buildSourceProjection(content: string): SourceProjection {
  if (projectionCache?.content === content) return projectionCache.projection;

  const n = content.length;
  const kind = new Uint8Array(n).fill(VISIBLE);
  const noInject = new Uint8Array(n);
  const atomStart = new Int32Array(n).fill(-1);
  const atomEnd = new Int32Array(n).fill(-1);
  // Classified by an earlier pass; later passes leave these alone
  const locked = new Uint8Array(n);

  const set = (s: number, e: number, k: number) => {
    for (let i = s; i < e; i++) if (!locked[i]) kind[i] = k;
  };
  const lock = (s: number, e: number) => locked.fill(1, s, e);
  const each = (re: RegExp, fn: (m: RegExpExecArray, s: number, e: number) => void) => {
    let m;
    while ((m = re.exec(content)) !== null) {
      if (m[0].length === 0) { re.lastIndex++; continue; }
      if (locked[m.index]) continue;
      fn(m, m.index, m.index + m[0].length);
    }
  };

  // Never rendered: front matter, HTML comments, shortcodes
  each(/^---\r?\n[\s\S]*?\r?\n---[ \t]*(?=\r?\n|$)/g, (_, s, e) => { set(s, e, SEPARATOR); lock(s, e); });
  each(/<!--[\s\S]*?-->/g, (_, s, e) => { set(s, e, SEPARATOR); lock(s, e); });
  each(/\{\{<[\s\S]*?>\}\}/g, (_, s, e) => { set(s, e, SEPARATOR); lock(s, e); });

  // Fenced code: executable chunks are stripped from the preview; other code
  // is shown but can't hold a <mark> (it would render as literal text)
  each(/^([ \t]*)(`{3,}|~{3,})([^\n]*)\n([\s\S]*?)^[ \t]*\2[ \t]*$/gm, (m, s, e) => {
    if (m[3].trim().startsWith('{')) {
      set(s, e, SEPARATOR);
    } else {
      const bodyStart = s + m[1].length + m[2].length + m[3].length + 1;
      const bodyEnd = bodyStart + m[4].length;
      set(s, bodyStart, SEPARATOR);
      set(bodyEnd, e, SEPARATOR);
      noInject.fill(1, bodyStart, bodyEnd);
    }
    lock(s, e);
  });

  // Code spans: backticks are syntax, the span is highlighted as a whole
  each(/(`+)[^`\n]+?\1/g, (m, s, e) => {
    set(s, s + m[1].length, SYNTAX);
    set(e - m[1].length, e, SYNTAX);
    atomStart.fill(s, s, e);
    atomEnd.fill(e, s, e);
    lock(s, e);
  });

  // Math stays in its $…$ source form (selections are converted back to LaTeX)
  each(/\$\$[\s\S]+?\$\$/g, (_, s, e) => { noInject.fill(1, s, e); lock(s, e); });
  // Pandoc rules: no space after the opening $, none before the closing $, no digit after it
  each(/(?<![\\$])\$(?![$\s])(?:[^$\n]*?[^$\s\\])?\$(?![$\d])/g, (_, s, e) => {
    atomStart.fill(s, s, e);
    atomEnd.fill(e, s, e);
    lock(s, e);
  });

  // Block syntax at line starts
  each(/^[ \t]*:::.*$/gm, (_, s, e) => set(s, e, SEPARATOR));
  each(/^[ \t]*\|?[ \t]*:?-+:?[ \t]*(?:\|[ \t]*:?-+:?[ \t]*)+\|?[ \t]*$/gm, (_, s, e) => set(s, e, SEPARATOR));
  each(/^:[ \t]+\{[^}\n]*\}[ \t]*$/gm, (_, s, e) => set(s, e, SEPARATOR));
  each(/^(?:[ \t]*>[ \t]?)*[ \t]*(?:#{1,6}[ \t]+|[-*+][ \t]+|\d+[.)][ \t]+|:[ \t]+)?/gm, (_, s, e) => set(s, e, SEPARATOR));

  // Table cell separators
  each(/^[ \t]*\|.*$/gm, (_, s, e) => {
    for (let i = s; i < e; i++) {
      if (content[i] === '|' && content[i - 1] !== '\\') set(i, i + 1, SEPARATOR);
    }
  });

  // Links and images: `[`/`![` and `](url){attrs}` are syntax, the text is shown
  each(/(!?\[)((?:[^[\]\n]|\[[^\]\n]*\])*)(\]\([^)\n]*\)(?:\{[^}\n]*\})?)/g, (m, s, e) => {
    set(s, s + m[1].length, SYNTAX);
    set(s + m[1].length + m[2].length, e, SYNTAX);
  });

  // Inline HTML tags and Pandoc attribute blocks
  each(/<\/?[a-zA-Z][^<>\n]*>/g, (_, s, e) => set(s, e, SYNTAX));
  each(/\{[#.][^}\n]*\}|(?<=[\])])\{[^}\n]*\}/g, (_, s, e) => set(s, e, SYNTAX));

  // Backslash escapes: the backslash is syntax, the escaped character is text
  each(/\\[!-/:-@[-`{-~]/g, (_, s) => { set(s, s + 1, SYNTAX); lock(s + 1, s + 2); });

  // Emphasis and strikethrough; `_` only at word boundaries (snake_case is text)
  each(/\*+|~~/g, (_, s, e) => set(s, e, SYNTAX));
  each(/_+/g, (_, s, e) => {
    const wordChar = /[\p{L}\p{N}]/u;
    if (!wordChar.test(content[s - 1] ?? ' ') || !wordChar.test(content[e] ?? ' ')) set(s, e, SYNTAX);
  });

  const chars: string[] = [];
  const map: number[] = [];
  let pendingSpace = false;
  for (let i = 0; i < n; i++) {
    const k = kind[i];
    if (k === SYNTAX) continue;
    if (k === SEPARATOR || /\s/.test(content[i])) {
      if (chars.length) pendingSpace = true;
      continue;
    }
    if (pendingSpace) {
      chars.push(' ');
      map.push(i);
      pendingSpace = false;
    }
    chars.push(content[i]);
    map.push(i);
  }

  const projection = { text: chars.join(''), map, kind, noInject, atomStart, atomEnd };
  projectionCache = { content, projection };
  return projection;
}

/**
 * Find rendered text (as selected in the preview) in the markdown source.
 * Returns the source span [start, end), widened to whole code spans / inline
 * math, using the occurrence nearest `approxOffset` when there are several.
 */
export function findRenderedTextInSource(
  text: string,
  content: string,
  approxOffset = -1
): { start: number; end: number } | null {
  const needle = text.replace(/\s+/g, ' ').trim();
  if (!needle) return null;
  const p = buildSourceProjection(content);

  let best: { start: number; end: number } | null = null;
  let bestDist = Infinity;
  let pos = 0;
  while (true) {
    const idx = p.text.indexOf(needle, pos);
    if (idx === -1) break;
    let start = p.map[idx];
    let end = p.map[idx + needle.length - 1] + 1;
    if (p.atomStart[start] !== -1) start = p.atomStart[start];
    if (p.atomEnd[end - 1] !== -1) end = p.atomEnd[end - 1];
    const dist = approxOffset < 0 ? 0 : Math.abs(start - approxOffset);
    if (dist < bestDist) {
      best = { start, end };
      bestDist = dist;
      if (approxOffset < 0) break;
    }
    pos = idx + 1;
  }
  return best;
}

/** The visible runs of a source span, each safe to wrap in its own <mark>. */
function highlightSegments(
  p: SourceProjection,
  content: string,
  start: number,
  end: number
): [number, number][] {
  const segments: [number, number][] = [];
  let segStart = -1;
  let segEnd = -1;
  const flush = () => {
    if (segStart !== -1) {
      while (segStart < segEnd && /\s/.test(content[segStart])) segStart++;
      while (segEnd > segStart && /\s/.test(content[segEnd - 1])) segEnd--;
      if (segEnd > segStart) segments.push([segStart, segEnd]);
    }
    segStart = -1;
  };

  let i = start;
  while (i < end) {
    if (p.atomStart[i] !== -1) {
      // Code span / inline math: take it whole
      if (segStart === -1) segStart = p.atomStart[i];
      segEnd = p.atomEnd[i];
      i = segEnd;
      continue;
    }
    const ch = content[i];
    if (p.kind[i] !== VISIBLE || p.noInject[i] || ch === '\n' || ch === '\r') {
      flush();
    } else {
      if (segStart === -1) segStart = i;
      segEnd = i + 1;
    }
    i++;
  }
  flush();
  return segments;
}

// ─── Highlight Injection ─────────────────────────────────────────────

/**
 * Inject <mark> tags into clean markdown content for each anchored comment.
 * Comments must have been processed by `anchorComments` first. A comment whose
 * target spans markdown syntax gets one <mark> per visible run; the last one
 * carries `data-comment-last` for the indicator dot.
 */
export function injectCommentHighlights(
  cleanContent: string,
  comments: Comment[]
): string {
  const p = buildSourceProjection(cleanContent);
  const anchored = comments
    .filter((c) => !c.orphaned && c._offset !== undefined)
    .sort((a, b) => a._offset! - b._offset!);

  const inserts: { pos: number; close: boolean; text: string }[] = [];
  let taken = 0; // end of the last highlighted span — overlapping marks would nest wrongly

  for (const c of anchored) {
    const start = c._offset!;
    let end = c._end;
    if (end === undefined) {
      // Offset without a known span (normalized fallback): only highlight an exact match
      if (cleanContent.slice(start, start + c.targetText.length) !== c.targetText) continue;
      end = start + c.targetText.length;
    }
    if (end > cleanContent.length || end <= start) continue;

    const segments = highlightSegments(p, cleanContent, start, end);
    if (segments.length === 0 || segments[0][0] < taken) continue;
    taken = segments[segments.length - 1][1];

    segments.forEach(([s, e], i) => {
      const last = i === segments.length - 1 ? ' data-comment-last="true"' : '';
      inserts.push({ pos: s, close: false, text: `<mark class="comment-highlight" data-comment-id="${c.id}"${last}>` });
      inserts.push({ pos: e, close: true, text: '</mark>' });
    });
  }

  // At a shared position, close the previous mark before opening the next
  inserts.sort((a, b) => a.pos - b.pos || Number(b.close) - Number(a.close));

  let result = '';
  let prev = 0;
  for (const ins of inserts) {
    result += cleanContent.slice(prev, ins.pos) + ins.text;
    prev = ins.pos;
  }
  result += cleanContent.slice(prev);

  return result;
}

// ─── Utilities ───────────────────────────────────────────────────────

/** Generate an 8-character hex ID */
export function generateCommentId(): string {
  const arr = new Uint8Array(4);
  crypto.getRandomValues(arr);
  return Array.from(arr, (b) => b.toString(16).padStart(2, '0')).join('');
}

/**
 * Given a character offset in clean content, determine the section path, paragraph index,
 * paragraph start fingerprint, and offset of the target within the paragraph.
 */
export function locatePosition(
  cleanContent: string,
  offset: number
): { section: string; paragraph: number; paraStart: string; targetOffsetInPara: number } {
  const sections = buildSectionMap(cleanContent);

  // Find the most specific (deepest) section containing this offset
  let bestSection: SectionInfo | null = null;
  for (const s of sections) {
    if (offset >= s.start && offset < s.end) {
      if (!bestSection || s.level > bestSection.level) {
        bestSection = s;
      }
    }
  }

  if (!bestSection) {
    return { section: '(preamble)', paragraph: 1, paraStart: '', targetOffsetInPara: 0 };
  }

  // Find which paragraph
  let paragraphIdx = 1;
  let foundPara: { text: string; start: number; end: number } | null = null;
  for (let i = 0; i < bestSection.paragraphs.length; i++) {
    const p = bestSection.paragraphs[i];
    if (offset >= p.start && offset < p.end + 1) {
      paragraphIdx = i + 1;
      foundPara = p;
      break;
    }
    if (offset < p.start) {
      paragraphIdx = Math.max(1, i);
      foundPara = bestSection.paragraphs[paragraphIdx - 1] ?? null;
      break;
    }
    paragraphIdx = i + 1;
    foundPara = p;
  }

  const paraStart = foundPara
    ? foundPara.text.slice(0, 40).replace(/\s+/g, ' ').trim()
    : '';
  const targetOffsetInPara = foundPara ? Math.max(0, offset - foundPara.start) : 0;

  return { section: bestSection.path, paragraph: paragraphIdx, paraStart, targetOffsetInPara };
}

/**
 * Extract context around a target text at a given offset.
 * Returns ~200 chars (or sentence boundary) before and after.
 */
export function extractContext(
  cleanContent: string,
  offset: number,
  targetLength: number
): { contextBefore: string; contextAfter: string } {
  // Look for sentence boundaries or take ~200 chars
  const CONTEXT_LEN = 200;

  let beforeStart = Math.max(0, offset - CONTEXT_LEN);
  const beforeText = cleanContent.slice(beforeStart, offset);
  // Try to start at a sentence boundary
  const sentenceStart = beforeText.search(/[.!?]\s+[A-Z]/);
  if (sentenceStart !== -1 && sentenceStart < beforeText.length - 20) {
    beforeStart = beforeStart + sentenceStart + 1;
  }
  const contextBefore = cleanContent.slice(beforeStart, offset).trimStart();

  let afterEnd = Math.min(cleanContent.length, offset + targetLength + CONTEXT_LEN);
  const afterText = cleanContent.slice(offset + targetLength, afterEnd);
  // Try to end at a sentence boundary
  const sentenceEnd = afterText.search(/[.!?]\s/);
  if (sentenceEnd !== -1 && sentenceEnd > 10) {
    afterEnd = offset + targetLength + sentenceEnd + 1;
  }
  const contextAfter = cleanContent.slice(offset + targetLength, afterEnd).trimEnd();

  return { contextBefore, contextAfter };
}
