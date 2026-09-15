import { useEffect, useRef } from 'react';

export interface ContextMenuItem {
  label: string;
  danger?: boolean;
  onSelect: () => void;
}

interface Props {
  position: { top: number; left: number };
  items: ContextMenuItem[];
  onClose: () => void;
}

/** Small right-click menu, closed by Escape, scrolling or a click elsewhere. */
export default function CommentContextMenu({ position, items, onClose }: Props) {
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    const handleMouseDown = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) onClose();
    };
    window.addEventListener('keydown', handleKey);
    window.addEventListener('mousedown', handleMouseDown);
    window.addEventListener('scroll', onClose, true);
    return () => {
      window.removeEventListener('keydown', handleKey);
      window.removeEventListener('mousedown', handleMouseDown);
      window.removeEventListener('scroll', onClose, true);
    };
  }, [onClose]);

  return (
    <div
      ref={menuRef}
      className="comment-context-menu"
      role="menu"
      style={{ top: position.top, left: position.left }}
      onContextMenu={(e) => e.preventDefault()}
    >
      {items.map(item => (
        <button
          key={item.label}
          role="menuitem"
          className={`comment-context-menu-item${item.danger ? ' comment-context-menu-danger' : ''}`}
          onClick={() => { item.onSelect(); onClose(); }}
        >
          {item.label}
        </button>
      ))}
    </div>
  );
}
