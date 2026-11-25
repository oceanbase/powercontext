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

import type { TOCItemType } from 'fumadocs-core/toc';
import { ArrowUpRight, ChevronRight } from 'lucide-react';
import { notFound } from 'next/navigation';
import { DynamicCodeBlock } from 'fumadocs-ui/components/dynamic-codeblock';
import { DocsBody, DocsDescription, DocsPage, DocsTitle } from 'fumadocs-ui/layouts/docs/page';
import { isLanguage, languages, type Language } from '@/lib/i18n';
import { pageMetadata } from '@/lib/metadata';
import { releaseAnchor, releases, type Release } from '@/lib/releases';

const text = {
  en: {
    title: 'Changelog',
    description: 'Stable PowerContext releases and the changes that affect you.',
    prereleases: 'Alpha, beta and RC versions are on',
    details: 'View details',
    github: 'GitHub release',
  },
  zh: {
    title: '更新日志',
    description: 'PowerContext 的正式版本，以及会影响使用方式的变化。',
    prereleases: 'alpha、beta 和 RC 版本请查看',
    details: '查看详情',
    github: 'GitHub Release',
  },
} as const;

const monthKey = (date: string) => date.slice(0, 7);

function monthLabel(key: string, lang: Language) {
  return new Intl.DateTimeFormat(lang === 'zh' ? 'zh-CN' : 'en-US', { year: 'numeric', month: 'long', timeZone: 'UTC' })
    .format(new Date(`${key}-01T00:00:00Z`));
}

/** Releases grouped by the month they shipped, newest first. */
function byMonth() {
  const groups = new Map<string, Release[]>();
  for (const release of releases) {
    const key = monthKey(release.date);
    groups.set(key, [...(groups.get(key) ?? []), release]);
  }
  return [...groups];
}

/** Keeps a title's middots at the end of a line rather than starting the next. */
const keepSeparators = (value: string) => value.replaceAll(' · ', ' · ');

/**
 * The changelog is a documentation page: releases grouped by month, whose
 * headings make the table of contents. Each release shows its install
 * command and keeps the full list of changes behind "View details".
 */
export default async function ChangelogPage({ params }: { params: Promise<{ lang: string }> }) {
  const { lang } = await params;
  if (!isLanguage(lang)) notFound();
  const copy = text[lang];
  const months = byMonth();
  const toc: TOCItemType[] = months.map(([key]) => ({ title: monthLabel(key, lang), url: `#month-${key}`, depth: 2 }));

  return (
    <DocsPage breadcrumb={{ enabled: false }} toc={toc}>
      <DocsTitle>{copy.title}</DocsTitle>
      <DocsDescription>
        {copy.description} {copy.prereleases}{' '}
        <a className="underline decoration-1 underline-offset-[0.2em]" href="https://github.com/oceanbase/powercontext/releases">
          GitHub Releases
        </a>
      </DocsDescription>
      <DocsBody className="pc-changelog">
        {months.map(([key, items]) => (
          <section aria-labelledby={`month-${key}`} key={key}>
            <h2 id={`month-${key}`}>{monthLabel(key, lang)}</h2>
            {items.map((release, index) => (
              <article className="pc-release" id={releaseAnchor(release.version)} key={release.version}>
                <time className="pc-release-date" dateTime={release.date}>{release.date}</time>
                <h3>PowerContext <span className="pc-release-version">{release.version.replace(/^v/, '')}</span></h3>
                <p className="pc-release-title">{keepSeparators(release.title[lang])}</p>
                <p>{release.summary[lang]}</p>
                <DynamicCodeBlock code={release.installCommand} lang="bash" />
                <details className="pc-release-details" open={key === months[0][0] && index === 0}>
                  <summary>
                    <ChevronRight aria-hidden="true" className="pc-release-chevron" />
                    {copy.details}
                  </summary>
                  <ul>
                    {release.changes[lang].map((change) => <li key={change}>{change}</li>)}
                  </ul>
                  <p>
                    <a href={release.githubUrl}>
                      {copy.github} <ArrowUpRight aria-hidden="true" className="inline size-4" />
                    </a>
                  </p>
                </details>
              </article>
            ))}
          </section>
        ))}
      </DocsBody>
    </DocsPage>
  );
}

export function generateStaticParams() {
  return languages.map((lang) => ({ lang }));
}

export async function generateMetadata({ params }: { params: Promise<{ lang: string }> }) {
  const { lang } = await params;
  if (!isLanguage(lang)) notFound();
  return pageMetadata({
    path: `/${lang}/docs/changelog/`, lang, title: text[lang].title, description: text[lang].description,
    translations: { en: '/en/docs/changelog/', zh: '/zh/docs/changelog/' },
  });
}
