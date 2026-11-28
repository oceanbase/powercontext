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

import { ArrowLeft } from 'lucide-react';
import Link from 'next/link';
import type { ReactNode } from 'react';
import type { Language } from '@/lib/i18n';
import styles from './editorial.module.css';

export { styles as editorial };

/** A display-serif page title with an optional lead. No kicker by design. */
export function PageHeading({ title, lead, align = 'start', children }: {
  title: ReactNode;
  lead?: ReactNode;
  /** `split` sets the lead beside the title on wide screens. */
  align?: 'start' | 'split';
  children?: ReactNode;
}) {
  return (
    <header className={styles.heading} data-align={align}>
      <h1 className={styles.title}>{title}</h1>
      {lead ? <p className={styles.lead}>{lead}</p> : null}
      {children}
    </header>
  );
}

export function formatDate(date: string, lang: Language) {
  return new Intl.DateTimeFormat(lang === 'zh' ? 'zh-CN' : 'en-US', {
    year: 'numeric', month: 'short', day: 'numeric', timeZone: 'UTC',
  }).format(new Date(`${date}T00:00:00Z`));
}

/** The date, then quiet labels such as a category, separated by middots. */
export function Dateline({ date, lang, labels = [], className }: {
  date: string;
  lang: Language;
  labels?: ReactNode[];
  className?: string;
}) {
  return (
    <p className={[styles.dateline, className].filter(Boolean).join(' ')}>
      <time dateTime={date}>{formatDate(date, lang)}</time>
      {labels.filter(Boolean).map((label, index) => <span key={index}>{label}</span>)}
    </p>
  );
}

export function BackLink({ href, children }: { href: string; children: ReactNode }) {
  return (
    <Link className={styles.back} href={href}>
      <ArrowLeft aria-hidden="true" strokeWidth={1.75} />
      {children}
    </Link>
  );
}

/** Rows in a list: date and labels in a side column, then the entry. */
export function EntryRow({ href, date, lang, labels, title, children, thumb, byline }: {
  href: string;
  /** Who wrote it. With a byline, the date and labels join it on one line above the title. */
  byline?: ReactNode;
  date: string;
  lang: Language;
  labels: ReactNode[];
  title: ReactNode;
  children?: ReactNode;
  thumb?: ReactNode;
}) {
  return (
    <div className={`${styles.row} ${styles.entry}`} data-inline={byline ? '' : undefined} data-thumb={thumb ? '' : undefined}>
      {byline ? null : <>
        <Dateline className={styles.rowMetaInline} date={date} labels={labels} lang={lang} />
        <p className={styles.rowMeta}>
          <time dateTime={date}>{formatDate(date, lang)}</time>
          {labels.filter(Boolean).map((label, index) => <span key={index}>{label}</span>)}
        </p>
      </>}
      <div className={styles.rowBody}>
        {byline ? (
          <div className={styles.metaRow}>
            {byline}
            <Dateline date={date} labels={labels} lang={lang} />
          </div>
        ) : null}
        <h3 className={styles.rowTitle}>
          <Link className={styles.linkTitle} href={href}>{title}</Link>
        </h3>
        {children}
      </div>
      {thumb ? <Link aria-hidden="true" className={styles.rowThumb} href={href} tabIndex={-1}>{thumb}</Link> : null}
    </div>
  );
}
