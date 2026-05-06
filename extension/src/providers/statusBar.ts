import * as vscode from 'vscode';
import {
  showToggleButtons,
  hideToggleButtons,
  disposeToggleButtons,
} from './syncOptions';

let statusBarItem: vscode.StatusBarItem | undefined;

export function createStatusBar(): vscode.StatusBarItem {
  statusBarItem = vscode.window.createStatusBarItem(
    vscode.StatusBarAlignment.Left,
    100
  );
  statusBarItem.command = 'cqs.syncToCanvas';
  statusBarItem.text = '$(cloud-upload) Sync to Canvas';
  statusBarItem.tooltip = 'Run CanvasQuartoSync';

  updateVisibility();

  return statusBarItem;
}

export function updateVisibility(): void {
  if (!statusBarItem) return;

  const workspaceFolders = vscode.workspace.workspaceFolders;
  if (!workspaceFolders) {
    statusBarItem.hide();
    hideToggleButtons();
    return;
  }

  // Show only if config.toml exists in the workspace
  const configUri = vscode.Uri.joinPath(workspaceFolders[0].uri, 'config.toml');
  vscode.workspace.fs.stat(configUri).then(
    () => {
      statusBarItem!.show();
      showToggleButtons();
    },
    () => {
      statusBarItem!.hide();
      hideToggleButtons();
    }
  );
}

export function setSyncing(syncing: boolean): void {
  if (!statusBarItem) return;
  if (syncing) {
    statusBarItem.text = '$(sync~spin) Syncing...';
    statusBarItem.command = undefined;
  } else {
    statusBarItem.text = '$(cloud-upload) Sync to Canvas';
    statusBarItem.command = 'cqs.syncToCanvas';
  }
}

export function dispose(): void {
  statusBarItem?.dispose();
  disposeToggleButtons();
}
