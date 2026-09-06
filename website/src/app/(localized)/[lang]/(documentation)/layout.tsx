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

import type { ReactNode } from 'react';
import { notFound } from 'next/navigation';
import type { Root } from 'fumadocs-core/page-tree';
import { DocsLayout } from 'fumadocs-ui/layouts/docs';
import { isLanguage } from '@/lib/i18n';
import { baseOptions } from '@/lib/site';
import { source } from '@/lib/source';

function getDocumentationTree(lang: string): Root {
  const tree = source.getPageTree(lang);
  const documentation = tree.children.find(
    (node) => node.type === 'folder' && node.index?.url.replace(/\/$/, '') === `/${lang}/docs`,
  );
  const rfcs = tree.children.find(
    (node) => node.type === 'folder' && (
      node.index?.url.replace(/\/$/, '') === `/${lang}/rfcs`
      || node.children.some(
        (child) => child.type === 'page' && child.url.startsWith(`/${lang}/rfcs/`),
      )
    ),
  );

  if (!documentation || documentation.type !== 'folder') return tree;
  if (!rfcs || rfcs.type !== 'folder') {
    return { ...tree, children: documentation.children };
  }

  const referenceIndex = documentation.children.findIndex(
    (node) => node.type === 'folder' && node.children.some(
      (child) => child.type === 'page' && child.url.startsWith(`/${lang}/docs/reference/`),
    ),
  );
  const children = [...documentation.children];
  children.splice(referenceIndex === -1 ? children.length : referenceIndex, 0, rfcs);

  return {
    ...tree,
    children,
  };
}

export default async function DocumentationLayout({
  children,
  params,
}: {
  children: ReactNode;
  params: Promise<{ lang: string }>;
}) {
  const { lang } = await params;
  if (!isLanguage(lang)) notFound();

  return (
    <DocsLayout tree={getDocumentationTree(lang)} {...baseOptions(lang)}>
      {children}
    </DocsLayout>
  );
}
