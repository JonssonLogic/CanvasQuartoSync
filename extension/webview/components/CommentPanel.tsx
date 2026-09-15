import type { Comment } from '../types/comments';

interface Props {
  comments: Comment[];
  onScrollTo: (commentId: string) => void;
  onDelete: (id: string) => void;
}

export default function CommentPanel({ comments, onScrollTo, onDelete }: Props) {
  if (comments.length === 0) {
    return (
      <aside className="comment-panel">
        <div className="comment-panel-header">Comments</div>
        <div className="comment-panel-empty">No comments yet</div>
      </aside>
    );
  }

  const active = comments.filter(c => !c.orphaned);
  const orphaned = comments.filter(c => c.orphaned);

  return (
    <aside className="comment-panel">
      <div className="comment-panel-header">
        Comments <span className="comment-panel-count">{comments.length}</span>
      </div>
      <div className="comment-panel-list">
        {active.map(c => (
          <div
            key={c.id}
            className="comment-panel-entry"
            onClick={() => onScrollTo(c.id)}
          >
            <div className="comment-panel-target">
              &ldquo;{c.targetText.length > 50 ? c.targetText.slice(0, 50) + '...' : c.targetText}&rdquo;
            </div>
            <div className="comment-panel-body">{c.body}</div>
            <div className="comment-panel-meta">
              <span className="comment-panel-section">{c.section}</span>
              <span className="comment-panel-date">{c.updatedAt}</span>
            </div>
          </div>
        ))}
        {orphaned.length > 0 && (
          <>
            <div className="comment-panel-orphaned-header">Orphaned</div>
            {orphaned.map(c => (
              <div key={c.id} className="comment-panel-entry comment-panel-entry-orphaned">
                <div className="comment-panel-target">
                  &ldquo;{c.targetText.length > 50 ? c.targetText.slice(0, 50) + '...' : c.targetText}&rdquo;
                </div>
                <div className="comment-panel-body">{c.body}</div>
                <div className="comment-panel-actions-inline">
                  <button className="comment-btn comment-btn-small comment-btn-danger" onClick={() => onDelete(c.id)}>
                    Delete
                  </button>
                </div>
              </div>
            ))}
          </>
        )}
      </div>
    </aside>
  );
}
