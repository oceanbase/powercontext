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

import { ArrowLeft, ExternalLink } from 'lucide-react';
import Link from 'next/link';
import { notFound } from 'next/navigation';
import { HomeLayout } from 'fumadocs-ui/layouts/home';
import { buttonVariants } from 'fumadocs-ui/components/ui/button';
import { SiteFooter } from '@/components/site-footer';
import { isLanguage, languages } from '@/lib/i18n';
import { releases } from '@/lib/releases';
import { baseOptions } from '@/lib/site';

export default async function ReleasePage({
  params,
}: {
  params: Promise<{ lang: string; version: string }>;
}) {
  const { lang, version } = await params;
  if (!isLanguage(lang)) notFound();
  const release = releases.find((item) => item.version === version);
  if (!release) notFound();

  return (
    <HomeLayout {...baseOptions(lang)}>
      <main className="mx-auto w-full max-w-(--fd-layout-width) px-4 py-16">
        <article className="prose max-w-3xl">
          <Link className="inline-flex items-center gap-1 text-sm text-fd-primary no-underline hover:underline" href={`/${lang}/changelog`}>
            <ArrowLeft aria-hidden="true" className="size-4" /> {lang === 'zh' ? '返回更新日志' : 'Back to changelog'}
          </Link>
          <time className="mt-10 block text-sm text-fd-muted-foreground" dateTime={release.date}>{release.date}</time>
          <h1 className="mt-2">{release.version} · {release.title[lang]}</h1>
          <p className="lead">{release.summary[lang]}</p>
          <h2>{lang === 'zh' ? '主要变化' : 'Highlights'}</h2>
          <ul>
            {release.changes[lang].map((change) => <li key={change}>{change}</li>)}
          </ul>
          <pre className="overflow-x-auto"><code>{release.installCommand}</code></pre>
          <Link className={buttonVariants({ variant: 'outline' })} href={release.githubUrl}>
            {lang === 'zh' ? '查看完整 GitHub Release' : 'View the full GitHub release'}
            <ExternalLink aria-hidden="true" className="size-4" />
          </Link>
        </article>
      </main>
      <SiteFooter lang={lang} />
    </HomeLayout>
  );
}

export function generateStaticParams() {
  return languages.flatMap((lang) => releases.map((release) => ({ lang, version: release.version })));
}
