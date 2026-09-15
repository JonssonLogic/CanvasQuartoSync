import { useState, useCallback, useEffect, useMemo, useRef } from 'react';
import type { Comment } from '../types/comments';
import {
  extractComments,
  serializeComments,
  anchorComments,
  injectCommentHighlights,
  generateCommentId,
  locatePosition,
  extractContext,
} from '../preprocessing/commentParser';

const SHOW_COMMENTS_KEY = 'cqs-show-comments';

function readShowComments(): boolean {
  try {
    return localStorage.getItem(SHOW_COMMENTS_KEY) !== 'false';
  } catch {
    return true;
  }
}

/**
 * Comment management hook for the VS Code webview — mirrors MDViewer's
 * useComments, except that every change is saved immediately through the
 * extension host (there is no dirty state or Ctrl+S).
 */
export function useComments(
  rawContent: string,
  vscodeApi: { postMessage(msg: any): void }
) {
  const [comments, setComments] = useState<Comment[]>([]);
  const [showComments, setShowComments] = useState<boolean>(readShowComments);

  // Clean content (comment block stripped) for the rendering pipeline.
  // Derived during render so it never lags one content update behind.
  const { cleanContent, comments: parsed } = useMemo(
    () => extractComments(rawContent),
    [rawContent]
  );

  // The latest raw content and comments, for serialization from callbacks
  const rawContentRef = useRef(rawContent);
  rawContentRef.current = rawContent;
  const commentsRef = useRef(comments);
  commentsRef.current = comments;

  // Re-anchor whenever the file content changes (load, edit, or our own save
  // coming back from the host — re-parsing that is idempotent)
  useEffect(() => {
    setComments(anchorComments(cleanContent, parsed));
  }, [cleanContent, parsed]);

  // Content with highlights injected (when comments are visible)
  const displayContent = useMemo(() => {
    if (!showComments || comments.length === 0) return cleanContent;
    return injectCommentHighlights(cleanContent, comments);
  }, [cleanContent, comments, showComments]);

  // The whole block is rebuilt from `updated`, so serializing against content
  // that doesn't yet include an earlier unechoed save is still correct.
  const save = useCallback((updated: Comment[]) => {
    setComments(updated);
    const newContent = serializeComments(rawContentRef.current, updated);
    vscodeApi.postMessage({ type: 'saveComment', content: newContent });
  }, [vscodeApi]);

  const addComment = useCallback((
    targetText: string,
    charOffset: number,
    body: string
  ) => {
    const clean = extractComments(rawContentRef.current).cleanContent;
    const { section, paragraph, paraStart, targetOffsetInPara } = locatePosition(clean, charOffset);
    const { contextBefore, contextAfter } = extractContext(clean, charOffset, targetText.length);
    const now = new Date().toISOString().slice(0, 10);

    const newComment: Comment = {
      id: generateCommentId(),
      section,
      paragraph,
      paraStart,
      targetText,
      targetOffsetInPara,
      contextBefore,
      contextAfter,
      body,
      createdAt: now,
      updatedAt: now,
      _offset: charOffset,
    };

    save(anchorComments(clean, [...commentsRef.current, newComment]));
  }, [save]);

  const editComment = useCallback((id: string, newBody: string) => {
    const now = new Date().toISOString().slice(0, 10);
    save(commentsRef.current.map(c => c.id === id ? { ...c, body: newBody, updatedAt: now } : c));
  }, [save]);

  const deleteComment = useCallback((id: string) => {
    save(commentsRef.current.filter(c => c.id !== id));
  }, [save]);

  const toggleShowComments = useCallback(() => {
    setShowComments(prev => {
      const next = !prev;
      try { localStorage.setItem(SHOW_COMMENTS_KEY, String(next)); } catch { /* storage unavailable */ }
      return next;
    });
  }, []);

  return {
    comments,
    showComments,
    cleanContent,
    displayContent,
    addComment,
    editComment,
    deleteComment,
    toggleShowComments,
  };
}
