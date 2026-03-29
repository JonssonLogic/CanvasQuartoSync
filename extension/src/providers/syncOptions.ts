import * as vscode from 'vscode';

// ── Sync flags (shared between status bar toggles and sync commands) ──

export interface SyncOptions {
  verbose: boolean;
  force: boolean;
  syncCalendar: boolean;
  checkDrift: boolean;
}

export const syncOptions: SyncOptions = {
  verbose: true,
  force: false,
  syncCalendar: false,
  checkDrift: false,
};

// ── Toggle buttons in the status bar ──────────────────────────────────
//
// These sit next to the main "Sync to Canvas" button at the bottom of
// VS Code. Each one is a clickable toggle — click to flip on/off.
// When ON, the button is highlighted with a warning background color.
// When OFF, it appears dimmed.

interface ToggleDef {
  key: keyof SyncOptions;
  labelOn: string;
  labelOff: string;
  tooltip: string;
  priority: number; // higher = further left in the status bar
}

const toggles: ToggleDef[] = [
  {
    key: 'force',
    labelOn: '$(zap) Force',
    labelOff: '$(zap) Force',
    tooltip: 'Force re-render: ignore cache, re-render all files',
    priority: 99,
  },
  {
    key: 'syncCalendar',
    labelOn: '$(calendar) Calendar',
    labelOff: '$(calendar) Calendar',
    tooltip: 'Sync calendar events',
    priority: 98,
  },
  {
    key: 'checkDrift',
    labelOn: '$(git-compare) Drift',
    labelOff: '$(git-compare) Drift',
    tooltip: 'Check drift: compare local vs Canvas (no sync)',
    priority: 97,
  },
];

const toggleItems: vscode.StatusBarItem[] = [];

export function createToggleButtons(): vscode.StatusBarItem[] {
  for (const def of toggles) {
    const item = vscode.window.createStatusBarItem(
      vscode.StatusBarAlignment.Left,
      def.priority
    );
    item.command = `cqs.toggle.${def.key}`;
    item.tooltip = def.tooltip;
    updateToggleAppearance(item, def, syncOptions[def.key]);
    toggleItems.push(item);
  }
  return toggleItems;
}

function updateToggleAppearance(
  item: vscode.StatusBarItem,
  def: ToggleDef,
  isOn: boolean
): void {
  if (isOn) {
    item.text = def.labelOn;
    // Yellow-ish highlight so it's obvious the flag is active
    item.backgroundColor = new vscode.ThemeColor(
      'statusBarItem.warningBackground'
    );
  } else {
    item.text = def.labelOff;
    item.backgroundColor = undefined; // default / dimmed
  }
}

export function registerToggleCommands(): vscode.Disposable[] {
  return toggles.map((def, i) =>
    vscode.commands.registerCommand(`cqs.toggle.${def.key}`, () => {
      syncOptions[def.key] = !syncOptions[def.key];
      updateToggleAppearance(toggleItems[i], def, syncOptions[def.key]);
    })
  );
}

export function showToggleButtons(): void {
  for (const item of toggleItems) {
    item.show();
  }
}

export function hideToggleButtons(): void {
  for (const item of toggleItems) {
    item.hide();
  }
}

export function disposeToggleButtons(): void {
  for (const item of toggleItems) {
    item.dispose();
  }
}
