import * as vscode from 'vscode';

// ── Sidebar Tree Views ───────────────────────────────────────────────
//
// Three tree views in the sidebar, similar to PlatformIO's layout:
//
//   PROJECT
//     New Project
//     Open config.toml
//     Open Settings
//
//   SYNC
//     Sync All Files
//     Sync Current File
//
//   TOOLS
//     Import from Canvas
//     Diff with Canvas
//     Open Preview

interface ActionItem {
  label: string;
  icon: string;
  command: string;
  description?: string;
}

class ActionTreeProvider implements vscode.TreeDataProvider<ActionItem> {
  constructor(private items: ActionItem[]) {}

  getTreeItem(item: ActionItem): vscode.TreeItem {
    const treeItem = new vscode.TreeItem(item.label);
    treeItem.iconPath = new vscode.ThemeIcon(item.icon);
    treeItem.command = {
      command: item.command,
      title: item.label,
    };
    if (item.description) {
      treeItem.description = item.description;
    }
    return treeItem;
  }

  getChildren(): ActionItem[] {
    return this.items;
  }
}

export function registerSidebarViews(
  context: vscode.ExtensionContext
): void {
  // PROJECT section
  const projectProvider = new ActionTreeProvider([
    { label: 'New Project', icon: 'add', command: 'cqs.newProject' },
    {
      label: 'Open config.toml',
      icon: 'settings-gear',
      command: 'cqs.openConfig',
    },
    {
      label: 'Extension Settings',
      icon: 'gear',
      command: 'cqs.openExtensionSettings',
    },
  ]);

  // SYNC section
  const syncProvider = new ActionTreeProvider([
    {
      label: 'Sync All Files',
      icon: 'cloud-upload',
      command: 'cqs.syncToCanvas',
    },
    {
      label: 'Sync Current File',
      icon: 'file-symlink-file',
      command: 'cqs.syncFile',
    },
  ]);

  // TOOLS section
  const toolsProvider = new ActionTreeProvider([
    {
      label: 'Import from Canvas',
      icon: 'cloud-download',
      command: 'cqs.importFromCanvas',
    },
    {
      label: 'Diff with Canvas',
      icon: 'git-compare',
      command: 'cqs.diffWithCanvas',
    },
    {
      label: 'Module Structure',
      icon: 'list-tree',
      command: 'cqs.showModuleStructure',
    },
    {
      label: 'Open Preview',
      icon: 'open-preview',
      command: 'cqs.openPreview',
    },
    {
      label: 'Purge Canvas Course',
      icon: 'trash',
      command: 'cqs.purgeCourse',
    },
  ]);

  context.subscriptions.push(
    vscode.window.registerTreeDataProvider('cqs.projectActions', projectProvider),
    vscode.window.registerTreeDataProvider('cqs.syncActions', syncProvider),
    vscode.window.registerTreeDataProvider('cqs.toolActions', toolsProvider)
  );

  // Helper commands for the sidebar
  context.subscriptions.push(
    vscode.commands.registerCommand('cqs.openConfig', async () => {
      const folders = vscode.workspace.workspaceFolders;
      if (!folders) {
        vscode.window.showWarningMessage('No workspace folder open.');
        return;
      }
      const configUri = vscode.Uri.joinPath(folders[0].uri, 'config.toml');
      try {
        const doc = await vscode.workspace.openTextDocument(configUri);
        await vscode.window.showTextDocument(doc);
      } catch {
        vscode.window.showWarningMessage(
          'No config.toml found in workspace. Use "New Project" to create one.'
        );
      }
    }),
    vscode.commands.registerCommand('cqs.openExtensionSettings', () => {
      vscode.commands.executeCommand(
        'workbench.action.openSettings',
        'cqs'
      );
    })
  );

  // Set context for welcome view
  updateProjectContext();
  context.subscriptions.push(
    vscode.workspace.onDidChangeWorkspaceFolders(() => updateProjectContext())
  );
}

function updateProjectContext(): void {
  const folders = vscode.workspace.workspaceFolders;
  if (!folders) {
    vscode.commands.executeCommand('setContext', 'cqs.hasCourseProject', false);
    return;
  }
  const configUri = vscode.Uri.joinPath(folders[0].uri, 'config.toml');
  vscode.workspace.fs.stat(configUri).then(
    () => vscode.commands.executeCommand('setContext', 'cqs.hasCourseProject', true),
    () => vscode.commands.executeCommand('setContext', 'cqs.hasCourseProject', false)
  );
}
