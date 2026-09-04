import { notFound } from 'next/navigation';
import type { ReactNode } from 'react';
import { Provider } from '@/components/provider';
import { isLanguage, languages } from '@/lib/i18n';

export function generateStaticParams() {
  return languages.map((lang) => ({ lang }));
}

export default async function LanguageLayout({
  children,
  params,
}: {
  children: ReactNode;
  params: Promise<{ lang: string }>;
}) {
  const { lang } = await params;
  if (!isLanguage(lang)) notFound();

  return <Provider lang={lang}>{children}</Provider>;
}
