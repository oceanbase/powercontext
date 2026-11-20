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
import { Dateline, EntryRow, PageHeading, editorial as styles } from '@/components/editorial';
import { SiteFooter } from '@/components/site-footer';
import { buttonClasses } from '@astrafoundry/design/ui';
import { isLanguage } from '@/lib/i18n';
import { releases } from '@/lib/releases';

/** Keeps a title's middots at the end of a line rather than starting the next. */
const title = (text: string) => text.replaceAll(' · ', '\u00a0· ');
import { baseOptions } from '@/lib/site';
import { pageMetadata } from '@/lib/metadata';

const releasesUrl = 'https://github.com/oceanbase/powercontext/releases';

const labels = {
  en: {
    title: 'Changelog',
    description: 'Stable PowerContext releases and changes that affect users.',
    prereleases: 'Alpha, beta and RC versions are on',
    earlier: 'Earlier releases',
    highlights: 'Highlights',
    github: 'GitHub release',
    more: (n: number) => `All ${n} changes`,
  },
  zh: {
    title: '更新日志',
    description: '这里记录 PowerContext 的正式版本，以及会影响使用方式的变化。',
    prereleases: 'alpha、beta 和 RC 版本请查看',
    earlier: '更早的版本',
    highlights: '主要变化',
    github: 'GitHub Release',
    more: (n: number) => `全部 ${n} 项变化`,
  },
} as const;

/** How many changes of the latest release show before its notes. */
const LATEST_CHANGES = 3;

/** The first sentence of a change, so the latest release reads as a summary. */
function firstSentence(change: string) {
  const match = change.match(/^.+?[.。](?=\s|$)/);
  return match ? match[0] : change;
}

export default async function ChangelogPage({ params }: { params: Promise<{ lang: string }> }) {
  const { lang } = await params;
  if (!isLanguage(lang)) notFound();
  const text = labels[lang];
  const [latest, ...earlier] = releases;

  return (
    <HomeLayout {...baseOptions(lang)}>
      <main className={styles.main}>
        <PageHeading align="split" title={text.title} lead={<>
          {text.description} {text.prereleases}{' '}
          <Link className="underline decoration-1 underline-offset-[0.2em]" href={releasesUrl}>GitHub Releases</Link>
        </>} />
        {latest ? (
          <section aria-labelledby="latest-release" className={`${styles.featured} ${styles.entry}`}>
            <div className={styles.featuredBody}>
              <Dateline date={latest.date} labels={[<span className={styles.version} key="v">{latest.version}</span>]} lang={lang} />
              <h2 className={styles.featuredTitle} id="latest-release">
                <Link className={styles.linkTitle} href={`/${lang}/changelog/${latest.version}`}>{title(latest.title[lang])}</Link>
              </h2>
              <p className={styles.featuredLead}>{latest.summary[lang]}</p>
            </div>
            <div className={styles.featuredBody}>
              <ul aria-label={text.highlights} className={styles.changes}>
                {latest.changes[lang].slice(0, LATEST_CHANGES).map((change) => <li key={change}>{firstSentence(change)}</li>)}
              </ul>
              <div className={styles.links}>
                <Link className={buttonClasses('secondary', 'md')} href={`/${lang}/changelog/${latest.version}`}>
                  {text.more(latest.changes[lang].length)}
                </Link>
                <Link className={buttonClasses('secondary', 'md')} href={latest.githubUrl}>
                  {text.github} <ArrowUpRight aria-hidden="true" strokeWidth={1.75} />
                </Link>
              </div>
            </div>
          </section>
        ) : null}
        {earlier.length > 0 ? (
          <section aria-labelledby="earlier-releases" className={styles.section}>
            <h2 className={styles.sectionTitle} id="earlier-releases">{text.earlier}</h2>
            <ul className={styles.list}>
              {earlier.map((release) => (
                <li key={release.version}>
                  <EntryRow
                    date={release.date}
                    href={`/${lang}/changelog/${release.version}`}
                    labels={[<span className={styles.version} key="v">{release.version}</span>]}
                    lang={lang}
                    title={title(release.title[lang])}
                  >
                    <p className={styles.rowText}>{release.summary[lang]}</p>
                  </EntryRow>
                </li>
              ))}
            </ul>
          </section>
        ) : null}
      </main>
      <SiteFooter lang={lang} />
    </HomeLayout>
  );
}

export async function generateMetadata({ params }: { params: Promise<{ lang: string }> }) {
  const { lang } = await params;
  if (!isLanguage(lang)) notFound();
  return pageMetadata({
    path: `/${lang}/changelog/`, lang, title: labels[lang].title, description: labels[lang].description,
    translations: { en: '/en/changelog/', zh: '/zh/changelog/' },
  });
}
