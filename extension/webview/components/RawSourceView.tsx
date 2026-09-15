import { useMemo } from 'react';

interface Props {
  content: string;
  fileName: string;
}

type TokenType =
  | 'yaml-fence'
  | 'yaml-key'
  | 'yaml-value'
  | 'heading'
  | 'code-fence'
  | 'code-lang'
  | 'bold'
  | 'italic'
  | 'link-text'
  | 'link-url'
  | 'image-bang'
  | 'table-pipe'
  | 'html-tag'
  | 'comment'
  | 'callout-marker'
  | 'inline-code'
  | 'math'
  | 'math-fence'
  | 'shortcode'
  | 'citation'
  | 'attr'
  | 'text';

interface Token {
  type: TokenType;
  text: string;
}

const TOKEN_CLASSES: Record<TokenType, string> = {
  'yaml-fence': 'syn-yaml-fence',
  'yaml-key': 'syn-yaml-key',
  'yaml-value': 'syn-yaml-val',
  'heading': 'syn-heading',
  'code-fence': 'syn-code-fence',
  'code-lang': 'syn-code-lang',
  'bold': 'syn-bold',
  'italic': 'syn-italic',
  'link-text': 'syn-link-text',
  'link-url': 'syn-link-url',
  'image-bang': 'syn-image',
  'table-pipe': 'syn-table-pipe',
  'html-tag': 'syn-html',
  'comment': 'syn-comment',
  'callout-marker': 'syn-callout',
  'inline-code': 'syn-inline-code',
  'math': 'syn-math',
  'math-fence': 'syn-math',
  'shortcode': 'syn-shortcode',
  'citation': 'syn-citation',
  'attr': 'syn-callout',
  'text': '',
};

/** Tokenize inline markdown syntax within a string */
function tokenizeInline(text: string): Token[] {
  const tokens: Token[] = [];
  const combined = /(\{\{<[^>]+>\}\})|(\[@[\w:./-]+(?:\s*;\s*@[\w:./-]+)*\])|(`[^`]+`)|(\*\*[^*]+\*\*)|(\*[^*]+\*)|(!?\[[^\]]*\]\([^)]*\))|(<[a-zA-Z/][^>]*>)|(\$[^$]+\$)|(\{[^}]*\})/g;

  let lastIndex = 0;
  let m;
  while ((m = combined.exec(text)) !== null) {
    if (m.index > lastIndex) {
      tokens.push({ type: 'text', text: text.slice(lastIndex, m.index) });
    }

    const matched = m[0];
    if (m[1]) {
      tokens.push({ type: 'shortcode', text: matched });
    } else if (m[2]) {
      tokens.push({ type: 'citation', text: matched });
    } else if (m[3]) {
      tokens.push({ type: 'inline-code', text: matched });
    } else if (m[4]) {
      tokens.push({ type: 'bold', text: matched });
    } else if (m[5]) {
      tokens.push({ type: 'italic', text: matched });
    } else if (m[6]) {
      // Image or link — split into parts
      if (matched.startsWith('!')) {
        const linkMatch = matched.match(/^(!\[)([^\]]*)(\]\()([^)]*)(\))$/);
        if (linkMatch) {
          tokens.push({ type: 'image-bang', text: linkMatch[1] });
          tokens.push({ type: 'link-text', text: linkMatch[2] });
          tokens.push({ type: 'image-bang', text: linkMatch[3] });
          tokens.push({ type: 'link-url', text: linkMatch[4] });
          tokens.push({ type: 'image-bang', text: linkMatch[5] });
        } else {
          tokens.push({ type: 'link-text', text: matched });
        }
      } else {
        const linkMatch = matched.match(/^(\[)([^\]]*)(\]\()([^)]*)(\))$/);
        if (linkMatch) {
          tokens.push({ type: 'link-text', text: linkMatch[1] + linkMatch[2] + linkMatch[3].charAt(0) });
          tokens.push({ type: 'link-url', text: linkMatch[3].slice(1) + linkMatch[4] + linkMatch[5] });
        } else {
          tokens.push({ type: 'link-text', text: matched });
        }
      }
    } else if (m[7]) {
      tokens.push({ type: 'html-tag', text: matched });
    } else if (m[8]) {
      tokens.push({ type: 'math', text: matched });
    } else if (m[9]) {
      tokens.push({ type: 'attr', text: matched });
    }

    lastIndex = m.index + matched.length;
  }

  if (lastIndex < text.length) {
    tokens.push({ type: 'text', text: text.slice(lastIndex) });
  }

  return tokens.length ? tokens : [{ type: 'text', text }];
}

interface LineContext {
  inYaml: boolean;
  inCode: boolean;
  inMath: boolean;
  inComment: boolean;
}

/** Tokenize a single line for syntax highlighting */
function tokenizeLine(line: string, ctx: LineContext): Token[] {
  // YAML front matter fences
  if (/^---\s*$/.test(line) && !ctx.inCode && !ctx.inMath) {
    return [{ type: 'yaml-fence', text: line }];
  }

  // Inside YAML front matter
  if (ctx.inYaml) {
    const m = line.match(/^(\s*[\w-]+)(:)(.*)/);
    if (m) {
      return [
        { type: 'yaml-key', text: m[1] },
        { type: 'text', text: m[2] },
        { type: 'yaml-value', text: m[3] },
      ];
    }
    return [{ type: 'yaml-value', text: line }];
  }

  // Display math fences $$
  if (/^\$\$\s*$/.test(line)) {
    return [{ type: 'math-fence', text: line }];
  }

  // Inside display math block
  if (ctx.inMath) {
    return [{ type: 'math', text: line }];
  }

  // Code fence lines
  if (/^```/.test(line)) {
    const m = line.match(/^(```\s*)(\{?[a-zA-Z][\w-]*[^}]*\}?)?(.*)$/);
    if (m && m[2]) {
      return [
        { type: 'code-fence', text: m[1] },
        { type: 'code-lang', text: m[2] },
        ...(m[3] ? [{ type: 'text', text: m[3] } as Token] : []),
      ];
    }
    return [{ type: 'code-fence', text: line }];
  }

  // Inside code blocks — no further highlighting
  if (ctx.inCode) {
    return [{ type: 'text', text: line }];
  }

  // Headings
  if (/^#{1,6}\s/.test(line)) {
    return [{ type: 'heading', text: line }];
  }

  // Callout/div markers :::
  if (/^:::/.test(line)) {
    return [{ type: 'callout-marker', text: line }];
  }

  // Inside multi-line HTML comment
  if (ctx.inComment) {
    return [{ type: 'comment', text: line }];
  }

  // HTML comments (single-line or start of multi-line)
  if (/^\s*<!--/.test(line)) {
    return [{ type: 'comment', text: line }];
  }

  // Table attribute line  `: {.striped .hover}`
  if (/^:[ \t]+\{[^}]+\}\s*$/.test(line)) {
    return [{ type: 'attr', text: line }];
  }

  // Table rows — highlight pipes AND inline content
  if (/^\|/.test(line)) {
    return tokenizeTableRow(line);
  }

  // Regular lines — inline tokenization
  return tokenizeInline(line);
}

/** Tokenize table rows: pipes get pipe color, cell content gets inline highlighting */
function tokenizeTableRow(line: string): Token[] {
  const tokens: Token[] = [];
  const segments = line.split(/(\|)/);
  for (const seg of segments) {
    if (seg === '|') {
      tokens.push({ type: 'table-pipe', text: '|' });
    } else if (seg) {
      // Tokenize cell content for inline syntax (bold, links, html, etc.)
      tokens.push(...tokenizeInline(seg));
    }
  }
  return tokens;
}

export default function RawSourceView({ content, fileName }: Props) {
  const shouldHighlight = /\.(qmd|md|markdown)$/i.test(fileName);

  const highlightedLines = useMemo(() => {
    const lines = content.split('\n');
    const ctx: LineContext = { inYaml: false, inCode: false, inMath: false, inComment: false };
    let yamlFenceCount = 0;

    return lines.map((line) => {
      if (!shouldHighlight) {
        return [{ type: 'text' as TokenType, text: line }];
      }

      // Track HTML comment state (<!-- ... -->)
      // Comments can span multiple lines; track open/close across lines
      if (!ctx.inCode && !ctx.inYaml && !ctx.inMath) {
        if (ctx.inComment) {
          const tokens = tokenizeLine(line, ctx);
          if (/-->/.test(line)) ctx.inComment = false;
          return tokens;
        }
        if (/^\s*<!--/.test(line) && !(/-->/.test(line))) {
          // Comment opens but doesn't close on this line
          ctx.inComment = true;
          return tokenizeLine(line, ctx);
        }
      }

      // Track YAML state (--- fences at top of file)
      if (/^---\s*$/.test(line) && !ctx.inCode && !ctx.inMath) {
        yamlFenceCount++;
        const tokens = tokenizeLine(line, ctx);
        if (yamlFenceCount === 1) ctx.inYaml = true;
        else if (yamlFenceCount === 2) ctx.inYaml = false;
        return tokens;
      }

      // Track display math state ($$ fences)
      if (/^\$\$\s*$/.test(line) && !ctx.inYaml && !ctx.inCode) {
        const tokens = tokenizeLine(line, ctx);
        ctx.inMath = !ctx.inMath;
        return tokens;
      }

      // Track code block state (``` fences)
      if (/^```/.test(line) && !ctx.inYaml && !ctx.inMath) {
        const tokens = tokenizeLine(line, ctx);
        ctx.inCode = !ctx.inCode;
        return tokens;
      }

      return tokenizeLine(line, ctx);
    });
  }, [content, shouldHighlight]);

  return (
    <table className="raw-source-table">
      <tbody>
        {highlightedLines.map((tokens, i) => (
          <tr key={i}>
            <td className="raw-line-number">{i + 1}</td>
            <td className="raw-line-content">
              {tokens.map((token, j) => {
                const cls = TOKEN_CLASSES[token.type];
                return cls
                  ? <span key={j} className={cls}>{token.text}</span>
                  : <span key={j}>{token.text}</span>;
              })}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
