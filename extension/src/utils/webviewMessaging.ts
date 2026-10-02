/**
 * Typed message protocol between extension host and webview.
 * Both sides import these types to ensure consistency.
 */

// Extension host -> Webview
export type ToWebviewMessage =
  | { type: 'updateContent'; content: string; filePath: string; imageMap: Record<string, string> }
  | { type: 'updateConfig'; calloutStyles: CalloutStyles; brandColors: Record<string, string> }
  | { type: 'commentsLoaded'; comments: CommentData[] }
  | { type: 'imageUri'; requestId: string; uri: string };

// Webview -> Extension host
export type ToExtensionMessage =
  | { type: 'ready' }
  | { type: 'saveComment'; comment: CommentData }
  | { type: 'deleteComment'; id: string }
  | { type: 'resolveImage'; requestId: string; relativePath: string }
  | { type: 'openFile'; filePath: string };

export interface CalloutStyles {
  [calloutType: string]: {
    border: string;
    bg: string;
    icon: string;
  };
}

export interface CommentData {
  id: string;
  line: number;
  text: string;
  author: string;
  timestamp: string;
  sectionPath?: string;
  context?: string;
}
