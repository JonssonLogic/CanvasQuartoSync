/**
 * Lightweight BibTeX parser — ported from MDViewer.
 * Parses .bib files and generates APA-style citations and bibliography.
 */

export interface BibEntry {
  type: string;
  key: string;
  fields: Record<string, string>;
}

export function parseBibtex(content: string): BibEntry[] {
  const entries: BibEntry[] = [];
  const entryRe = /@(\w+)\s*\{([^,]+),/g;
  let match;

  while ((match = entryRe.exec(content)) !== null) {
    const type = match[1].toLowerCase();
    const key = match[2].trim();
    if (type === 'string' || type === 'preamble' || type === 'comment') continue;

    let depth = 1;
    let pos = match.index + match[0].length;
    const start = pos;
    while (pos < content.length && depth > 0) {
      if (content[pos] === '{') depth++;
      else if (content[pos] === '}') depth--;
      pos++;
    }
    const body = content.slice(start, pos - 1);

    const fields: Record<string, string> = {};
    const fieldRe = /(\w[\w-]*)\s*=\s*(?:\{((?:[^{}]|\{[^{}]*\})*)\}|"([^"]*)"|(\d+))/g;
    let fm;
    while ((fm = fieldRe.exec(body)) !== null) {
      const fieldName = fm[1].toLowerCase();
      const value = (fm[2] ?? fm[3] ?? fm[4] ?? '').trim();
      fields[fieldName] = cleanLatex(value);
    }

    entries.push({ type, key, fields });
  }

  return entries;
}

function cleanLatex(text: string): string {
  return text
    .replace(/\{\\[a-zA-Z]+\s*/g, '')
    .replace(/\\[a-zA-Z]+\{([^}]*)\}/g, '$1')
    .replace(/[{}]/g, '')
    .replace(/~/g, '\u00A0')
    .replace(/\\&/g, '&')
    .replace(/\\\\/g, '')
    .replace(/--/g, '\u2013')
    .trim();
}

export function formatBibEntry(entry: BibEntry): string {
  const f = entry.fields;
  const parts: string[] = [];

  if (f.author) parts.push(formatAuthors(f.author));
  if (f.year) parts.push(`(${f.year}).`);
  if (f.title) parts.push(`<em>${f.title}</em>.`);

  if (f.journal) {
    let journalPart = f.journal;
    if (f.volume) journalPart += `, ${f.volume}`;
    if (f.number) journalPart += `(${f.number})`;
    if (f.pages) journalPart += `, ${f.pages}`;
    parts.push(`${journalPart}.`);
  } else if (f.booktitle) {
    parts.push(`In <em>${f.booktitle}</em>.`);
  }

  if (f.publisher) parts.push(`${f.publisher}.`);
  if (f.doi) parts.push(`<a href="https://doi.org/${f.doi}">doi:${f.doi}</a>`);
  else if (f.url) parts.push(`<a href="${f.url}">${f.url}</a>`);

  return parts.join(' ');
}

function formatAuthors(authors: string): string {
  return authors.split(/\s+and\s+/).map(a => a.trim()).filter(Boolean).join(', & ') + '.';
}

function shortAuthors(authors: string): string {
  const names = authors.split(/\s+and\s+/).map(a => {
    const trimmed = a.trim();
    if (trimmed.includes(',')) return trimmed.split(',')[0].trim();
    const parts = trimmed.split(/\s+/);
    return parts[parts.length - 1];
  }).filter(Boolean);

  if (names.length === 0) return '';
  if (names.length === 1) return names[0];
  if (names.length === 2) return `${names[0]} & ${names[1]}`;
  return `${names[0]} et al.`;
}

function apaCiteText(entry: BibEntry): string {
  const author = entry.fields.author ? shortAuthors(entry.fields.author) : entry.key;
  const year = entry.fields.year ?? '';
  return year ? `${author} (${year})` : author;
}

export function processCitations(
  content: string,
  entries: BibEntry[]
): { content: string; citedKeys: string[] } {
  const keyToEntry = new Map(entries.map(e => [e.key, e]));
  const citedKeys: string[] = [];
  const citedSet = new Set<string>();

  function trackKey(key: string): void {
    if (!citedSet.has(key) && keyToEntry.has(key)) {
      citedSet.add(key);
      citedKeys.push(key);
    }
  }

  const parts = content.split(/(<[^>]+>)/g);
  const processed = parts.map(part => {
    if (part.startsWith('<')) return part;

    let result = part.replace(
      /\[(-?@[\w:./-]+(?:\s*;\s*-?@[\w:./-]+)*)\]/g,
      (_match, inner: string) => {
        const keys = inner.split(/\s*;\s*/).map((k: string) => k.replace(/^-?@/, ''));
        const refs: string[] = [];
        for (const key of keys) {
          const entry = keyToEntry.get(key);
          if (!entry) { refs.push(`${key}?`); continue; }
          trackKey(key);
          refs.push(`<a href="#ref-${key}" class="citation-link">${apaCiteText(entry)}</a>`);
        }
        return `(${refs.join('; ')})`;
      }
    );

    result = result.replace(
      /(?<![[\w@])@([a-zA-Z][\w]*)/g,
      (_match, key: string) => {
        const entry = keyToEntry.get(key);
        if (!entry) return _match;
        trackKey(key);
        return `<a href="#ref-${key}" class="citation-link">${apaCiteText(entry)}</a>`;
      }
    );

    return result;
  }).join('');

  return { content: processed, citedKeys };
}

export function generateBibliography(citedKeys: string[], entries: BibEntry[]): string {
  if (citedKeys.length === 0) return '';

  const keyToEntry = new Map(entries.map(e => [e.key, e]));
  const sortedKeys = [...citedKeys].sort((a, b) => {
    const ea = keyToEntry.get(a), eb = keyToEntry.get(b);
    const authA = ea?.fields.author ?? a, authB = eb?.fields.author ?? b;
    return authA.localeCompare(authB);
  });

  const items = sortedKeys.map((key) => {
    const entry = keyToEntry.get(key);
    if (!entry) return '';
    return `<li id="ref-${key}" class="bib-entry"><span class="bib-content">${formatBibEntry(entry)}</span></li>`;
  }).filter(Boolean);

  return `\n\n<div class="bibliography">\n<h2>References</h2>\n<ol class="bib-list">\n${items.join('\n')}\n</ol>\n</div>`;
}
