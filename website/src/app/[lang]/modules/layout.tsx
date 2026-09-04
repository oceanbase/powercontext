import type { ReactNode } from 'react';
import { notFound } from 'next/navigation';
import { DocsLayout } from 'fumadocs-ui/layouts/docs';
import { isLanguage } from '@/lib/i18n';
import { getPythonSource } from '@/lib/python-source';
import { baseOptions } from '@/lib/site';

export default async function PythonApiLayout({
  children,
  params,
}: {
  children: ReactNode;
  params: Promise<{ lang: string }>;
}) {
  const { lang } = await params;
  if (!isLanguage(lang)) notFound();
  const pythonSource = getPythonSource(lang);

  return (
    <DocsLayout tree={pythonSource.getPageTree()} {...baseOptions(lang)}>
      {children}
    </DocsLayout>
  );
}
