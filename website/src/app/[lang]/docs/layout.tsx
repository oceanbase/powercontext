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

  if (!documentation || documentation.type !== 'folder') return tree;

  return {
    ...tree,
    children: documentation.children,
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
