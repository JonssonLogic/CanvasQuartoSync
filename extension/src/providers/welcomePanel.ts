import * as vscode from 'vscode';

// ── Welcome Tab ──────────────────────────────────────────────────────
// Shows on first activation when no config.toml exists in the workspace.
// Offers "Create New Course" and "Open Example (Mechatronics)" actions.

const WELCOMED_KEY = 'cqs.hasShownWelcome';

export function showWelcomeIfNeeded(context: vscode.ExtensionContext): void {
  // Don't show if already welcomed in this workspace
  if (context.workspaceState.get(WELCOMED_KEY)) return;

  // Don't show if config.toml already exists
  const folders = vscode.workspace.workspaceFolders;
  if (!folders) return;

  const configUri = vscode.Uri.joinPath(folders[0].uri, 'config.toml');
  vscode.workspace.fs.stat(configUri).then(
    () => {
      // config.toml exists — mark as welcomed, don't show
      context.workspaceState.update(WELCOMED_KEY, true);
    },
    () => {
      // No config.toml — show the welcome tab
      context.workspaceState.update(WELCOMED_KEY, true);
      openWelcomePanel(context);
    }
  );
}

function openWelcomePanel(context: vscode.ExtensionContext): void {
  const panel = vscode.window.createWebviewPanel(
    'cqs.welcome',
    'Welcome to Canvas Quarto Sync',
    vscode.ViewColumn.One,
    { enableScripts: true }
  );

  panel.webview.html = getWelcomeHtml();

  panel.webview.onDidReceiveMessage(
    (msg) => {
      if (msg.type === 'newProject') {
        vscode.commands.executeCommand('cqs.newProject');
        panel.dispose();
      }
      if (msg.type === 'openWalkthrough') {
        vscode.commands.executeCommand(
          'workbench.action.openWalkthrough',
          'KalleMirza.canvasquartosync#cqs.gettingStarted'
        );
        panel.dispose();
      }
      if (msg.type === 'cloneMechatronics') {
        vscode.commands.executeCommand(
          'git.clone',
          'https://github.com/cenmir/Mechatronics.git'
        );
        panel.dispose();
      }
    },
    undefined,
    context.subscriptions
  );
}

function getWelcomeHtml(): string {
  return /* html */ `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Welcome</title>
  <style>
    body {
      font-family: var(--vscode-font-family, 'Segoe UI', sans-serif);
      color: var(--vscode-foreground);
      background: var(--vscode-editor-background);
      padding: 0;
      margin: 0;
      display: flex;
      justify-content: center;
    }
    .container {
      max-width: 600px;
      padding: 48px 32px;
    }
    h1 {
      font-size: 28px;
      font-weight: 600;
      margin-bottom: 8px;
    }
    .subtitle {
      font-size: 15px;
      color: var(--vscode-descriptionForeground);
      margin-bottom: 32px;
    }
    .actions {
      display: flex;
      flex-direction: column;
      gap: 12px;
    }
    .action-btn {
      display: flex;
      align-items: center;
      gap: 12px;
      padding: 14px 18px;
      background: var(--vscode-button-secondaryBackground);
      border: 1px solid var(--vscode-panel-border, transparent);
      border-radius: 6px;
      cursor: pointer;
      font-family: inherit;
      font-size: 14px;
      color: var(--vscode-foreground);
      text-align: left;
      transition: background 0.15s;
    }
    .action-btn:hover {
      background: var(--vscode-list-hoverBackground);
    }
    .action-btn.primary {
      background: var(--vscode-button-background);
      color: var(--vscode-button-foreground);
    }
    .action-btn.primary:hover {
      background: var(--vscode-button-hoverBackground);
    }
    .action-icon { font-size: 24px; width: 32px; text-align: center; }
    .action-text strong { display: block; margin-bottom: 2px; }
    .action-text span { font-size: 12px; color: var(--vscode-descriptionForeground); }
    .action-btn.primary .action-text span { color: var(--vscode-button-foreground); opacity: 0.8; }
    .footer {
      margin-top: 32px;
      font-size: 12px;
      color: var(--vscode-descriptionForeground);
    }
    .footer a { color: var(--vscode-textLink-foreground); text-decoration: none; }
    .footer a:hover { text-decoration: underline; }
  </style>
</head>
<body>
  <div class="container">
    <h1>Canvas Quarto Sync</h1>
    <p class="subtitle">Write courses in Quarto, preview live, sync to Canvas in one click.</p>

    <div class="actions">
      <button class="action-btn primary" onclick="vscode.postMessage({type:'newProject'})">
        <span class="action-icon">+</span>
        <span class="action-text">
          <strong>Create New Course Project</strong>
          <span>Set up config.toml, study guide template, and folder structure</span>
        </span>
      </button>

      <button class="action-btn" onclick="vscode.postMessage({type:'cloneMechatronics'})">
        <span class="action-icon">&#128218;</span>
        <span class="action-text">
          <strong>Open Example (Mechatronics)</strong>
          <span>Clone the canonical example course to see how it works</span>
        </span>
      </button>

      <button class="action-btn" onclick="vscode.postMessage({type:'openWalkthrough'})">
        <span class="action-icon">&#128221;</span>
        <span class="action-text">
          <strong>Getting Started Walkthrough</strong>
          <span>Step-by-step guide from setup to first sync</span>
        </span>
      </button>
    </div>

    <div class="footer">
      <p>Need help? See the <a href="https://github.com/cenmir/CanvasQuartoSync">documentation</a>.</p>
    </div>
  </div>

  <script>const vscode = acquireVsCodeApi();</script>
</body>
</html>`;
}
