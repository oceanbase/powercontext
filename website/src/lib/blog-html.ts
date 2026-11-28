/*
 * Copyright (c) 2026 OceanBase.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 * http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

import type { MDXPresetOptions } from 'fumadocs-mdx/config';
import rehypeRaw from 'rehype-raw';

type HastNode = { type: string; tagName?: string; properties?: Record<string, unknown>; children?: HastNode[] };

function eachElement(node: HastNode, visit: (element: HastNode & { tagName: string }) => void) {
  if (node.type === 'element' && node.tagName) visit(node as HastNode & { tagName: string });
  for (const child of node.children ?? []) eachElement(child, visit);
}

/**
 * Raw HTML in a post is trusted (posts are reviewed like code) but must stay
 * static: scripts, styles, frames and event handlers are rejected, so a post
 * renders the same with or without JavaScript and never loads another site.
 */
const FORBIDDEN_ELEMENTS: Record<string, string> = {
  script: 'posts are static HTML',
  style: 'style figures with the figure kit classes, not inline <style>',
  iframe: 'posts do not embed other pages',
  object: 'posts do not embed other pages',
  embed: 'posts do not embed other pages',
  link: 'posts do not load stylesheets',
};

export class PostContentError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'PostContentError';
  }
}

export function rehypeStaticHtml() {
  return (tree: HastNode) => {
    eachElement(tree, (node) => {
      const advice = FORBIDDEN_ELEMENTS[node.tagName];
      if (advice) throw new PostContentError(`<${node.tagName}> is not allowed in blog posts: ${advice}.`);
      for (const name of Object.keys(node.properties ?? {})) {
        if (/^on[A-Z]/.test(name)) {
          throw new PostContentError(`<${node.tagName}> has an event handler (${name}); blog posts are static.`);
        }
      }
    });
  };
}

/** MDX node types rehype-raw must leave alone. */
const mdxNodeTypes = ['mdxFlowExpression', 'mdxJsxFlowElement', 'mdxJsxTextElement', 'mdxTextExpression', 'mdxjsEsm'];

/** Parse raw HTML written in post Markdown, then reject anything that is not static. */
export const blogHtmlPlugins: NonNullable<MDXPresetOptions['rehypePlugins']> = [[rehypeRaw, { passThrough: mdxNodeTypes }], rehypeStaticHtml];
