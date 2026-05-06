import { useEffect, useState, useRef } from 'react';
import mermaid from 'mermaid';

let idCounter = 0;

interface Props {
  chart: string;
}

export default function MermaidBlock({ chart }: Props) {
  const [svg, setSvg] = useState<string>('');
  const [error, setError] = useState<string | null>(null);
  const idRef = useRef(`mermaid-${idCounter++}`);

  useEffect(() => {
    let cancelled = false;

    mermaid.initialize({
      startOnLoad: false,
      theme: 'default',
      securityLevel: 'loose',
    });

    const id = `${idRef.current}-${Date.now()}`;

    mermaid
      .render(id, chart)
      .then(({ svg }) => {
        if (!cancelled) { setSvg(svg); setError(null); }
      })
      .catch((err) => {
        if (!cancelled) setError(String(err));
      });

    return () => { cancelled = true; };
  }, [chart]);

  if (error) {
    return (
      <div className="mermaid-error">
        <pre>{chart}</pre>
        <p className="mermaid-error-msg">Diagram error: {error}</p>
      </div>
    );
  }

  if (!svg) return <div className="mermaid-block mermaid-loading">Rendering diagram...</div>;

  return (
    <div
      className="mermaid-block"
      dangerouslySetInnerHTML={{ __html: svg }}
    />
  );
}
