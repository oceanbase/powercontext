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

import { CopyPageButton } from './copy-page-button';
import { getPageMarkdownUrl } from '@/lib/markdown';
import { source } from '@/lib/source';
import { isLanguage } from '@/lib/i18n';
import { absoluteSiteUrl, withBasePath } from '@/lib/urls';
import { StructuredData } from './structured-data';

type ContentPage = ReturnType<typeof source.getPages>[number];

/** The page's breadcrumb trail as structured data. */
export function ContentPageActions({ page }: { page: ContentPage }) {
  const lang = page.locale ?? 'en';
  const parents = page.slugs.flatMap((_, index) => {
    const parent = source.getPage(page.slugs.slice(0, index + 1), lang);
    return parent ? [{ name: parent.data.title, url: absoluteSiteUrl(`${parent.url.replace(/\/$/, '')}/`) }] : [];
  });
  const breadcrumbs = [{ name: 'PowerContext', url: absoluteSiteUrl(lang === 'en' ? '/' : '/zh/') }, ...parents];
  return (
    <StructuredData data={{
      '@context': 'https://schema.org', '@type': 'BreadcrumbList',
      itemListElement: breadcrumbs.map((item, index) => ({
        '@type': 'ListItem', position: index + 1, name: item.name, item: item.url,
      })),
    }} />
  );
}

/**
 * DocsPage options: no breadcrumb kicker over the title, and the one page
 * action, Copy page, at the top of the table of contents and in the phone
 * contents popover.
 */
export function pageActionOptions(page: ContentPage) {
  const lang = page.locale && isLanguage(page.locale) ? page.locale : 'en';
  const button = <CopyPageButton lang={lang} markdownUrl={withBasePath(getPageMarkdownUrl(page).url)} />;
  return {
    // The sidebar already shows where the page sits; no breadcrumb over the title.
    breadcrumb: { enabled: false },
    tableOfContent: { header: <div className="pc-toc-actions">{button}</div> },
    tableOfContentPopover: { footer: <div className="pc-toc-actions">{button}</div> },
  };
}
