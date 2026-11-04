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

import { createGetUrl, llms, loader, PathUtils } from 'fumadocs-core/source';
import { fromMarkdown } from 'mdast-util-from-markdown';
import { toMarkdown } from 'mdast-util-to-markdown';
import type { Nodes } from 'mdast';
import { posts } from './blog-source';
import { i18n } from './i18n';
import { getPageMarkdownUrl } from './markdown';
import { siteDescriptions } from './metadata';
import { docs } from './source';
import { absoluteSiteUrl } from './urls';

const blogCollection = posts.toFumadocsSource();
const blogInput: typeof blogCollection = {
  ...blogCollection,
  files: blogCollection.files.map((file) => ({
    ...file,
    // Keep the locale directory first for the source loader's i18n parser.
    path: file.path.replace(/^([^/]+)\//, '$1/blog/'),
  })),
};

export const llmsSource = loader({
  baseUrl: '/',
  i18n,
  source: { docs: docs.toFumadocsSource(), blog: blogInput },
  url: (slugs, locale) => absoluteSiteUrl(getPageMarkdownUrl({ slugs, locale }).url),
  pageTree: {
    transformers: [{
      root(root) {
        return { ...root, name: 'PowerContext', description: siteDescriptions[this.locale === 'zh' ? 'zh' : 'en'] };
      },
    }],
  },
});

const getHtmlUrl = createGetUrl('/', i18n);

function resolveMarkdownLinks(markdown: string, page: typeof llmsSource.$inferPage) {
  const localePrefix = page.locale ? `${page.locale}/` : '';
  const path = localePrefix && page.path.startsWith(localePrefix) ? page.path.slice(localePrefix.length) : page.path;
  const dir = PathUtils.dirname(path);
  const edits: Array<{ start: number; end: number; value: string }> = [];

  function resolve(href: string) {
    if (!href || /^(?:[a-z][a-z\d+.-]*:|\/\/|#)/i.test(href)) return href;
    if (href.startsWith('/')) return absoluteSiteUrl(href);

    const [, pathname, suffix] = /^([^?#]*)(.*)$/.exec(href)!;
    const target = llmsSource.getPageByHref(pathname.startsWith('.') ? pathname : `./${pathname}`, {
      dir,
      language: page.locale,
    });
    return target ? `${target.page.url}${suffix}` : href;
  }

  function visit(node: Nodes, nested = false): boolean {
    const resource = node.type === 'link' || node.type === 'image' || node.type === 'definition';
    let changed = false;
    if ('children' in node) {
      for (const child of node.children) changed = visit(child, nested || resource) || changed;
    }
    if (resource) {
      const url = resolve(node.url);
      changed = changed || url !== node.url;
      node.url = url;
      const start = node.position?.start.offset;
      const end = node.position?.end.offset;
      // Keep surrounding Markdown, code blocks, and authored MDX unchanged.
      if (changed && !nested && start !== undefined && end !== undefined) {
        edits.push({ start, end, value: toMarkdown(node).trimEnd() });
      }
    }
    return changed;
  }

  visit(fromMarkdown(markdown));
  for (const edit of edits.reverse()) {
    markdown = markdown.slice(0, edit.start) + edit.value + markdown.slice(edit.end);
  }
  return markdown;
}

export const docsLlms = llms(llmsSource, {
  renderPage: async (page) => {
    const markdown = await page.data.getText('processed');
    const body = resolveMarkdownLinks(markdown.replace(/^# [^\n]+\n+/, ''), page);
    const url = absoluteSiteUrl(`${getHtmlUrl(page.slugs, page.locale).replace(/\/$/, '')}/`);
    return `# ${page.data.title} (${url})\n\n${body}`;
  },
});
