import { visit, SKIP } from 'unist-util-visit';
import type { Plugin } from 'unified';
import type { Root } from 'mdast';

const CALLOUT_TYPES = new Set([
  'note', 'warning', 'tip', 'caution', 'important', 'danger', 'info',
]);

const DIRECTIVE_PREFIX: Record<string, string> = {
  textDirective: ':',
  leafDirective: '::',
  containerDirective: ':::',
};

/**
 * Remark plugin that transforms container directives into callout divs.
 * Works with remark-directive. Syntax:
 *   :::note
 *   Content here.
 *   :::
 *
 * Any other directive node (text/leaf/non-callout container) is converted
 * back to plain text. Without this, patterns like Swedish "AI:n" — which
 * remark-directive parses as a `:n` text directive — render as empty
 * elements and break the surrounding text flow.
 */
export const remarkCallouts: Plugin<[], Root> = () => {
  return (tree) => {
    visit(tree, (node: any, index, parent: any) => {
      if (node.type === 'containerDirective' && CALLOUT_TYPES.has(node.name)) {
        const data = node.data ?? (node.data = {});
        const type = node.name as string;
        data.hName = 'div';
        data.hProperties = {
          ...(data.hProperties ?? {}),
          className: `callout callout-${type}`,
          'data-callout': type,
        };
        return;
      }

      const prefix = DIRECTIVE_PREFIX[node.type];
      if (prefix && parent && typeof index === 'number') {
        parent.children.splice(index, 1, {
          type: 'text',
          value: prefix + node.name,
        });
        return [SKIP, index];
      }
    });
  };
};
