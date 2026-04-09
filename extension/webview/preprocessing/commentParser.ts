/**
 * Comment parser — ported from MDViewer.
 * Handles extracting, serializing, anchoring, and highlighting comments
 * stored in a structured HTML comment block at the end of .qmd files.
 */

export interface Comment {
  id: string;
  section: string;
  paragraph: number;
  targetText: string;
  contextBefore: string;
  contextAfter: string;
  body: string;
  createdAt: string;
  updatedAt: string;
  orphaned?: boolean;
  _offset?: number;
}

const BLOCK_START = '<!-- === MDVIEWER COMMENTS ===';
const BLOCK_END = '=== END MDVIEWER COMMENTS === -->';

const INSTRUCTION_HEADER = `Review comments on this document. Each comment targets a specific text passage
identified by its section heading path, paragraph number, and exact text match.
To address a comment: locate the target text in the indicated section and paragraph,
apply the suggested change, then remove that comment entry from this block.
Delete this entire block once all comments are resolved.`;

interface SectionInfo {
  path: string;
  level: number;
  start: number;
  end: number;
  paragraphs: { text: string; start: number; end: number }[];
}

function buildSectionMap(content: string): SectionInfo[] {
  const headingRegex = /^(#{1,6})\s+(.+)$/gm;
  const headings: { level: number; text: string; fullLine: string; offset: number }[] = [];
  let match;
  while ((match = headingRegex.exec(content)) !== null) {
    headings.push({ level: match[1].length, text: match[2].trim(), fullLine: match[0], offset: match.index });
  }

  const sections: SectionInfo[] = [];
  const parentStack: { level: number; text: string }[] = [];

  for (let i = 0; i < headings.length; i++) {
    const h = headings[i];
    while (parentStack.length > 0 && parentStack[parentStack.length - 1].level >= h.level) parentStack.pop();
    const pathParts = [...parentStack.map(p => p.text), `${'#'.repeat(h.level)} ${h.text}`];
    parentStack.push({ level: h.level, text: `${'#'.repeat(h.level)} ${h.text}` });

    const headingLineEnd = content.indexOf('\n', h.offset);
    const contentStart = headingLineEnd === -1 ? content.length : headingLineEnd + 1;
    let sectionEnd = content.length;
    for (let j = i + 1; j < headings.length; j++) {
      if (headings[j].level <= h.level) { sectionEnd = headings[j].offset; break; }
    }

    const sectionContent = content.slice(contentStart, sectionEnd);
    const paragraphs = splitIntoParagraphs(sectionContent, contentStart);
    sections.push({ path: pathParts.join(' > '), level: h.level, start: h.offset, end: sectionEnd, paragraphs });
  }

  if (headings.length === 0 || headings[0].offset > 0) {
    const end = headings.length > 0 ? headings[0].offset : content.length;
    const paras = splitIntoParagraphs(content.slice(0, end), 0);
    if (paras.some(p => p.text.trim())) {
      sections.unshift({ path: '(preamble)', level: 0, start: 0, end: end, paragraphs: paras });
    }
  }

  return sections;
}

function splitIntoParagraphs(text: string, baseOffset: number) {
  const paragraphs: { text: string; start: number; end: number }[] = [];
  const parts = text.split(/\n\s*\n/);
  let pos = 0;
  for (const part of parts) {
    const trimmed = part.trim();
    if (!trimmed) { pos += part.length + 1; continue; }
    const idx = text.indexOf(part, pos);
    if (idx !== -1) {
      paragraphs.push({ text: trimmed, start: baseOffset + idx, end: baseOffset + idx + part.length });
      pos = idx + part.length;
    }
  }
  return paragraphs;
}

export function extractComments(rawContent: string): { cleanContent: string; comments: Comment[] } {
  const blockStartIdx = rawContent.lastIndexOf(BLOCK_START);
  if (blockStartIdx === -1) return { cleanContent: rawContent, comments: [] };
  const blockEndIdx = rawContent.indexOf(BLOCK_END, blockStartIdx);
  if (blockEndIdx === -1) return { cleanContent: rawContent, comments: [] };

  const blockContent = rawContent.slice(blockStartIdx + BLOCK_START.length, blockEndIdx);
  let cleanEnd = blockStartIdx;
  while (cleanEnd > 0 && (rawContent[cleanEnd - 1] === '\n' || rawContent[cleanEnd - 1] === '\r')) cleanEnd--;
  const cleanContent = rawContent.slice(0, cleanEnd) + rawContent.slice(blockEndIdx + BLOCK_END.length);

  return { cleanContent: cleanContent.trimEnd() + '\n', comments: parseCommentEntries(blockContent) };
}

function parseCommentEntries(blockContent: string): Comment[] {
  const comments: Comment[] = [];
  const entryRegex = /\[comment:([a-f0-9]+)\]\s*(.*)/g;
  const entries: { id: string; headerLine: string; startIdx: number }[] = [];
  let match;
  while ((match = entryRegex.exec(blockContent)) !== null) {
    entries.push({ id: match[1], headerLine: match[2], startIdx: match.index + match[0].length });
  }

  for (let i = 0; i < entries.length; i++) {
    const entry = entries[i];
    const nextStart = i + 1 < entries.length
      ? blockContent.lastIndexOf('\n', entries[i + 1].startIdx - entries[i + 1].headerLine.length - entry.id.length - 12)
      : blockContent.length;
    const rawBody = blockContent.slice(entry.startIdx, nextStart);
    const header = entry.headerLine;
    const section = extractQuoted(header, 'section:') ?? '(preamble)';
    const paragraph = extractInt(header, 'paragraph:') ?? 1;
    const target = extractQuoted(header, 'target:') ?? '';
    const context = extractQuoted(header, 'context:') ?? '';
    let contextBefore = '', contextAfter = '';
    const tIdx = context.indexOf('{t}');
    if (tIdx !== -1) { contextBefore = context.slice(0, tIdx); contextAfter = context.slice(tIdx + 3); }

    const bodyLines = rawBody.split('\n');
    const contentLines: string[] = [];
    let dateStr = '';
    for (const line of bodyLines) {
      const trimmed = line.trim();
      if (!trimmed) continue;
      const dateMatch = trimmed.match(/^\((\d{4}-\d{2}-\d{2})\)$/);
      if (dateMatch) dateStr = dateMatch[1];
      else contentLines.push(trimmed);
    }

    comments.push({ id: entry.id, section, paragraph, targetText: target, contextBefore, contextAfter,
      body: contentLines.join('\n'), createdAt: dateStr || new Date().toISOString().slice(0, 10),
      updatedAt: dateStr || new Date().toISOString().slice(0, 10) });
  }
  return comments;
}

function extractQuoted(str: string, prefix: string): string | null {
  const idx = str.indexOf(prefix);
  if (idx === -1) return null;
  const start = str.indexOf('"', idx + prefix.length);
  if (start === -1) return null;
  let end = start + 1;
  while (end < str.length) {
    if (str[end] === '\\' && end + 1 < str.length) { end += 2; continue; }
    if (str[end] === '"') break;
    end++;
  }
  return str.slice(start + 1, end).replace(/\\"/g, '"');
}

function extractInt(str: string, prefix: string): number | null {
  const idx = str.indexOf(prefix);
  if (idx === -1) return null;
  const m = str.slice(idx + prefix.length).match(/^(\d+)/);
  return m ? parseInt(m[1], 10) : null;
}

export function serializeComments(originalRawContent: string, comments: Comment[]): string {
  const { cleanContent } = extractComments(originalRawContent);
  const base = cleanContent.trimEnd();
  if (comments.length === 0) return base + '\n';

  const lines = ['', BLOCK_START, INSTRUCTION_HEADER];
  for (const c of comments) {
    lines.push('');
    const context = `${escapeQ(c.contextBefore)}{t}${escapeQ(c.contextAfter)}`;
    lines.push(`[comment:${c.id}] section:"${escapeQ(c.section)}" paragraph:${c.paragraph} target:"${escapeQ(c.targetText)}" context:"${context}"`);
    for (const bodyLine of c.body.split('\n')) lines.push(`  ${bodyLine}`);
    lines.push(`  (${c.updatedAt})`);
  }
  lines.push(BLOCK_END);
  return base + '\n' + lines.join('\n') + '\n';
}

function escapeQ(s: string): string { return s.replace(/\\/g, '\\\\').replace(/"/g, '\\"'); }

export function anchorComments(cleanContent: string, comments: Comment[]): Comment[] {
  const sections = buildSectionMap(cleanContent);
  return comments.map((comment) => {
    const resolved = { ...comment, orphaned: false, _offset: undefined as number | undefined };

    const section = sections.find(s => s.path === comment.section);
    if (section) {
      const para = section.paragraphs[comment.paragraph - 1];
      if (para) {
        const absIdx = cleanContent.indexOf(comment.targetText, para.start);
        if (absIdx !== -1) { resolved._offset = absIdx; return resolved; }
      }
      const sectionContent = cleanContent.slice(section.start, section.end);
      const targetIdx = sectionContent.indexOf(comment.targetText);
      if (targetIdx !== -1) { resolved._offset = section.start + targetIdx; return resolved; }
    }

    const globalMatches = findAll(cleanContent, comment.targetText);
    if (globalMatches.length === 1) { resolved._offset = globalMatches[0]; return resolved; }
    if (globalMatches.length > 1) {
      const ctx = comment.contextBefore + comment.targetText + comment.contextAfter;
      for (const offset of globalMatches) {
        const start = Math.max(0, offset - comment.contextBefore.length - 10);
        const end = Math.min(cleanContent.length, offset + comment.targetText.length + comment.contextAfter.length + 10);
        if (cleanContent.slice(start, end).includes(ctx)) { resolved._offset = offset; return resolved; }
      }
      resolved._offset = globalMatches[0];
      return resolved;
    }

    resolved.orphaned = true;
    return resolved;
  });
}

function findAll(text: string, search: string): number[] {
  if (!search) return [];
  const results: number[] = [];
  let pos = 0;
  while (true) { const idx = text.indexOf(search, pos); if (idx === -1) break; results.push(idx); pos = idx + 1; }
  return results;
}

export function injectCommentHighlights(cleanContent: string, comments: Comment[]): string {
  const anchored = comments.filter(c => !c.orphaned && c._offset !== undefined)
    .sort((a, b) => (b._offset ?? 0) - (a._offset ?? 0));
  let result = cleanContent;
  for (const c of anchored) {
    const offset = c._offset!;
    const end = offset + c.targetText.length;
    if (result.slice(offset, end) !== c.targetText) continue;
    result = result.slice(0, offset) +
      `<mark class="comment-highlight" data-comment-id="${c.id}">${c.targetText}</mark>` +
      result.slice(end);
  }
  return result;
}

export function generateCommentId(): string {
  const arr = new Uint8Array(4);
  crypto.getRandomValues(arr);
  return Array.from(arr, b => b.toString(16).padStart(2, '0')).join('');
}

export function locatePosition(cleanContent: string, offset: number): { section: string; paragraph: number } {
  const sections = buildSectionMap(cleanContent);
  let best: SectionInfo | null = null;
  for (const s of sections) {
    if (offset >= s.start && offset < s.end) {
      if (!best || s.level > best.level) best = s;
    }
  }
  if (!best) return { section: '(preamble)', paragraph: 1 };
  let paragraphIdx = 1;
  for (let i = 0; i < best.paragraphs.length; i++) {
    const p = best.paragraphs[i];
    if (offset >= p.start && offset < p.end + 1) { paragraphIdx = i + 1; break; }
    if (offset < p.start) { paragraphIdx = Math.max(1, i); break; }
    paragraphIdx = i + 1;
  }
  return { section: best.path, paragraph: paragraphIdx };
}

export function extractContext(cleanContent: string, offset: number, targetLength: number) {
  const LEN = 80;
  let beforeStart = Math.max(0, offset - LEN);
  const beforeText = cleanContent.slice(beforeStart, offset);
  const sentStart = beforeText.search(/[.!?]\s+[A-Z]/);
  if (sentStart !== -1 && sentStart < beforeText.length - 20) beforeStart += sentStart + 1;
  const contextBefore = cleanContent.slice(beforeStart, offset).trimStart();

  let afterEnd = Math.min(cleanContent.length, offset + targetLength + LEN);
  const afterText = cleanContent.slice(offset + targetLength, afterEnd);
  const sentEnd = afterText.search(/[.!?]\s/);
  if (sentEnd !== -1 && sentEnd > 10) afterEnd = offset + targetLength + sentEnd + 1;
  const contextAfter = cleanContent.slice(offset + targetLength, afterEnd).trimEnd();

  return { contextBefore, contextAfter };
}
