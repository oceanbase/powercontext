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

import { MarkdownCopyButton } from 'fumadocs-ui/layouts/docs/page';
import { getPageMarkdownUrl } from '@/lib/markdown';
import { source } from '@/lib/source';
import { absoluteSiteUrl, withBasePath } from '@/lib/urls';
import { StructuredData } from './structured-data';

export function ContentPageActions({ page }: { page: ReturnType<typeof source.getPages>[number] }) {
  const lang = page.locale ?? 'en';
  const parents = page.slugs.flatMap((_, index) => {
    const parent = source.getPage(page.slugs.slice(0, index + 1), lang);
    return parent ? [{ name: parent.data.title, url: absoluteSiteUrl(`${parent.url.replace(/\/$/, '')}/`) }] : [];
  });
  const breadcrumbs = [{ name: 'PowerContext', url: absoluteSiteUrl(lang === 'en' ? '/' : '/zh/') }, ...parents];
  const markdown = withBasePath(getPageMarkdownUrl(page).url);
  return <>
    <StructuredData data={{
      '@context': 'https://schema.org', '@type': 'BreadcrumbList',
      itemListElement: breadcrumbs.map((item, index) => ({
        '@type': 'ListItem', position: index + 1, name: item.name, item: item.url,
      })),
    }} />
    <div className="mb-4 flex items-center gap-3 text-sm text-fd-muted-foreground">
      <MarkdownCopyButton markdownUrl={markdown} />
      <a href={markdown} className="underline underline-offset-4">{lang === 'zh' ? '查看 Markdown' : 'View Markdown'}</a>
    </div>
  </>;
}
