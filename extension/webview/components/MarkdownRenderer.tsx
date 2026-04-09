import { useMemo } from 'react';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math';
import remarkDirective from 'remark-directive';
import rehypeKatex from 'rehype-katex';
import rehypeSlug from 'rehype-slug';
import rehypeRaw from 'rehype-raw';
import rehypeHighlight from 'rehype-highlight';
import { remarkCallouts } from '../preprocessing/remarkCallouts';
import CodeBlock from './CodeBlock';
import MermaidBlock from './MermaidBlock';
import TabsetBlock from './TabsetBlock';
import 'katex/dist/katex.min.css';

// VS Code API — set by App.tsx on init
let _vscodeApi: { postMessage(msg: any): void } | undefined;
export function setVsCodeApi(api: { postMessage(msg: any): void }) {
  _vscodeApi = api;
}

interface Props {
  content: string;
  imageMap: Record<string, string>;
  onCommentClick?: (commentId: string, rect: DOMRect) => void;
}

function resolveImageSrc(src: string, imageMap: Record<string, string>): string {
  if (!src) return src;
  if (/^(https?:|data:|blob:)/i.test(src)) return src;
  return imageMap[src] || src;
}

function handleLinkClick(e: React.MouseEvent, href: string) {
  e.preventDefault();
  if (href.startsWith('#')) {
    // Internal anchor — scroll within the preview
    const el = document.getElementById(href.slice(1));
    if (el) el.scrollIntoView({ behavior: 'smooth' });
  } else if (/^https?:\/\//i.test(href)) {
    // External URL — open in VS Code's browser
    if (_vscodeApi) _vscodeApi.postMessage({ type: 'openExternal', url: href });
  }
}

export default function MarkdownRenderer({ content, imageMap, onCommentClick }: Props) {
  const remarkPlugins = useMemo(
    () => [remarkGfm, remarkMath, remarkDirective, remarkCallouts],
    []
  );

  const rehypePlugins = useMemo(
    () => [rehypeRaw, rehypeKatex, rehypeSlug, rehypeHighlight],
    []
  );

  const components = useMemo(
    () => ({
      code({ className, children, node, ...props }: any) {
        const match = /language-(\w+)/.exec(className ?? '');
        const language = match?.[1] ?? '';

        // Check if this code is inside a <pre> (block code vs inline code)
        const isBlock = node?.position && String(children).includes('\n') || language;

        if (language === 'mermaid') {
          return <MermaidBlock chart={String(children).trimEnd()} />;
        }

        if (language) {
          return (
            <CodeBlock language={language} className={className} {...props}>
              {children}
            </CodeBlock>
          );
        }

        // Block code without a language — render as a plain code block
        if (isBlock) {
          return (
            <CodeBlock language="" className={className} {...props}>
              {children}
            </CodeBlock>
          );
        }

        // Inline code
        return <code className={className} {...props}>{children}</code>;
      },

      pre({ children }: any) {
        return <>{children}</>;
      },

      img({ src, alt, ...props }: any) {
        const resolved = resolveImageSrc(src ?? '', imageMap);
        if (alt) {
          return (
            <figure>
              <img src={resolved} alt={alt} {...props} />
              <figcaption>{alt}</figcaption>
            </figure>
          );
        }
        return <img src={resolved} alt="" {...props} />;
      },

      video({ src, children, ...props }: any) {
        const resolved = src ? resolveImageSrc(src, imageMap) : undefined;
        return (
          <video controls {...props} src={resolved}>
            {children}
          </video>
        );
      },

      source({ src, ...props }: any) {
        const resolved = src ? resolveImageSrc(src, imageMap) : undefined;
        return <source {...props} src={resolved} />;
      },

      // Comment highlights
      mark({ className, children, ...props }: any) {
        const commentId = props['data-comment-id'];
        if (className === 'comment-highlight' && commentId && onCommentClick) {
          return (
            <mark className="comment-highlight" data-comment-id={commentId}
              onClick={(e) => {
                e.stopPropagation();
                const rect = (e.target as HTMLElement).getBoundingClientRect();
                onCommentClick(commentId, rect);
              }}>
              {children}
              <span className="comment-indicator" />
            </mark>
          );
        }
        return <mark className={className} {...props}>{children}</mark>;
      },

      // Links: external URLs open in VS Code browser, anchors scroll in preview
      a({ href, children, ...props }: any) {
        return (
          <a
            href={href ?? '#'}
            onClick={(e) => handleLinkClick(e, href ?? '')}
            {...props}
          >
            {children}
          </a>
        );
      },

      div({ className, children, ...props }: any) {
        if (className === 'panel-tabset') {
          return <TabsetBlock>{children}</TabsetBlock>;
        }
        return <div className={className} {...props}>{children}</div>;
      },
    }),
    [imageMap, onCommentClick]
  );

  return (
    <div className="markdown-body">
      <Markdown
        remarkPlugins={remarkPlugins}
        rehypePlugins={rehypePlugins as any}
        components={components}
      >
        {content}
      </Markdown>
    </div>
  );
}
