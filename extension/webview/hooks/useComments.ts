import { useState, useCallback, useEffect, useRef } from 'react';
import type { Comment } from '../preprocessing/commentParser';
import {
  extractComments, serializeComments, anchorComments,
  generateCommentId, locatePosition, extractContext,
} from '../preprocessing/commentParser';

/**
 * Comment management hook for VS Code webview.
 * Comments are saved immediately to the .qmd file via postMessage.
 */
export function useComments(
  rawContent: string,
  vscodeApi: { postMessage(msg: any): void }
) {
  const [comments, setComments] = useState<Comment[]>([]);
  const cleanContentRef = useRef('');
  const rawContentRef = useRef('');
  // Guard: skip the next incoming content update after we save
  // (to prevent our own write from resetting state)
  const skipNextUpdate = useRef(false);

  // Parse comments when content changes (file load / external edit)
  useEffect(() => {
    if (skipNextUpdate.current) {
      skipNextUpdate.current = false;
      return;
    }
    if (!rawContent) {
      setComments([]);
      cleanContentRef.current = '';
      rawContentRef.current = '';
      return;
    }
    const { cleanContent, comments: parsed } = extractComments(rawContent);
    cleanContentRef.current = cleanContent;
    rawContentRef.current = rawContent;
    setComments(anchorComments(cleanContent, parsed));
  }, [rawContent]);

  const addComment = useCallback((targetText: string, charOffset: number, body: string) => {
    const clean = cleanContentRef.current;
    const { section, paragraph } = locatePosition(clean, charOffset);
    const { contextBefore, contextAfter } = extractContext(clean, charOffset, targetText.length);
    const now = new Date().toISOString().slice(0, 10);

    const newComment: Comment = {
      id: generateCommentId(), section, paragraph, targetText,
      contextBefore, contextAfter, body,
      createdAt: now, updatedAt: now, _offset: charOffset,
    };

    const updated = [...comments, newComment];
    const anchored = anchorComments(clean, updated);
    setComments(anchored);

    // Save immediately
    const newContent = serializeComments(rawContentRef.current, anchored);
    rawContentRef.current = newContent;
    skipNextUpdate.current = true;
    vscodeApi.postMessage({ type: 'saveComment', content: newContent });
  }, [comments, vscodeApi]);

  const editComment = useCallback((id: string, newBody: string) => {
    const now = new Date().toISOString().slice(0, 10);
    const updated = comments.map(c => c.id === id ? { ...c, body: newBody, updatedAt: now } : c);
    setComments(updated);

    const newContent = serializeComments(rawContentRef.current, updated);
    rawContentRef.current = newContent;
    skipNextUpdate.current = true;
    vscodeApi.postMessage({ type: 'saveComment', content: newContent });
  }, [comments, vscodeApi]);

  const deleteComment = useCallback((id: string) => {
    const updated = comments.filter(c => c.id !== id);
    setComments(updated);

    const newContent = serializeComments(rawContentRef.current, updated);
    rawContentRef.current = newContent;
    skipNextUpdate.current = true;
    vscodeApi.postMessage({ type: 'saveComment', content: newContent });
  }, [comments, vscodeApi]);

  return { comments, addComment, editComment, deleteComment };
}
