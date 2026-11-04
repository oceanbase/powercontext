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

import { createGetUrl, llms, loader } from 'fumadocs-core/source';
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

export const docsLlms = llms(llmsSource, {
  renderPage: async (page) => {
    const markdown = await page.data.getText('processed');
    const body = markdown.replace(/^# [^\n]+\n+/, '');
    const url = absoluteSiteUrl(`${getHtmlUrl(page.slugs, page.locale).replace(/\/$/, '')}/`);
    return `# ${page.data.title} (${url})\n\n${body}`;
  },
});
