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
import { DocsPage, DocsTitle } from 'fumadocs-ui/layouts/docs/page';
import { OpenAPIPage } from '@/components/openapi-page';
import { apiSource } from '@/lib/openapi-source';

interface PageProps {
  params: Promise<{ slug: string[] }>;
}

export default async function ApiOperationPage({ params }: PageProps) {
  const { slug } = await params;
  const page = apiSource.getPage(slug);
  if (!page) notFound();

  return (
    <DocsPage full toc={page.data.toc}>
      <DocsTitle>{page.data.title}</DocsTitle>
      <OpenAPIPage {...page.data.getOpenAPIPageProps()} />
    </DocsPage>
  );
}

export function generateStaticParams() {
  return apiSource.generateParams();
}

export async function generateMetadata({ params }: PageProps): Promise<Metadata> {
  const { slug } = await params;
  const page = apiSource.getPage(slug);
  if (!page) notFound();
  return { title: page.data.title, description: page.data.description };
}
