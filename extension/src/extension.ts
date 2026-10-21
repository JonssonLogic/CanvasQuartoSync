import * as vscode from 'vscode';
import { showSyncMenu, syncFile } from './commands/syncToCanvas';
import { openPreview } from './commands/openPreview';
import { initCourse } from './commands/initCourse';
import { importFromCanvas } from './commands/importFromCanvas';
import { diffWithCanvas } from './commands/diffWithCanvas';
import { purgeCourse } from './commands/purgeCourse';
import { createGithubRepo, inviteToGithub } from './commands/githubRepo';
import { createStatusBar, updateVisibility, dispose as disposeStatusBar } from './providers/statusBar';
import { createToggleButtons, registerToggleCommands } from './providers/syncOptions';
import { registerSidebarViews } from './providers/sidebarTreeView';
import { openNewProjectPanel } from './providers/newProjectPanel';
import { openCourseBoard } from './providers/courseBoardPanel';
import { openGradeRollupPanel } from './providers/gradeRollupPanel';
import { showWelcomeIfNeeded } from './providers/welcomePanel';

export function activate(context: vscode.ExtensionContext) {
  const version = context.extension.packageJSON?.version ?? 'unknown';
  console.log(`CanvasQuartoSync v${version} activated`);

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
      openPreview(context)
    ),
    vscode.commands.registerCommand('cqs.initCourse', () =>
      initCourse(context.extensionPath)
    ),
    vscode.commands.registerCommand('cqs.importFromCanvas', () =>
      importFromCanvas(context.extensionPath)
    ),
    vscode.commands.registerCommand('cqs.diffWithCanvas', () =>
      diffWithCanvas(context.extensionPath)
    ),
    vscode.commands.registerCommand('cqs.purgeCourse', () =>
      purgeCourse(context.extensionPath)
    ),
    vscode.commands.registerCommand('cqs.openCourseBoard', () =>
      openCourseBoard(context)
    ),
    // The panel's old name. Kept so keybindings and scripts that call it work.
    vscode.commands.registerCommand('cqs.showModuleStructure', () =>
      openCourseBoard(context)
    ),
    vscode.commands.registerCommand('cqs.showGradeRollup', () =>
      openGradeRollupPanel(context.extensionPath)
    ),
    vscode.commands.registerCommand('cqs.createGithubRepo', () =>
      createGithubRepo(context)
    ),
    vscode.commands.registerCommand('cqs.inviteToGithub', () =>
      inviteToGithub(context)
    )
  );

  // Welcome tab on first activation (if no config.toml)
  // Auto-open the Course Board if this is a course workspace
  const folders = vscode.workspace.workspaceFolders;
  if (folders) {
    const isCourse = async () => {
      for (const marker of ['config.toml', '_modules.yml']) {
        try {
          await vscode.workspace.fs.stat(vscode.Uri.joinPath(folders[0].uri, marker));
          return true;
        } catch { /* not this one */ }
      }
      return false;
    };
    isCourse().then((yes) => {
      if (yes) {
        // A course folder: the Course Board is the main panel. It opens even
        // when config.toml is incomplete, and says what to fix.
        openCourseBoard(context);
      } else {
        // No config.toml — show welcome for first-time setup
        showWelcomeIfNeeded(context);
      }
    });
  } else {
    showWelcomeIfNeeded(context);
  }

  // Re-check status bar visibility when workspace folders change
  context.subscriptions.push(
    vscode.workspace.onDidChangeWorkspaceFolders(() => updateVisibility())
  );
}

export function deactivate() {
  disposeStatusBar();
}
