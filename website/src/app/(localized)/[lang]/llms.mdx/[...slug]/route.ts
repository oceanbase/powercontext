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

import { notFound } from 'next/navigation';
import { isLanguage } from '@/lib/i18n';
import { docsLlms, llmsSource } from '@/lib/llms';
import { getPageMarkdownUrl } from '@/lib/markdown';

export const dynamic = 'force-static';
export const dynamicParams = false;

export function generateStaticParams() {
  return llmsSource.generateParams().map((item) => ({
    ...item, slug: getPageMarkdownUrl({ slugs: item.slug, locale: item.lang }).segments,
  }));
}

export async function GET(_request: Request, { params }: { params: Promise<{ lang: string; slug: string[] }> }) {
  const { lang, slug } = await params;
  if (!isLanguage(lang) || slug.at(-1) !== 'content.md') notFound();
  const page = llmsSource.getPage(slug.slice(0, -1), lang);
  if (!page) notFound();
  return new Response(await docsLlms.page(page), { headers: { 'Content-Type': 'text/markdown; charset=utf-8' } });
}
