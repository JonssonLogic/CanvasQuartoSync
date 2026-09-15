import { useMemo, useEffect, useState, useCallback, useRef } from 'react';
import { useFileContent } from './hooks/useFileContent';
import { useComments } from './hooks/useComments';
import MarkdownRenderer, { setVsCodeApi } from './components/MarkdownRenderer';
import RawSourceView from './components/RawSourceView';
import CommentInput from './components/CommentInput';
import CommentPopup from './components/CommentPopup';
import CommentPanel from './components/CommentPanel';
import { preprocessQmd } from './preprocessing/qmdPreprocess';
import { findRenderedTextInSource } from './preprocessing/commentParser';
import './styles/markdown.css';
import './styles/comments.css';

declare function acquireVsCodeApi(): { postMessage(msg: any): void };
const vscode = acquireVsCodeApi();
setVsCodeApi(vscode);

// ── Comment selection helpers (ported from MDViewer's App.tsx) ────────

/** Walk up the live DOM from `node` to find the nearest .katex ancestor, or null. */
function findKatexAncestor(node: Node): Element | null {
  let n: Node | null = node;
  while (n) {
    if (n.nodeType === Node.ELEMENT_NODE && (n as Element).classList?.contains('katex')) {
      return n as Element;
    }
    n = n.parentNode;
  }
  return null;
}

/**
 * Extract source-faithful text from a DOM range by replacing KaTeX with its
 * original LaTeX source (from the MathML annotation) and stripping images.
 */
function extractSourceTextFromRange(range: Range): string {
  try {
    const fragment = range.cloneContents();
    const katexEls = fragment.querySelectorAll('.katex');

    if (katexEls.length === 0) {
      // Selection is entirely within a .katex element — cloneContents() yields only
      // a partial .katex-html subtree with no .katex root. Walk up the live DOM instead.
      const liveKatex = findKatexAncestor(range.startContainer);
      if (liveKatex) {
        const annotation = liveKatex.querySelector('annotation[encoding="application/x-tex"]');
        if (annotation?.textContent) {
          const delim = liveKatex.parentElement?.classList.contains('katex-display') ? '$$' : '$';
          return `${delim}${annotation.textContent}${delim}`;
        }
      }
    } else {
      katexEls.forEach(el => {
        let annotation = el.querySelector('annotation[encoding="application/x-tex"]');
        let isDisplay = el.parentElement?.classList.contains('katex-display') ?? false;

        // If the annotation is absent, the selection started inside this .katex element
        // (so .katex-mathml was excluded from the clone). Retrieve it from the live DOM.
        if (!annotation) {
          const liveKatex = findKatexAncestor(range.startContainer);
          if (liveKatex) {
            annotation = liveKatex.querySelector('annotation[encoding="application/x-tex"]');
            isDisplay = liveKatex.parentElement?.classList.contains('katex-display') ?? false;
          }
        }

        if (annotation?.textContent) {
          const delim = isDisplay ? '$$' : '$';
          el.replaceWith(`${delim}${annotation.textContent}${delim}`);
        }
      });
    }

    fragment.querySelectorAll('img').forEach(el => el.remove());
    return (fragment.textContent ?? '').trim();
  } catch {
    return '';
  }
}

/**
 * Find an approximate source offset by walking up the DOM to the nearest block
 * element and matching its first plain-text words (skipping KaTeX MathML).
 */
function findOffsetFromDOMRange(range: Range, cleanContent: string): number {
  const startNode = range.startContainer;
  let el: Element | null = startNode.nodeType === Node.TEXT_NODE
    ? startNode.parentElement
    : startNode as Element;

  const blockTags = new Set(['P', 'LI', 'TD', 'TH', 'H1', 'H2', 'H3', 'H4', 'H5', 'H6', 'BLOCKQUOTE']);
  while (el && !blockTags.has(el.tagName)) el = el.parentElement;
  if (!el) return -1;

  const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
  const parts: string[] = [];
  let node = walker.nextNode();
  while (node) {
    let p: Element | null = node.parentElement;
    let skip = false;
    while (p && p !== el) {
      if (p.classList?.contains('katex-mathml')) { skip = true; break; }
      p = p.parentElement;
    }
    if (!skip && node.textContent?.trim()) {
      parts.push(node.textContent);
      if (parts.join('').trim().length >= 30) break;
    }
    node = walker.nextNode();
  }

  const probe = parts.join('').trim().slice(0, 30);
  if (probe.length < 3) return -1;
  return findRenderedTextInSource(probe, cleanContent)?.start ?? -1;
}

/**
 * Count visible text characters from the start of the nearest block element to
 * range.startContainer[startOffset]. Used to rank duplicate-word occurrences by
 * how close they are to the actual selection position.
 */
function estimateSelectionOffsetInBlock(range: Range): number {
  const startNode = range.startContainer;
  let blockEl: Element | null = startNode.nodeType === Node.TEXT_NODE
    ? startNode.parentElement
    : startNode as Element;

  const blockTags = new Set(['P', 'LI', 'TD', 'TH', 'H1', 'H2', 'H3', 'H4', 'H5', 'H6', 'BLOCKQUOTE']);
  while (blockEl && !blockTags.has(blockEl.tagName)) blockEl = blockEl.parentElement;
  if (!blockEl) return 0;

  const walker = document.createTreeWalker(blockEl, NodeFilter.SHOW_TEXT);
  let count = 0;
  let node = walker.nextNode();
  while (node) {
    if (node === startNode) {
      count += range.startOffset;
      break;
    }
    let p: Element | null = node.parentElement;
    let skip = false;
    while (p && p !== blockEl) {
      if (p.classList?.contains('katex-mathml')) { skip = true; break; }
      p = p.parentElement;
    }
    if (!skip) count += node.textContent?.length ?? 0;
    node = walker.nextNode();
  }
  return count;
}

// ── App Component ────────────────────────────────────────────────────

export default function App() {
  const fileContent = useFileContent();
  const {
    comments,
    showComments,
    cleanContent,
    displayContent,
    addComment,
    editComment,
    deleteComment,
    toggleShowComments,
  } = useComments(fileContent?.content ?? '', vscode);

  const rootRef = useRef<HTMLDivElement>(null);
  const savedRangeRef = useRef<Range | null>(null);

  // displayContent is the comment-stripped source, with <mark> highlights
  // injected when comments are visible
  const processed = useMemo(() => preprocessQmd(displayContent), [displayContent]);

  // Signal ready
  useEffect(() => {
    console.log('[CQS Preview] React app loaded, signaling ready');
    vscode.postMessage({ type: 'ready' });
  }, []);

  // --- Raw source toggle ---
  const [showRawSource, setShowRawSource] = useState(false);

  // Keyboard shortcuts
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      const mod = e.ctrlKey || e.metaKey;
      if (mod && e.key === 'u') { e.preventDefault(); setShowRawSource(prev => !prev); }
      if (mod && e.key === 'm') { e.preventDefault(); toggleShowComments(); }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [toggleShowComments]);

  // --- Comment UI state ---
  const [commentPopup, setCommentPopup] = useState<{
    commentId: string;
    position: { top: number; left: number };
  } | null>(null);

  const [commentInput, setCommentInput] = useState<{
    targetText: string;
    charOffset: number;
    position: { top: number; left: number };
  } | null>(null);

  const [addCommentBtn, setAddCommentBtn] = useState<{
    position: { top: number; left: number };
    targetText: string;
  } | null>(null);

  /** Convert a viewport rect to a position inside the (position: relative) root. */
  const positionBelow = useCallback((rect: DOMRect, centered: boolean) => {
    const root = rootRef.current;
    if (!root) return { top: rect.bottom + 4, left: rect.left };
    const rootRect = root.getBoundingClientRect();
    const left = rect.left - rootRect.left + (centered ? rect.width / 2 : 0);
    // Keep popups (max 340px wide) inside a narrow preview panel
    const maxLeft = Math.max(8, root.clientWidth - (centered ? 60 : 350));
    return {
      top: rect.bottom - rootRect.top + 4,
      left: Math.min(Math.max(8, left), maxLeft),
    };
  }, []);

  // Handle text selection → show "+ Comment" button
  const handleMouseUp = useCallback(() => {
    if (!showComments || showRawSource) {
      setAddCommentBtn(null);
      return;
    }

    const sel = window.getSelection();
    if (!sel || sel.isCollapsed || !sel.toString().trim()) {
      // Small delay before hiding to allow clicking the button
      setTimeout(() => setAddCommentBtn(null), 200);
      return;
    }

    const text = sel.toString().trim();
    const range = sel.getRangeAt(0);
    savedRangeRef.current = range.cloneRange();
    setAddCommentBtn({
      position: positionBelow(range.getBoundingClientRect(), true),
      targetText: text,
    });
  }, [showComments, showRawSource, positionBelow]);

  // When "+ Comment" button is clicked
  const handleAddCommentClick = useCallback(() => {
    if (!addCommentBtn) return;
    const { targetText, position } = addCommentBtn;

    const range = savedRangeRef.current;
    let offset = -1;
    let resolvedTarget = targetText;

    // Where in the source the selection roughly is, to pick between repeated occurrences
    let approxOffset = -1;
    if (range) {
      const blockOffset = findOffsetFromDOMRange(range, cleanContent);
      if (blockOffset !== -1) approxOffset = blockOffset + estimateSelectionOffsetInBlock(range);
    }

    // Strategy 1: match the rendered text against the source with markdown syntax
    // projected out, so selections across **bold**, links and table cells resolve
    // to their real source span. Try the LaTeX form first when the selection
    // touches KaTeX (it only differs from the plain text then).
    const candidates: string[] = [];
    if (range) {
      const sourceText = extractSourceTextFromRange(range);
      if (sourceText && sourceText !== targetText) candidates.push(sourceText);
    }
    candidates.push(targetText);
    // Strategy 2: first non-empty line only (a multi-block selection can't be one span)
    const firstLine = targetText.split('\n').find(l => l.trim())?.trim() ?? '';
    if (firstLine && firstLine !== targetText) candidates.push(firstLine);

    for (const candidate of candidates) {
      const span = findRenderedTextInSource(candidate, cleanContent, approxOffset);
      if (span) {
        offset = span.start;
        resolvedTarget = cleanContent.slice(span.start, span.end);
        break;
      }
    }

    // Strategy 3: DOM position fallback — anchor to surrounding paragraph
    if (offset === -1 && range) {
      offset = findOffsetFromDOMRange(range, cleanContent);
      if (offset !== -1) resolvedTarget = targetText.slice(0, 60).trim();
    }

    if (offset === -1) {
      console.warn('[CQS Comment] Could not find selection in source markdown');
      setAddCommentBtn(null);
      return;
    }

    setCommentInput({ targetText: resolvedTarget, charOffset: offset, position });
    setAddCommentBtn(null);
    window.getSelection()?.removeAllRanges();
  }, [addCommentBtn, cleanContent]);

  // Submit new comment
  const handleCommentSubmit = useCallback((body: string) => {
    if (!commentInput) return;
    addComment(commentInput.targetText, commentInput.charOffset, body);
    setCommentInput(null);
  }, [commentInput, addComment]);

  const cancelCommentInput = useCallback(() => setCommentInput(null), []);
  const closeCommentPopup = useCallback(() => setCommentPopup(null), []);

  // Handle clicking a comment highlight in the rendered view
  const handleCommentClick = useCallback((commentId: string, rect: DOMRect) => {
    setCommentPopup({ commentId, position: positionBelow(rect, false) });
  }, [positionBelow]);

  // Scroll to a comment from the panel
  const handleScrollToComment = useCallback((commentId: string) => {
    // A highlight spanning markdown syntax is split into several <mark> segments
    const segments = document.querySelectorAll(`[data-comment-id="${commentId}"]`);
    if (segments.length === 0) return;
    segments[0].scrollIntoView({ behavior: 'smooth', block: 'center' });
    segments.forEach(el => {
      el.classList.add('comment-highlight-flash');
      setTimeout(() => el.classList.remove('comment-highlight-flash'), 1500);
    });
  }, []);

  const activeComment = commentPopup
    ? comments.find(c => c.id === commentPopup.commentId)
    : null;

  if (!fileContent) {
    return (
      <div className="loading">
        <p>Open a .qmd file and the preview will appear here.</p>
      </div>
    );
  }

  const commentLabel = `${comments.length} comment${comments.length !== 1 ? 's' : ''}`;
  const fileName = fileContent.filePath.split(/[\\/]/).pop() ?? '';

  return (
    <div ref={rootRef} className="preview-root">
      {/* Toolbar */}
      <div className="preview-toolbar">
        {!showRawSource && (comments.length > 0 || !showComments) && (
          <button
            className={`comment-btn ${showComments ? 'comment-btn-primary' : ''}`}
            title="Toggle comments (Ctrl+M)"
            onClick={toggleShowComments}
          >
            {showComments ? `Hide ${commentLabel}` : `Show ${commentLabel}`}
          </button>
        )}
        {!showRawSource && comments.length === 0 && showComments && (
          <span className="preview-toolbar-hint">Select text to add a comment</span>
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

      <div className="preview-layout">
        {showRawSource ? (
          <div className="raw-source-view">
            <div className="raw-source-pre">
              <RawSourceView content={fileContent.content.replace(/\r\n?/g, '\n')} fileName={fileName} />
            </div>
          </div>
        ) : (
          <main className="preview-content" onMouseUp={handleMouseUp}>
            <MarkdownRenderer
              content={processed}
              imageMap={fileContent.imageMap}
              onCommentClick={showComments ? handleCommentClick : undefined}
            />
          </main>
        )}

        {/* Comment panel sidebar */}
        {showComments && !showRawSource && comments.length > 0 && (
          <CommentPanel
            comments={comments}
            onScrollTo={handleScrollToComment}
            onDelete={deleteComment}
          />
        )}
      </div>

      {/* "+ Comment" button (floating near selection) */}
      {addCommentBtn && showComments && !showRawSource && (
        <button
          className="add-comment-btn"
          style={{ top: addCommentBtn.position.top, left: addCommentBtn.position.left }}
          onMouseDown={(e) => { e.preventDefault(); handleAddCommentClick(); }}
        >
          + Comment
        </button>
      )}

      {/* Comment input popover */}
      {commentInput && (
        <CommentInput
          position={commentInput.position}
          onSubmit={handleCommentSubmit}
          onCancel={cancelCommentInput}
        />
      )}

      {/* Comment popup (view/edit/delete) */}
      {commentPopup && activeComment && (
        <CommentPopup
          commentId={activeComment.id}
          body={activeComment.body}
          date={activeComment.updatedAt}
          targetText={activeComment.targetText}
          position={commentPopup.position}
          onEdit={editComment}
          onDelete={deleteComment}
          onClose={closeCommentPopup}
        />
      )}
    </div>
  );
}
