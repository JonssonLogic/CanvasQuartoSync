/**
 * QMD Preprocessor — ported from MDViewer (zero platform dependencies).
 * Strips YAML, converts fenced divs, tabsets, shortcodes, cross-refs, bib.
 */

import { processCitations, generateBibliography, type BibEntry } from './bibParser';

interface ParsedAttrs {
  attrs: Record<string, string>;
  classes: string[];
  styles: string[];
}

function parseQmdAttributes(raw: string): ParsedAttrs {
  const attrs: Record<string, string> = {};
  const classes: string[] = [];
  const styles: string[] = [];
  const inner = raw.replace(/^\{|\}$/g, '').trim();
  const tokenRe = /(\.[a-zA-Z_][\w-]*|#[a-zA-Z_][\w-]*|[a-zA-Z_][\w-]*\s*=\s*"[^"]*"|[a-zA-Z_][\w-]*\s*=\s*[^\s"]+)/g;
  let m;
  while ((m = tokenRe.exec(inner)) !== null) {
    const token = m[1];
    if (token.startsWith('.')) {
      classes.push(token.slice(1));
    } else if (token.startsWith('#')) {
      attrs['id'] = token.slice(1);
    } else {
      const eqIdx = token.indexOf('=');
      const key = token.slice(0, eqIdx).trim();
      const value = token.slice(eqIdx + 1).trim().replace(/^"|"$/g, '');
      if (key === 'fig-align') {
        if (value === 'center') styles.push('margin-left:auto;margin-right:auto;display:block');
        else if (value === 'left') styles.push('margin-right:auto;display:block');
        else if (value === 'right') styles.push('margin-left:auto;display:block');
      } else if (key === 'fig-alt') {
        attrs['alt'] = value;
      } else if (key === 'fig-cap') {
        attrs['data-caption'] = value;
      } else if (key === 'style') {
        styles.push(value);
      } else {
        attrs[key] = value;
      }
    }
  }
  return { attrs, classes, styles };
}

function buildHtmlAttrs(parsed: ParsedAttrs): string {
  let html = '';
  for (const [k, v] of Object.entries(parsed.attrs)) {
    html += ` ${k}="${v}"`;
  }
  if (parsed.classes.length) html += ` class="${parsed.classes.join(' ')}"`;
  if (parsed.styles.length) html += ` style="${parsed.styles.join(';')}"`;
  return html;
}

export function extractYamlMeta(content: string): { bibliography?: string; title?: string } {
  const match = content.match(/^---\r?\n([\s\S]*?)\r?\n---/);
  if (!match) return {};
  const yaml = match[1].replace(/\r/g, '');
  const meta: Record<string, string> = {};
  for (const line of yaml.split('\n')) {
    const m = line.match(/^(\w[\w-]*)\s*:\s*(.+)$/);
    if (m) meta[m[1]] = m[2].replace(/^["']|["']$/g, '').trim();
  }
  return meta;
}

export function preprocessQmd(content: string, bibEntries?: BibEntry[]): string {
  let result = content.replace(/\r\n/g, '\n').replace(/\r/g, '\n');

  // Strip YAML front matter
  result = result.replace(/^---\n[\s\S]*?\n---\n?/, '');

  // Strip HTML comments
  result = result.replace(/<!--[\s\S]*?-->/g, '\n');

  // Strip executable code chunks
  result = result.replace(
    /^```\s*\{[a-zA-Z][a-zA-Z0-9]*[^}]*\}[^\n]*\n[\s\S]*?^```[ \t]*$/gm,
    ''
  );

  // Images with attributes
  result = result.replace(
    /!\[([^\]]*)\]\(([^)]*)\)\{([^}]+)\}/g,
    (_match, alt: string, src: string, attrStr: string) => {
      const parsed = parseQmdAttributes(`{${attrStr}}`);
      const imgAlt = parsed.attrs['alt'] ?? alt;
      delete parsed.attrs['alt'];
      const caption = parsed.attrs['data-caption'];
      delete parsed.attrs['data-caption'];
      const figCaption = caption || alt;
      const effectiveAlt = figCaption ? '' : imgAlt;
      let html = `<img src="${src}" alt="${effectiveAlt}"${buildHtmlAttrs(parsed)} />`;
      if (figCaption) {
        html = `\n<figure>${html}<figcaption>${figCaption}</figcaption></figure>\n`;
      }
      return html;
    }
  );

  // Links with attributes
  result = result.replace(
    /(?<!!)\[([^\]]*)\]\(([^)]*)\)\{([^}]+)\}/g,
    (_match, text: string, href: string, attrStr: string) => {
      const parsed = parseQmdAttributes(`{${attrStr}}`);
      return `<a href="${href}"${buildHtmlAttrs(parsed)}>${text}</a>`;
    }
  );

  // Fenced divs
  const lines = result.split('\n');
  let quartoDivDepth = 0;
  for (let i = 0; i < lines.length; i++) {
    const openMatch = lines[i].match(/^:::\s*(\{[^}]+\})\s*$/);
    if (openMatch) {
      const parsed = parseQmdAttributes(openMatch[1]);
      const ncol = parsed.attrs['layout-ncol'];
      const nrow = parsed.attrs['layout-nrow'];
      if (ncol) {
        parsed.classes.push('quarto-layout-grid');
        parsed.styles.push(`grid-template-columns:repeat(${ncol},1fr)`);
        delete parsed.attrs['layout-ncol'];
      }
      if (nrow) {
        parsed.classes.push('quarto-layout-grid');
        parsed.styles.push(`grid-template-rows:repeat(${nrow},auto)`);
        delete parsed.attrs['layout-nrow'];
      }
      lines[i] = `<div${buildHtmlAttrs(parsed)}>\n`;
      quartoDivDepth++;
    } else if (/^:::\s*$/.test(lines[i]) && quartoDivDepth > 0) {
      lines[i] = '\n</div>';
      quartoDivDepth--;
    }
  }
  result = lines.join('\n');

  // Panel-tabset
  result = result.replace(
    /<div class="panel-tabset">([\s\S]*?)<\/div>/g,
    (_match, inner: string) => {
      const sections = inner.split(/^##\s+(.+)$/gm).filter(s => s.trim());
      if (sections.length < 2) return _match;
      let tabsetHtml = '<div class="panel-tabset">';
      for (let i = 0; i < sections.length - 1; i += 2) {
        const label = sections[i].trim();
        const content = (sections[i + 1] || '').trim();
        tabsetHtml += `\n<div data-tab-label="${label}">\n\n${content}\n\n</div>`;
      }
      tabsetHtml += '\n</div>';
      return tabsetHtml;
    }
  );

  // Strip table class annotations
  result = result.replace(/^:[ \t]+\{[^}]+\}[ \t]*$/gm, '');

  // Shortcodes
  result = result.replace(/\{\{<\s*pagebreak\s*>\}\}/g, '\n<hr class="quarto-pagebreak" />\n');
  result = result.replace(
    /\{\{<\s*video\s+([^\s>]+)(?:\s+[^>]*)?\s*>\}\}/g,
    (_match, url: string) => {
      if (/youtube\.com|youtu\.be|vimeo\.com/.test(url)) {
        const embedUrl = url.replace(/watch\?v=/, 'embed/').replace(/youtu\.be\//, 'youtube.com/embed/');
        return `<div class="quarto-video"><iframe src="${embedUrl}" frameborder="0" allowfullscreen style="width:100%;aspect-ratio:16/9;border-radius:8px;"></iframe></div>`;
      }
      return `<div class="quarto-video"><video controls src="${url}" style="width:100%;border-radius:8px;"></video></div>`;
    }
  );
  result = result.replace(/\{\{<[^>]*>\}\}/g, '');

  // Cross-references
  const labelMap = new Map<string, { type: string; num: number }>();
  let figCount = 0, secCount = 0, tblCount = 0;

  const headingRe = /^(#{1,6})\s+(.+?)(?:\s*\{#([^}]+)\})?\s*$/gm;
  let hm;
  while ((hm = headingRe.exec(result)) !== null) {
    const id = hm[3] || hm[2].toLowerCase().replace(/[^\w]+/g, '-').replace(/^-|-$/g, '');
    if (id.startsWith('sec-')) { secCount++; labelMap.set(id, { type: 'Section', num: secCount }); }
  }

  const figLabelRe = /id="(fig-[^"]+)"/g;
  let fm2;
  while ((fm2 = figLabelRe.exec(result)) !== null) { figCount++; labelMap.set(fm2[1], { type: 'Figure', num: figCount }); }

  const tblLabelRe = /id="(tbl-[^"]+)"/g;
  let tm;
  while ((tm = tblLabelRe.exec(result)) !== null) { tblCount++; labelMap.set(tm[1], { type: 'Table', num: tblCount }); }

  result = result.replace(
    /@((?:fig|tbl|sec)-[\w-]+)/g,
    (_match, label: string) => {
      const ref = labelMap.get(label);
      if (ref) return `<a href="#${label}" class="cross-ref">${ref.type}\u00A0${ref.num}</a>`;
      return `<a href="#${label}" class="cross-ref">${label}</a>`;
    }
  );

  // Images with citations in alt
  result = result.replace(
    /^!\[([^\]]*@[^\]]*)\]\(([^)]+)\)\s*$/gm,
    (_match, alt: string, src: string) => {
      return `\n<figure><img src="${src}" alt="" /><figcaption>${alt}</figcaption></figure>\n`;
    }
  );

  // Bibliography
  if (bibEntries && bibEntries.length > 0) {
    const { content: citedContent, citedKeys } = processCitations(result, bibEntries);
    result = citedContent;
    const bibHtml = generateBibliography(citedKeys, bibEntries);
    if (result.includes('id="refs"')) {
      result = result.replace(/<div id="refs">\s*<\/div>/, bibHtml);
    } else {
      result += bibHtml;
    }
  }

  return result.trim();
}
