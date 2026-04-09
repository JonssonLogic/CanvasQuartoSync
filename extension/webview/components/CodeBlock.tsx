import { useCallback } from 'react';
import type { ComponentPropsWithoutRef } from 'react';

interface Props extends ComponentPropsWithoutRef<'code'> {
  language: string;
}

export default function CodeBlock({ language, className, children }: Props) {
  const copyToClipboard = useCallback(() => {
    const text = typeof children === 'string' ? children : '';
    navigator.clipboard.writeText(text).catch(() => {});
  }, [children]);

  return (
    <div className="code-block-wrapper">
      {language ? (
        <div className="code-block-header">
          <span className="code-language-badge">{language}</span>
          <button className="code-copy-btn" onClick={copyToClipboard} title="Copy code">
            Copy
          </button>
        </div>
      ) : null}
      <pre className={className}>
        <code className={className}>{children}</code>
      </pre>
    </div>
  );
}
