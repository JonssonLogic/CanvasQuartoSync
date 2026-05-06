import * as vscode from 'vscode';
import { openPreviewPanel } from '../providers/previewProvider';

export function openPreview(context: vscode.ExtensionContext): void {
  openPreviewPanel(context);
}
