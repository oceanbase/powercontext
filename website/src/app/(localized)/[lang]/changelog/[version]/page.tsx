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

import { ArrowUpRight } from 'lucide-react';
import Link from 'next/link';
import { notFound } from 'next/navigation';
import { HomeLayout } from 'fumadocs-ui/layouts/home';
import { BackLink, Dateline, editorial as styles } from '@/components/editorial';
import { SiteFooter } from '@/components/site-footer';
import { buttonClasses } from '@astrafoundry/design/ui';
import { isLanguage, languages } from '@/lib/i18n';
import { releases } from '@/lib/releases';

/** Keeps a title's middots at the end of a line rather than starting the next. */
const title = (text: string) => text.replaceAll(' · ', '\u00a0· ');
import { baseOptions } from '@/lib/site';
import { pageMetadata } from '@/lib/metadata';

const labels = {
  en: { back: 'Changelog', highlights: 'Highlights', install: 'Install', github: 'View the full GitHub release' },
  zh: { back: '更新日志', highlights: '主要变化', install: '安装', github: '查看完整 GitHub Release' },
} as const;

export default async function ReleasePage({
  params,
}: {
  params: Promise<{ lang: string; version: string }>;
}) {
  const { lang, version } = await params;
  if (!isLanguage(lang)) notFound();
  const release = releases.find((item) => item.version === version);
  if (!release) notFound();
  const text = labels[lang];

  return (
    <HomeLayout {...baseOptions(lang)}>
      <main>
        <article className={styles.article}>
          <header className={styles.articleHeader}>
            <BackLink href={`/${lang}/changelog`}>{text.back}</BackLink>
            <Dateline date={release.date} labels={[<span className={styles.version} key="v">{release.version}</span>]} lang={lang} />
            <h1 className={styles.postTitle}>{title(release.title[lang])}</h1>
            <p className={styles.postSummary}>{release.summary[lang]}</p>
          </header>
          <div className={styles.prose}>
            <h2 id="highlights">{text.highlights}</h2>
            <ul>
              {release.changes[lang].map((change) => <li key={change}>{change}</li>)}
            </ul>
            <h2 id="install">{text.install}</h2>
            <pre className={styles.command}><code>{release.installCommand}</code></pre>
            <div className={styles.links}>
              <Link className={buttonClasses('secondary', 'md')} href={release.githubUrl}>
                {text.github} <ArrowUpRight aria-hidden="true" strokeWidth={1.75} />
              </Link>
            </div>
          </div>
        </article>
      </main>
      <SiteFooter lang={lang} />
    </HomeLayout>
  );
}

export function generateStaticParams() {
  return languages.flatMap((lang) => releases.map((release) => ({ lang, version: release.version })));
}

export async function generateMetadata({ params }: { params: Promise<{ lang: string; version: string }> }) {
  const { lang, version } = await params;
  if (!isLanguage(lang)) notFound();
  const release = releases.find((item) => item.version === version);
  if (!release) notFound();
  return pageMetadata({
    path: `/${lang}/changelog/${version}/`, lang,
    title: `${version} · ${release.title[lang]}`, description: release.summary[lang],
    translations: { en: `/en/changelog/${version}/`, zh: `/zh/changelog/${version}/` },
  });
}
