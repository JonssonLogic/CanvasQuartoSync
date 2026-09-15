import { useState, useRef, useEffect } from 'react';

interface Props {
  commentId: string;
  body: string;
  date: string;
  targetText: string;
  position: { top: number; left: number };
  onEdit: (id: string, newBody: string) => void;
  onDelete: (id: string) => void;
  onClose: () => void;
}

export default function CommentPopup({
  commentId,
  body,
  date,
  targetText,
  position,
  onEdit,
  onDelete,
  onClose,
}: Props) {
  const [isEditing, setIsEditing] = useState(false);
  const [editBody, setEditBody] = useState(body);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const popupRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setEditBody(body);
    setIsEditing(false);
    setConfirmDelete(false);
  }, [commentId, body]);

  // Close on Escape
  useEffect(() => {
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handleKey);
    return () => window.removeEventListener('keydown', handleKey);
  }, [onClose]);

  // Close on click outside
  useEffect(() => {
    const handleClick = (e: MouseEvent) => {
      if (popupRef.current && !popupRef.current.contains(e.target as Node)) {
        onClose();
      }
    };
    // Delay to avoid closing immediately from the click that opened the popup
    const timer = setTimeout(() => {
      window.addEventListener('mousedown', handleClick);
    }, 100);
    return () => {
      clearTimeout(timer);
      window.removeEventListener('mousedown', handleClick);
    };
  }, [onClose]);

  const handleSave = () => {
    if (editBody.trim()) {
      onEdit(commentId, editBody.trim());
      setIsEditing(false);
    }
  };

  const handleDelete = () => {
    if (confirmDelete) {
      onDelete(commentId);
      onClose();
    } else {
      setConfirmDelete(true);
    }
  };

  return (
    <div
      ref={popupRef}
      className="comment-popup"
      style={{ top: position.top, left: position.left }}
    >
      <div className="comment-popup-target">
        &ldquo;{targetText.length > 60 ? targetText.slice(0, 60) + '...' : targetText}&rdquo;
      </div>

      {isEditing ? (
        <div className="comment-popup-edit">
          <textarea
            className="comment-textarea"
            value={editBody}
            onChange={(e) => setEditBody(e.target.value)}
            rows={3}
            autoFocus
          />
          <div className="comment-popup-actions">
            <button className="comment-btn comment-btn-primary" onClick={handleSave}>Save</button>
            <button className="comment-btn" onClick={() => { setIsEditing(false); setEditBody(body); }}>Cancel</button>
          </div>
        </div>
      ) : (
        <>
          <div className="comment-popup-body">{body}</div>
          <div className="comment-popup-date">{date}</div>
          <div className="comment-popup-actions">
            <button className="comment-btn" onClick={() => setIsEditing(true)}>Edit</button>
            <button
              className={`comment-btn comment-btn-danger${confirmDelete ? ' comment-btn-confirm' : ''}`}
              onClick={handleDelete}
            >
              {confirmDelete ? 'Confirm delete' : 'Delete'}
            </button>
          </div>
        </>
      )}
    </div>
  );
}
