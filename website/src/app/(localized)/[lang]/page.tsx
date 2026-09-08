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
import { HomePage } from '@/components/home-page';
import { defaultLanguage, isLanguage } from '@/lib/i18n';

type PageProps = { params: Promise<{ lang: string }> };

export default async function LocalizedHomePage({ params }: PageProps) {
  const { lang } = await params;
  if (!isLanguage(lang)) notFound();

  return <HomePage lang={lang} />;
}

export async function generateMetadata({ params }: PageProps): Promise<Metadata> {
  const { lang } = await params;
  if (!isLanguage(lang)) notFound();

  return {
    alternates: {
      canonical: lang === defaultLanguage ? '/' : `/${lang}/`,
      languages: { en: '/', zh: '/zh/', 'x-default': '/' },
    },
  };
}
