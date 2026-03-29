import * as vscode from 'vscode';
import { showSyncMenu, syncFile } from './commands/syncToCanvas';
import { openPreview } from './commands/openPreview';
import { initCourse } from './commands/initCourse';
import { importFromCanvas } from './commands/importFromCanvas';
import { diffWithCanvas } from './commands/diffWithCanvas';
import { createStatusBar, updateVisibility, dispose as disposeStatusBar } from './providers/statusBar';
import { createToggleButtons, registerToggleCommands } from './providers/syncOptions';
import { registerSidebarViews } from './providers/sidebarTreeView';
import { openNewProjectPanel } from './providers/newProjectPanel';

export function activate(context: vscode.ExtensionContext) {
  console.log('CanvasQuartoSync extension activated');

  // Sidebar tree views (Project / Sync / Tools)
  registerSidebarViews(context);

  // Status bar — main sync button + toggle buttons for flags
  const statusBar = createStatusBar();
  context.subscriptions.push(statusBar);
  const toggleButtons = createToggleButtons();
  for (const btn of toggleButtons) {
    context.subscriptions.push(btn);
  }
  context.subscriptions.push(...registerToggleCommands());

  // Commands
  context.subscriptions.push(
    vscode.commands.registerCommand('cqs.newProject', () =>
      openNewProjectPanel(context.extensionPath)
    ),
    vscode.commands.registerCommand('cqs.syncToCanvas', () =>
      showSyncMenu(context.extensionPath)
    ),
    vscode.commands.registerCommand('cqs.syncFile', (uri?: vscode.Uri) =>
      syncFile(context.extensionPath, uri)
    ),
    vscode.commands.registerCommand('cqs.openPreview', () =>
      openPreview()
    ),
    vscode.commands.registerCommand('cqs.initCourse', () =>
      initCourse(context.extensionPath)
    ),
    vscode.commands.registerCommand('cqs.importFromCanvas', () =>
      importFromCanvas(context.extensionPath)
    ),
    vscode.commands.registerCommand('cqs.diffWithCanvas', () =>
      diffWithCanvas(context.extensionPath)
    )
  );

  // Re-check status bar visibility when workspace folders change
  context.subscriptions.push(
    vscode.workspace.onDidChangeWorkspaceFolders(() => updateVisibility())
  );
}

export function deactivate() {
  disposeStatusBar();
}
