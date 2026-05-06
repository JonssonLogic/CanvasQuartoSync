import { visit } from 'unist-util-visit';
import type { Plugin } from 'unified';
import type { Root } from 'mdast';

const CALLOUT_TYPES = new Set([
  'note', 'warning', 'tip', 'caution', 'important', 'danger', 'info',
]);

/**
 * Remark plugin that transforms container directives into callout divs.
 * Works with remark-directive. Syntax: :::note ... :::
 */
export const remarkCallouts: Plugin<[], Root> = () => {
  return (tree) => {
    visit(tree, (node: any) => {
      if (node.type === 'containerDirective' && CALLOUT_TYPES.has(node.name)) {
        const data = node.data ?? (node.data = {});
        const type = node.name as string;
        data.hName = 'div';
        data.hProperties = {
          ...(data.hProperties ?? {}),
          className: `callout callout-${type}`,
          'data-callout': type,
        };
      }
    });
  };
};
