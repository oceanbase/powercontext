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

import { ArrowRight, ExternalLink } from 'lucide-react';
import Link from 'next/link';
import { notFound } from 'next/navigation';
import { Card, Cards } from 'fumadocs-ui/components/card';
import { HomeLayout } from 'fumadocs-ui/layouts/home';
import { DocsDescription, DocsTitle } from 'fumadocs-ui/layouts/docs/page';
import { SiteFooter } from '@/components/site-footer';
import { isLanguage } from '@/lib/i18n';
import { releases } from '@/lib/releases';
import { baseOptions } from '@/lib/site';

const labels = {
  en: {
    title: 'Changelog',
    description: 'Tagged PowerContext releases and changes that affect users. Design proposals remain in RFCs until they ship.',
    read: 'Read more',
    github: 'GitHub release',
  },
  zh: {
    title: '更新日志',
    description: '这里记录 PowerContext 的正式版本，以及会影响使用方式的变化。尚未交付的设计仍保留在 RFC 中。',
    read: '阅读更多',
    github: 'GitHub Release',
  },
} as const;

export default async function ChangelogPage({ params }: { params: Promise<{ lang: string }> }) {
  const { lang } = await params;
  if (!isLanguage(lang)) notFound();
  const text = labels[lang];

  return (
    <HomeLayout {...baseOptions(lang)}>
      <main className="mx-auto w-full max-w-(--fd-layout-width) px-4 py-16">
        <div className="max-w-3xl">
          <header>
            <DocsTitle>{text.title}</DocsTitle>
            <DocsDescription>{text.description}</DocsDescription>
          </header>
          <Cards className="grid-cols-1">
            {releases.map((release) => (
              <Card
                key={release.version}
                title={`${release.version} · ${release.title[lang]}`}
              >
                <div className="space-y-3">
                  <time className="block text-xs text-fd-muted-foreground" dateTime={release.date}>{release.date}</time>
                  <p>{release.summary[lang]}</p>
                  <div className="flex flex-wrap gap-4 text-sm text-fd-primary">
                    <Link className="inline-flex items-center gap-1 hover:underline" href={`/${lang}/changelog/${release.version}`}>
                      {text.read} <ArrowRight aria-hidden="true" className="size-4" />
                    </Link>
                    <Link className="inline-flex items-center gap-1 hover:underline" href={release.githubUrl}>
                      {text.github} <ExternalLink aria-hidden="true" className="size-4" />
                    </Link>
                  </div>
                </div>
              </Card>
            ))}
          </Cards>
        </div>
      </main>
      <SiteFooter lang={lang} />
    </HomeLayout>
  );
}
