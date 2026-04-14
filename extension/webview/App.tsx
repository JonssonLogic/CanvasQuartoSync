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

  }
}

// ── KaTeX / non-text selection helpers ──────────────────────────────

function findKatexAncestor(node: Node | null): Element | null {
  let cur = node instanceof Element ? node : node?.parentElement ?? null;
  while (cur) {
    if (cur.classList?.contains('katex')) return cur;
    cur = cur.parentElement;
  }
  return null;
}

/**
 * Given a DOM Range, return plain text suitable for searching in the markdown
 * source. KaTeX elements are replaced with their LaTeX source; images are dropped.
 */
function extractSourceTextFromRange(range: Range): string {
  const frag = range.cloneContents();
  // Replace each KaTeX root with its LaTeX annotation
  frag.querySelectorAll('.katex').forEach((el) => {
    const annotation = el.querySelector('annotation[encoding="application/x-tex"]');
    const latex = annotation?.textContent?.trim() ?? '';
    // Wrap in $ or $$ depending on whether it was a display equation
    const isDisplay = el.closest('.katex-display') !== null;
    const replacement = document.createTextNode(isDisplay ? `$$${latex}$$` : `$${latex}$`);
    el.parentNode?.replaceChild(replacement, el);
  });
  // Drop images
  frag.querySelectorAll('img').forEach((img) => img.remove());
  return frag.textContent?.trim() ?? '';
}

/**
 * Fallback: walk up to nearest block-level ancestor, take its first ~30 chars
 * of plain text (skipping math markup), and find that anchor in the source.
 */
function findOffsetFromDOMRange(range: Range, cleanContent: string): number {
  const blockTags = new Set(['P', 'LI', 'TD', 'TH', 'H1', 'H2', 'H3', 'H4', 'H5', 'H6', 'BLOCKQUOTE', 'DIV']);
  let el: Element | null = range.startContainer instanceof Element
    ? range.startContainer
    : range.startContainer.parentElement;
  while (el && !blockTags.has(el.tagName)) el = el.parentElement;
  if (!el) return 0;

  // Get plain text of the block, skipping .katex-mathml spans
  const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT, {
    acceptNode(node) {
      return node.parentElement?.closest('.katex-mathml')
        ? NodeFilter.FILTER_REJECT
        : NodeFilter.FILTER_ACCEPT;
    },
  });
  let blockText = '';
  while (walker.nextNode()) blockText += (walker.currentNode as Text).textContent;
  const anchor = blockText.trim().slice(0, 30);
  if (!anchor) return 0;

  const idx = cleanContent.indexOf(anchor);
  return idx === -1 ? 0 : idx;
}

// ── App Component ────────────────────────────────────────────────────

export default function App() {
  const fileContent = useFileContent();
  const { comments, addComment, editComment, deleteComment } = useComments(
    fileContent?.content ?? '', vscode
  );
  const contentRef = useRef<HTMLDivElement>(null);
  const savedRangeRef = useRef<Range | null>(null);

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

  // --- Raw source toggle ---
  const [showRawSource, setShowRawSource] = useState(false);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === 'u') {
        e.preventDefault();
        setShowRawSource(prev => !prev);
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
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

    const rawText = sel.toString().trim();
    if (!rawText) { setSelection(null); return; }

    const range = sel.getRangeAt(0);
    savedRangeRef.current = range.cloneRange();
    const rect = range.getBoundingClientRect();

    const { cleanContent } = extractComments(fileContent?.content ?? '');
    let targetText = rawText;
    let offset = -1;

    // Strategy 1: exact match in source
    offset = cleanContent.indexOf(rawText);

    // Strategy 2: KaTeX — replace rendered math with LaTeX source and retry
    if (offset === -1) {
      const sourceText = extractSourceTextFromRange(range);
      if (sourceText && sourceText !== rawText) {
        offset = cleanContent.indexOf(sourceText);
        if (offset !== -1) targetText = sourceText;
      }
    }

    // Strategy 3: first non-empty line only (handles multi-line list/table selections)
    if (offset === -1) {
      const firstLine = rawText.split('\n').find(l => l.trim());
      if (firstLine && firstLine !== rawText) {
        offset = cleanContent.indexOf(firstLine.trim());
        if (offset !== -1) targetText = firstLine.trim();
      }
    }

    // Strategy 4: DOM position fallback — anchor to paragraph start
    if (offset === -1 && savedRangeRef.current) {
      offset = findOffsetFromDOMRange(savedRangeRef.current, cleanContent);
      // Use first 40 chars of raw text as the stored target
      targetText = rawText.slice(0, 40);
    }

    if (offset === -1) offset = 0;

    console.log('[CQS Comment] Text selected:', targetText.slice(0, 50), 'offset:', offset);
    setSelection({
      text: targetText,
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
      {/* Toolbar */}
      <div style={{
        position: 'sticky', top: 0, zIndex: 50,
        background: '#fff', borderBottom: '1px solid #ddd',
        padding: '4px 16px', display: 'flex', gap: '8px', alignItems: 'center',
        fontSize: '0.8rem',
      }}>
        {!showRawSource && comments.length > 0 && (
          <button
            className={`comment-btn ${showComments ? 'comment-btn-primary' : ''}`}
            onClick={() => setShowComments(prev => !prev)}
          >
            {showComments ? `Hide ${comments.length} comment${comments.length !== 1 ? 's' : ''}` : `Show ${comments.length} comment${comments.length !== 1 ? 's' : ''}`}
          </button>
        )}
        {!showRawSource && comments.length === 0 && (
          <span style={{ color: '#6b6b6b' }}>Select text to add a comment</span>
        )}
        <div style={{ marginLeft: 'auto' }}>
          <button
            className={`comment-btn ${showRawSource ? 'comment-btn-primary' : ''}`}
            title="Toggle raw source (Ctrl+U)"
            onClick={() => setShowRawSource(prev => !prev)}
          >
            {showRawSource ? 'Rendered' : 'Raw source'}
          </button>
        </div>
      </div>

      {showRawSource ? (
        <pre style={{
          fontFamily: 'var(--font-mono, monospace)',
          fontSize: '0.85rem',
          lineHeight: 1.6,
          padding: '16px 24px',
          margin: 0,
          whiteSpace: 'pre-wrap',
          wordBreak: 'break-word',
          color: 'var(--color-text, #2d3b45)',
          background: 'var(--color-code-bg, #f7f7f7)',
          minHeight: '100vh',
        }}>
          {fileContent.content}
        </pre>
      ) : (
      <div ref={contentRef}>
        <MarkdownRenderer
          content={processed}
          imageMap={fileContent.imageMap}
        />
      </div>
      )}

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
