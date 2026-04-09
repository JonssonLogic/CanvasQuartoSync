import { useState, useRef, useEffect } from 'react';

interface Props {
  position: { top: number; left: number };
  onSubmit: (body: string) => void;
  onCancel: () => void;
}

export default function CommentInput({ position, onSubmit, onCancel }: Props) {
  const [body, setBody] = useState('');
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => { textareaRef.current?.focus(); }, []);

  useEffect(() => {
    const handleKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onCancel(); };
    window.addEventListener('keydown', handleKey);
    return () => window.removeEventListener('keydown', handleKey);
  }, [onCancel]);

  useEffect(() => {
    const handleClick = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) onCancel();
    };
    const timer = setTimeout(() => window.addEventListener('mousedown', handleClick), 100);
    return () => { clearTimeout(timer); window.removeEventListener('mousedown', handleClick); };
  }, [onCancel]);

  const handleSubmit = () => { if (body.trim()) onSubmit(body.trim()); };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); handleSubmit(); }
  };

  return (
    <div ref={containerRef} className="comment-input-popup" style={{ top: position.top, left: position.left }}>
      <div className="comment-input-header">Add comment</div>
      <textarea ref={textareaRef} className="comment-textarea" value={body}
        onChange={(e) => setBody(e.target.value)} onKeyDown={handleKeyDown}
        placeholder="Write your comment..." rows={3} />
      <div className="comment-popup-actions">
        <button className="comment-btn comment-btn-primary" onClick={handleSubmit} disabled={!body.trim()}>Save</button>
        <button className="comment-btn" onClick={onCancel}>Cancel</button>
      </div>
      <div className="comment-input-hint">Ctrl+Enter to save</div>
    </div>
  );
}
