import React, { useMemo, useEffect, useState, useCallback, useRef } from 'react';
import { useFileContent } from './hooks/useFileContent';
import { useComments } from './hooks/useComments';
import MarkdownRenderer, { setVsCodeApi } from './components/MarkdownRenderer';
import CommentInput from './components/CommentInput';
import { preprocessQmd } from './preprocessing/qmdPreprocess';
import { extractComments } from './preprocessing/commentParser';
import type { Comment } from './preprocessing/commentParser';
import './styles/markdown.css';
import './styles/comments.css';

declare function acquireVsCodeApi(): { postMessage(msg: any): void };
const vscode = acquireVsCodeApi();
setVsCodeApi(vscode);

// ── DOM-based comment highlighting ───────────────────────────────────
// After React renders, walk text nodes to find each comment's targetText
// and wrap matches in <mark> elements with click handlers.

function highlightCommentsInDom(
  container: HTMLElement,
  comments: Comment[],
  onClick: (id: string, rect: DOMRect) => void
) {
  // Remove existing highlights first
  container.querySelectorAll('mark.comment-highlight').forEach((mark) => {
    const parent = mark.parentNode;
    if (parent) {
      parent.replaceChild(document.createTextNode(mark.textContent ?? ''), mark);
      parent.normalize();
    }
  });

  for (const comment of comments) {
    if (!comment.targetText || comment.orphaned) continue;

    const target = comment.targetText;
    let found = false;

    // Strategy 1: Find in a single text node (works for plain text)
    const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT);
    while (walker.nextNode()) {
      const textNode = walker.currentNode as Text;
      const text = textNode.textContent ?? '';
      const idx = text.indexOf(target);
      if (idx === -1) continue;

      const before = text.slice(0, idx);
      const match = text.slice(idx, idx + target.length);
      const after = text.slice(idx + target.length);

      const mark = document.createElement('mark');
      mark.className = 'comment-highlight';
      mark.dataset.commentId = comment.id;
      mark.textContent = match;
      mark.title = comment.body;
      mark.addEventListener('click', (e) => {
        e.stopPropagation();
        onClick(comment.id, mark.getBoundingClientRect());
      });

      const parent = textNode.parentNode!;
      if (after) parent.insertBefore(document.createTextNode(after), textNode.nextSibling);
      parent.insertBefore(mark, textNode.nextSibling);
      if (before) {
        textNode.textContent = before;
      } else {
        parent.removeChild(textNode);
      }
      found = true;
      break;
    }

    // TODO: Strategy 2 — highlight comments on KaTeX math / complex rendered content
    // The target text for math selections contains rendered unicode that doesn't
    // match the DOM structure. Need a different approach (e.g. data attributes on
    // source lines, or matching by surrounding plain text context).
  }
}

// ── App Component ────────────────────────────────────────────────────

export default function App() {
  const fileContent = useFileContent();
  const { comments, addComment, editComment, deleteComment } = useComments(
    fileContent?.content ?? '', vscode
  );
  const contentRef = useRef<HTMLDivElement>(null);

  // Strip comment block before preprocessing for display
  const processed = useMemo(() => {
    if (!fileContent) return '';
    const { cleanContent } = extractComments(fileContent.content);
    return preprocessQmd(cleanContent);
  }, [fileContent?.content]);

  // Signal ready
  useEffect(() => {
    console.log('[CQS Preview] React app loaded, signaling ready');
    vscode.postMessage({ type: 'ready' });
  }, []);

  // --- Comment UI state ---
  const [showComments, setShowComments] = useState(true);
  const [selection, setSelection] = useState<{
    text: string; offset: number; rect: { top: number; left: number };
  } | null>(null);
  const [commentInput, setCommentInput] = useState<{
    top: number; left: number;
  } | null>(null);
  const [viewingComment, setViewingComment] = useState<{
    comment: Comment; rect: { top: number; left: number };
  } | null>(null);
  const [editText, setEditText] = useState('');

  // Highlight comments in the DOM after rendering
  const showCommentPopup = useCallback((commentId: string, rect: DOMRect) => {
    const comment = comments.find(c => c.id === commentId);
    if (comment) {
      setEditText(comment.body);
      setViewingComment({
        comment,
        rect: { top: rect.bottom + window.scrollY + 4, left: rect.left },
      });
    }
  }, [comments]);

  // Store latest comments/callback in refs so the MutationObserver always uses current values
  const commentsRef = useRef(comments);
  commentsRef.current = comments;
  const showCommentsRef = useRef(showComments);
  showCommentsRef.current = showComments;
  const popupRef = useRef(showCommentPopup);
  popupRef.current = showCommentPopup;

  // Apply/remove highlights after React renders
  const suppressObserver = useRef(false);

  useEffect(() => {
    if (!contentRef.current) return;

    const removeHighlights = () => {
      if (!contentRef.current) return;
      suppressObserver.current = true;
      contentRef.current.querySelectorAll('mark.comment-highlight').forEach((mark) => {
        const parent = mark.parentNode;
        if (parent) {
          parent.replaceChild(document.createTextNode(mark.textContent ?? ''), mark);
          parent.normalize();
        }
      });
      suppressObserver.current = false;
    };

    const applyHighlights = () => {
      if (!contentRef.current || !showCommentsRef.current || commentsRef.current.length === 0) return;
      suppressObserver.current = true;
      highlightCommentsInDom(contentRef.current, commentsRef.current, popupRef.current);
      suppressObserver.current = false;
    };

    if (!showComments) {
      removeHighlights();
      return;
    }

    // Initial apply after render settles
    const timer = setTimeout(applyHighlights, 150);

    // Re-apply when React replaces DOM children (e.g. after content update)
    const observer = new MutationObserver(() => {
      if (suppressObserver.current) return;
      if (contentRef.current &&
          !contentRef.current.querySelector('mark.comment-highlight') &&
          commentsRef.current.length > 0 && showCommentsRef.current) {
        setTimeout(applyHighlights, 150);
      }
    });
    observer.observe(contentRef.current, { childList: true, subtree: true });

    return () => { clearTimeout(timer); observer.disconnect(); };
  }, [processed, comments, showComments]);

  // Show "Add comment" button when text is selected
  const handleMouseUp = useCallback((e: React.MouseEvent | MouseEvent) => {
    // Don't clear selection if clicking on the "Add comment" button or comment popup
    const target = e.target as HTMLElement;
    if (target.closest('.add-comment-btn') || target.closest('.comment-input-popup') || target.closest('.comment-popup')) {
      return;
    }

    const sel = window.getSelection();
    if (!sel || sel.isCollapsed || !sel.toString().trim()) {
      setSelection(null);
      return;
    }

    const text = sel.toString().trim();
    if (!text) { setSelection(null); return; }

    const range = sel.getRangeAt(0);
    const rect = range.getBoundingClientRect();

    const { cleanContent } = extractComments(fileContent?.content ?? '');
    let offset = cleanContent.indexOf(text);
    if (offset === -1) {
      const shortText = text.slice(0, 40);
      offset = cleanContent.indexOf(shortText);
      if (offset === -1) offset = 0;
    }

    console.log('[CQS Comment] Text selected:', text.slice(0, 50), 'offset:', offset);
    setSelection({
      text,
      offset,
      rect: { top: rect.bottom + window.scrollY + 4, left: rect.left + rect.width / 2 },
    });
  }, [fileContent?.content]);

  // Close comment popup when clicking outside (must be before early return — hooks can't be conditional)
  const handleClick = useCallback((e: React.MouseEvent) => {
    const target = e.target as HTMLElement;
    if (!target.closest('.comment-input-popup') &&
        !target.closest('.comment-highlight') &&
        !target.closest('.add-comment-btn')) {
      setViewingComment(null);
      // Don't clear selection here — handleMouseUp manages that
    }
  }, []);

  if (!fileContent) {
    return (
      <div className="loading">
        <p>Open a .qmd file and the preview will appear here.</p>
      </div>
    );
  }

  return (
    <div onMouseUp={(e) => handleMouseUp(e)} onClick={handleClick} style={{ position: 'relative' }}>
      {/* Comment toolbar */}
      <div style={{
        position: 'sticky', top: 0, zIndex: 50,
        background: '#fff', borderBottom: '1px solid #ddd',
        padding: '4px 16px', display: 'flex', gap: '8px', alignItems: 'center',
        fontSize: '0.8rem',
      }}>
        {comments.length > 0 && (
          <button
            className={`comment-btn ${showComments ? 'comment-btn-primary' : ''}`}
            onClick={() => setShowComments(prev => !prev)}
          >
            {showComments ? `Hide ${comments.length} comment${comments.length !== 1 ? 's' : ''}` : `Show ${comments.length} comment${comments.length !== 1 ? 's' : ''}`}
          </button>
        )}
        {comments.length === 0 && (
          <span style={{ color: '#6b6b6b' }}>Select text to add a comment</span>
        )}
      </div>

      <div ref={contentRef}>
        <MarkdownRenderer
          content={processed}
          imageMap={fileContent.imageMap}
        />
      </div>

      {/* "Add comment" button on text selection */}
      {selection && !commentInput && (
        <button
          className="add-comment-btn"
          style={{ top: selection.rect.top, left: selection.rect.left }}
          onClick={() => setCommentInput(selection.rect)}
        >
          Add comment
        </button>
      )}

      {/* Comment input popup */}
      {commentInput && selection && (
        <CommentInput
          position={commentInput}
          onSubmit={(body) => {
            addComment(selection.text, selection.offset, body);
            setCommentInput(null);
            setSelection(null);
          }}
          onCancel={() => { setCommentInput(null); setSelection(null); }}
        />
      )}

      {/* Edit existing comment popup — opens as editable textarea immediately */}
      {viewingComment && (
        <div className="comment-input-popup"
          style={{ top: viewingComment.rect.top, left: viewingComment.rect.left }}>
          <div className="comment-popup-target">"{viewingComment.comment.targetText}"</div>
          <textarea className="comment-textarea" value={editText}
            onChange={(e) => setEditText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
                e.preventDefault();
                editComment(viewingComment.comment.id, editText.trim());
                setViewingComment(null);
              }
            }}
            rows={3} autoFocus />
          <div className="comment-popup-actions" style={{ marginTop: '8px' }}>
            <button className="comment-btn comment-btn-primary" onClick={() => {
              editComment(viewingComment.comment.id, editText.trim());
              setViewingComment(null);
            }}>Save</button>
            <button className="comment-btn" style={{ color: '#dc3545' }} onClick={() => {
              deleteComment(viewingComment.comment.id);
              setViewingComment(null);
            }}>Delete</button>
          </div>
          <div className="comment-input-hint">Ctrl+Enter to save</div>
        </div>
      )}
    </div>
  );
}
