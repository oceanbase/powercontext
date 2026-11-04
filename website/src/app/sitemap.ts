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

import type { MetadataRoute } from 'next';
import { blog, blogRoutes } from '@/lib/blog';
import { languages } from '@/lib/i18n';
import { canonicalPath } from '@/lib/metadata';
import { apiSource } from '@/lib/openapi-source';
import { getPythonSource } from '@/lib/python-source';
import { releases } from '@/lib/releases';
import { source } from '@/lib/source';
import { absoluteSiteUrl } from '@/lib/urls';

export const dynamic = 'force-static';

export default function sitemap(): MetadataRoute.Sitemap {
  const entries = new Map<string, MetadataRoute.Sitemap[number]>();
  function add(path: string, lastModified?: string) {
    const url = absoluteSiteUrl(canonicalPath(path));
    entries.set(url, { url, ...(lastModified ? { lastModified } : {}) });
  }
  add('/api/');
  for (const page of apiSource.getPages()) add(page.url);
  for (const lang of languages) {
    add(`/${lang}/`);
    add(`/${lang}/benchmarks/`);
    add(`/${lang}/changelog/`);
    add(`/${lang}/modules/`);
    for (const release of releases) add(`/${lang}/changelog/${release.version}/`, release.date);
    for (const page of source.getPages(lang)) add(page.url);
    for (const page of getPythonSource(lang).getPages()) add(page.url);
    for (const slug of blogRoutes(lang)) {
      const post = slug.length === 1 ? blog[lang].posts.find((item) => item.slug === slug[0]) : undefined;
      add(`/${lang}/blog/${slug.join('/')}`, post?.updated ?? post?.date);
    }
  }
  return [...entries.values()].sort((a, b) => a.url.localeCompare(b.url));
}
