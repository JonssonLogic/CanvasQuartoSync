import * as vscode from 'vscode';
import * as path from 'path';
import * as fs from 'fs';

// ── QMD Preview Provider ─────────────────────────────────────────────
//
// Manages a WebviewPanel that shows a live preview of .qmd files.
// The extension host reads the .qmd file, resolves image paths to
// webview-safe URIs, and sends the content to the React webview
// via postMessage. The webview handles parsing and rendering.

let currentPanel: vscode.WebviewPanel | undefined;
let currentFilePath: string | undefined;

export function openPreviewPanel(
  context: vscode.ExtensionContext
): void {
  const editor = vscode.window.activeTextEditor;
  if (!editor || !editor.document.fileName.endsWith('.qmd')) {
    vscode.window.showWarningMessage('Open a .qmd file first.');
    return;
  }

  const filePath = editor.document.fileName;

  // If panel exists, just update its content
  if (currentPanel) {
    currentPanel.reveal(vscode.ViewColumn.Beside);
    sendContent(currentPanel, filePath, context);
    currentFilePath = filePath;
    return;
  }

  const workspaceRoot = vscode.workspace.workspaceFolders?.[0]?.uri;
  if (!workspaceRoot) return;

  // Create the webview panel — opens side-by-side with the editor
  currentPanel = vscode.window.createWebviewPanel(
    'cqs.preview',
    'QMD Preview',
    vscode.ViewColumn.Beside,
    {
      enableScripts: true,
      retainContextWhenHidden: true,
      // Allow the webview to load images from the workspace
      localResourceRoots: [
        workspaceRoot,
        vscode.Uri.file(path.join(context.extensionPath, 'dist', 'webview')),
      ],
    }
  );

  currentPanel.webview.html = getWebviewHtml(
    currentPanel.webview,
    context.extensionPath
  );

  currentFilePath = filePath;

  // Send initial content once webview signals ready
  currentPanel.webview.onDidReceiveMessage(
    (msg) => {
      if (msg.type === 'ready') {
        sendContent(currentPanel!, currentFilePath!, context);
      }
      if (msg.type === 'openFile' && msg.filePath) {
        const uri = vscode.Uri.file(msg.filePath);
        vscode.window.showTextDocument(uri);
      }
      // Open external links in VS Code's browser
      if (msg.type === 'openExternal' && msg.url) {
        vscode.env.openExternal(vscode.Uri.parse(msg.url));
      }
      // Save comments back to the .qmd file
      if (msg.type === 'saveComment' && msg.content && currentFilePath) {
        const uri = vscode.Uri.file(currentFilePath);
        const encoder = new TextEncoder();
        vscode.workspace.fs.writeFile(uri, encoder.encode(msg.content));
      }
    },
    undefined,
    context.subscriptions
  );

  // Update preview when user switches to a different .qmd file
  const editorChangeDisposable = vscode.window.onDidChangeActiveTextEditor(
    (editor) => {
      if (
        editor &&
        editor.document.fileName.endsWith('.qmd') &&
        currentPanel
      ) {
        currentFilePath = editor.document.fileName;
        sendContent(currentPanel, currentFilePath, context);
      }
    }
  );

  // Update preview on save
  const saveDisposable = vscode.workspace.onDidSaveTextDocument((doc) => {
    if (
      doc.fileName.endsWith('.qmd') &&
      doc.fileName === currentFilePath &&
      currentPanel
    ) {
      sendContent(currentPanel, currentFilePath, context);
    }
  });

  // Also update on live edits (debounced)
  let editTimer: ReturnType<typeof setTimeout> | undefined;
  const editDisposable = vscode.workspace.onDidChangeTextDocument((e) => {
    if (
      e.document.fileName.endsWith('.qmd') &&
      e.document.fileName === currentFilePath &&
      currentPanel
    ) {
      if (editTimer) clearTimeout(editTimer);
      editTimer = setTimeout(() => {
        sendContent(currentPanel!, currentFilePath!, context);
      }, 400);
    }
  });

  currentPanel.onDidDispose(() => {
    currentPanel = undefined;
    currentFilePath = undefined;
    editorChangeDisposable.dispose();
    saveDisposable.dispose();
    editDisposable.dispose();
    if (editTimer) clearTimeout(editTimer);
  });
}

function sendContent(
  panel: vscode.WebviewPanel,
  filePath: string,
  context: vscode.ExtensionContext
): void {
  // Read the .qmd file content (use the in-memory version if it's open)
  const doc = vscode.workspace.textDocuments.find(
    (d) => d.fileName === filePath
  );
  const content = doc
    ? doc.getText()
    : fs.readFileSync(filePath, 'utf-8');

  // Build an image map: relative paths → webview URIs
  // This lets the webview display images from the workspace
  const fileDir = path.dirname(filePath);
  const imageMap: Record<string, string> = {};

  // Find all image references in the content
  const imagePatterns = [
    /!\[.*?\]\((.*?)\)/g,          // ![alt](path)
    /!\[\]\((.*?)\)/g,             // ![](path)
    /src=['"](.*?)['"]/g,          // src="path"
  ];

  for (const pattern of imagePatterns) {
    let match;
    while ((match = pattern.exec(content)) !== null) {
      const imgPath = match[1];
      if (imgPath && !imgPath.startsWith('http') && !imgPath.startsWith('data:')) {
        const absPath = path.resolve(fileDir, imgPath);
        if (fs.existsSync(absPath)) {
          const webviewUri = panel.webview.asWebviewUri(
            vscode.Uri.file(absPath)
          );
          imageMap[imgPath] = webviewUri.toString();
        }
      }
    }
  }

  panel.webview.postMessage({
    type: 'updateContent',
    content,
    filePath,
    imageMap,
  });

  // Update panel title
  panel.title = `Preview: ${path.basename(filePath)}`;
}

function getWebviewHtml(
  webview: vscode.Webview,
  extensionPath: string
): string {
  const distPath = path.join(extensionPath, 'dist', 'webview');
  const htmlPath = path.join(distPath, 'index.html');

  // If the webview has been built via Vite, use the built HTML
  console.log('[CQS Preview] Looking for Vite build at:', htmlPath, 'exists:', fs.existsSync(htmlPath));
  if (fs.existsSync(htmlPath)) {
    let html = fs.readFileSync(htmlPath, 'utf-8');

    // Convert local file references to webview URIs
    const distUri = webview.asWebviewUri(vscode.Uri.file(distPath));
    // Replace absolute/relative asset paths with webview URIs
    // Vite outputs: src="/assets/index.js" or href="/assets/index.css"
    html = html.replace(/(href|src)="[./]*assets\//g, `$1="${distUri}/assets/`);

    // Add CSP meta tag
    const csp = `<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src ${webview.cspSource} https: data:; media-src ${webview.cspSource} https: blob:; script-src ${webview.cspSource} 'unsafe-inline'; style-src ${webview.cspSource} 'unsafe-inline'; font-src ${webview.cspSource} https: data:; frame-src https:;">`;
    html = html.replace('<head>', `<head>\n    ${csp}`);

    return html;
  }

  // Fallback: inline HTML if webview hasn't been built yet
  return /* html */ `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src ${webview.cspSource} https: data:; script-src 'unsafe-inline'; style-src 'unsafe-inline'; font-src https: data:; frame-src https:;">
  <title>QMD Preview</title>
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/katex.min.css">
  <style>
    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
      color: var(--vscode-foreground, #333);
      background: var(--vscode-editor-background, #fff);
      padding: 24px;
      max-width: 900px;
      margin: 0 auto;
      line-height: 1.6;
    }
    h1, h2, h3, h4, h5, h6 { margin-top: 1.5em; margin-bottom: 0.5em; }
    h1 { font-size: 1.8em; border-bottom: 1px solid var(--vscode-panel-border, #ddd); padding-bottom: 0.3em; }
    h2 { font-size: 1.4em; border-bottom: 1px solid var(--vscode-panel-border, #ddd); padding-bottom: 0.2em; }
    h3 { font-size: 1.2em; }
    p { margin: 0.8em 0; }
    a { color: var(--vscode-textLink-foreground, #0066cc); }
    code {
      background: var(--vscode-textCodeBlock-background, #f5f5f5);
      padding: 2px 6px;
      border-radius: 3px;
      font-size: 0.9em;
    }
    pre {
      background: #f7f7f7;
      padding: 12px 16px;
      border-radius: 4px;
      overflow-x: auto;
      font-size: 0.9em;
      color: #003B4F;
    }
    pre code { background: none; padding: 0; }
    table {
      border-collapse: collapse;
      margin: 1em 0;
      width: 100%;
    }
    th, td {
      border: 1px solid var(--vscode-panel-border, #ddd);
      padding: 8px 12px;
      text-align: left;
    }
    th { background: var(--vscode-textCodeBlock-background, #f5f5f5); font-weight: 600; }
    blockquote {
      border-left: 4px solid var(--vscode-panel-border, #ddd);
      margin: 1em 0;
      padding: 0.5em 1em;
      color: var(--vscode-descriptionForeground, #666);
    }
    img { max-width: 100%; height: auto; }
    hr { border: none; border-top: 1px solid var(--vscode-panel-border, #ddd); margin: 2em 0; }
    ul, ol { padding-left: 1.5em; }
    li { margin: 0.3em 0; }

    /* Callout styles matching Canvas */
    .callout { border-left: 4px solid; padding: 12px 16px; margin: 16px 0; border-radius: 4px; }
    .callout-title { margin: 0 0 8px 0; font-weight: bold; }
    .callout-tip { border-color: #198754; background-color: #d1e7dd; }
    .callout-important { border-color: #dc3545; background-color: #f8d7da; }
    .callout-warning { border-color: #ffc107; background-color: #fff3cd; }
    .callout-note { border-color: #0d6efd; background-color: #cfe2ff; }
    .callout-caution { border-color: #fd7e14; background-color: #ffe5d0; }

    /* Syntax highlighting (matching Canvas/Quarto) */
    .token-kw { color: #003B4F; font-weight: bold; }
    .token-cf { color: #003B4F; font-weight: bold; }
    .token-st, .token-ss, .token-vs, .token-ch { color: #20794D; }
    .token-fu { color: #4758AB; }
    .token-dt, .token-bn, .token-fl, .token-dv, .token-er, .token-al { color: #AD0000; }
    .token-co, .token-cv, .token-in, .token-do, .token-wa { color: #5E5E5E; }
    .token-ot, .token-op, .token-sc { color: #5E5E5E; }
    .token-at { color: #657422; }
    .token-im { color: #00769E; }
    .token-cn { color: #8f5902; }
    .token-va { color: #111111; }

    .yaml-header {
      background: var(--vscode-textCodeBlock-background, #f5f5f5);
      border: 1px solid var(--vscode-panel-border, #ddd);
      border-radius: 4px;
      padding: 12px;
      margin-bottom: 24px;
      font-size: 0.85em;
      color: var(--vscode-descriptionForeground, #666);
    }
    .yaml-header strong { color: var(--vscode-foreground, #333); }

    .loading { text-align: center; padding: 48px; color: var(--vscode-descriptionForeground, #999); }
  </style>
</head>
<body>
  <div id="root" class="loading">Loading preview...</div>
  <script>
    const vscode = acquireVsCodeApi();

    // Simple markdown-to-HTML (fallback when React webview isn't built)
    function renderMarkdown(content, imageMap) {
      // Strip YAML frontmatter
      let md = content.replace(/^---[\\s\\S]*?---\\n*/m, '');

      // Replace image paths with webview URIs
      for (const [relPath, uri] of Object.entries(imageMap || {})) {
        md = md.split(relPath).join(uri);
      }

      // Convert markdown to HTML (basic)
      let html = md
        // Headers
        .replace(/^###### (.+)$/gm, '<h6>$1</h6>')
        .replace(/^##### (.+)$/gm, '<h5>$1</h5>')
        .replace(/^#### (.+)$/gm, '<h4>$1</h4>')
        .replace(/^### (.+)$/gm, '<h3>$1</h3>')
        .replace(/^## (.+)$/gm, '<h2>$1</h2>')
        .replace(/^# (.+)$/gm, '<h1>$1</h1>')
        // Bold and italic
        .replace(/\\*\\*\\*(.+?)\\*\\*\\*/g, '<strong><em>$1</em></strong>')
        .replace(/\\*\\*(.+?)\\*\\*/g, '<strong>$1</strong>')
        .replace(/\\*(.+?)\\*/g, '<em>$1</em>')
        // Images
        .replace(/!\\[([^\\]]*)\\]\\(([^)]+)\\)/g, '<img src="$2" alt="$1">')
        // Links
        .replace(/\\[([^\\]]+)\\]\\(([^)]+)\\)/g, '<a href="$2">$1</a>')
        // Inline code
        .replace(/\`([^\`]+)\`/g, '<code>$1</code>')
        // Horizontal rules
        .replace(/^---$/gm, '<hr>')
        // Line breaks
        .replace(/\\\\$/gm, '<br>')
        // Paragraphs
        .replace(/\\n\\n+/g, '</p><p>')
        ;

      // Tables
      html = html.replace(
        /^\\|(.+)\\|\\s*\\n\\|[:\\-\\| ]+\\|\\s*\\n((?:\\|.+\\|\\s*\\n)*)/gm,
        (match, header, body) => {
          const heads = header.split('|').map(h => '<th>' + h.trim() + '</th>').join('');
          const rows = body.trim().split('\\n').map(row => {
            const cells = row.replace(/^\\||\\|$/g, '').split('|').map(c => '<td>' + c.trim() + '</td>').join('');
            return '<tr>' + cells + '</tr>';
          }).join('');
          return '<table><thead><tr>' + heads + '</tr></thead><tbody>' + rows + '</tbody></table>';
        }
      );

      // Callouts: ::: {.callout-note} ... :::
      html = html.replace(
        /:::\\s*\\{\\.(callout-\\w+)(?:\\s+title="([^"]*)")?\\}\\s*([\\s\\S]*?):::/g,
        (match, type, title, body) => {
          const icons = {tip:'💡',important:'❗',warning:'⚠️',note:'📝',caution:'🔶'};
          const shortType = type.replace('callout-', '');
          const icon = icons[shortType] || '';
          const heading = title || shortType.charAt(0).toUpperCase() + shortType.slice(1);
          return '<div class="callout ' + type + '"><p class="callout-title">' + icon + ' ' + heading + '</p>' + body + '</div>';
        }
      );

      // Code blocks
      html = html.replace(
        /\`\`\`(\\w*)\\n([\\s\\S]*?)\`\`\`/g,
        '<pre><code class="language-$1">$2</code></pre>'
      );

      return '<p>' + html + '</p>';
    }

    // Listen for content updates from the extension
    window.addEventListener('message', (event) => {
      const msg = event.data;
      if (msg.type === 'updateContent') {
        const root = document.getElementById('root');
        root.className = '';
        root.innerHTML = renderMarkdown(msg.content, msg.imageMap);
      }
    });

    // Signal that we're ready to receive content
    vscode.postMessage({ type: 'ready' });
  </script>
</body>
</html>`;
}
