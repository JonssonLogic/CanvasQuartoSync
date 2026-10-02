import * as vscode from 'vscode';

/**
 * Creates a debounced file watcher for .qmd files.
 * Calls the callback at most once per `delayMs` milliseconds.
 */
export function createQmdWatcher(
  callback: (uri: vscode.Uri) => void,
  delayMs: number = 300
): vscode.Disposable {
  const watcher = vscode.workspace.createFileSystemWatcher('**/*.qmd');
  let debounceTimer: ReturnType<typeof setTimeout> | undefined;

  const debouncedCallback = (uri: vscode.Uri) => {
    if (debounceTimer) {
      clearTimeout(debounceTimer);
    }
    debounceTimer = setTimeout(() => callback(uri), delayMs);
  };

  const onChange = watcher.onDidChange(debouncedCallback);
  const onCreate = watcher.onDidCreate(debouncedCallback);

  return vscode.Disposable.from(watcher, onChange, onCreate, {
    dispose: () => {
      if (debounceTimer) clearTimeout(debounceTimer);
    },
  });
}
