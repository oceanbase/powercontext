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

import type { Metadata } from 'next';
import { notFound } from 'next/navigation';
import { createRelativeLink } from 'fumadocs-ui/mdx';
import { DocsBody, DocsDescription, DocsPage, DocsTitle } from 'fumadocs-ui/layouts/docs/page';
import { getMDXComponents } from '@/components/mdx';
import { isLanguage, languages } from '@/lib/i18n';
import { source } from '@/lib/source';

interface PageProps {
  params: Promise<{ lang: string; slug?: string[] }>;
}

function getDocumentationPage(lang: string, slug: string[] = []) {
  return source.getPage(['docs', ...slug], lang);
}

export default async function DocumentationPage({ params }: PageProps) {
  const { lang, slug } = await params;
  if (!isLanguage(lang)) notFound();
  const page = getDocumentationPage(lang, slug);
  if (!page) notFound();

  const MDX = page.data.body;
  const isOverview = !slug?.length;
  return (
    <DocsPage full={page.data.full} toc={page.data.toc}>
      {isOverview ? (
        <>
          <DocsTitle>{page.data.title}</DocsTitle>
          <DocsDescription>{page.data.description}</DocsDescription>
        </>
      ) : null}
      <DocsBody>
        <MDX components={getMDXComponents({ a: createRelativeLink(source, page) })} />
      </DocsBody>
    </DocsPage>
  );
}

export function generateStaticParams() {
  return languages.flatMap((lang) =>
    source
      .getPages(lang)
      .filter((page) => page.slugs[0] === 'docs')
      .map((page) => ({ lang, slug: page.slugs.slice(1) })),
  );
}

export async function generateMetadata({ params }: PageProps): Promise<Metadata> {
  const { lang, slug } = await params;
  const page = getDocumentationPage(lang, slug);
  if (!page) notFound();

  return {
    title: page.data.title,
    description: page.data.description,
  };
}
