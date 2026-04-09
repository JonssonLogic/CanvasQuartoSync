import { useState } from 'react';

interface TabsetProps {
  children: React.ReactNode;
}

export default function TabsetBlock({ children }: TabsetProps) {
  const [activeIndex, setActiveIndex] = useState(0);

  const panels: { label: string; content: React.ReactNode }[] = [];
  const childArray = Array.isArray(children) ? children : [children];

  for (const child of childArray) {
    if (child && typeof child === 'object' && 'props' in child) {
      const props = child.props as Record<string, unknown>;
      if (props['data-tab-label']) {
        panels.push({
          label: String(props['data-tab-label']),
          content: props.children as React.ReactNode,
        });
      }
    }
  }

  if (panels.length === 0) return <div>{children}</div>;

  return (
    <div className="quarto-tabset">
      <div className="quarto-tabset-tabs" role="tablist">
        {panels.map((panel, i) => (
          <button
            key={i}
            role="tab"
            aria-selected={i === activeIndex}
            className={`quarto-tabset-tab${i === activeIndex ? ' active' : ''}`}
            onClick={() => setActiveIndex(i)}
          >
            {panel.label}
          </button>
        ))}
      </div>
      <div className="quarto-tabset-content" role="tabpanel">
        {panels[activeIndex]?.content}
      </div>
    </div>
  );
}
