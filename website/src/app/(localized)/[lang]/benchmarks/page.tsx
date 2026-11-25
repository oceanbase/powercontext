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

import { ArrowDown, ExternalLink } from 'lucide-react';
import type { Metadata } from 'next';
import { notFound } from 'next/navigation';
import type { ReactNode } from 'react';
import { HomeLayout } from 'fumadocs-ui/layouts/home';
import {
  buttonClasses, Card, CardDescription, CardTitle, Tab, TabPanel, Tabs, TabsList,
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '@astrafoundry/design/ui';
import { ChartLegend, ColumnChart, LocomoComposition, LocomoTradeoffChart } from '@/components/benchmark-charts';
import { BenchmarkLeaderboards } from '@/components/benchmark-leaderboards';
import { CountUp, RevealOnView } from '@/components/benchmark-motion';
import { BenchmarkSectionNav } from '@/components/benchmark-section-nav';
import styles from '@/components/benchmarks.module.css';
import { ExternalTextLink } from '@/components/external-text-link';
import { SiteFooter } from '@/components/site-footer';
import { getBenchmarkContent, type BenchmarkContent } from '@/lib/benchmark-content';
import { isLanguage } from '@/lib/i18n';
import { baseOptions } from '@/lib/site';
import { pageMetadata } from '@/lib/metadata';

const pageCopy = {
  en: {
    title: 'Benchmarks',
    sections: 'Benchmarks on this page',
    published: 'Published results',
    higher: 'Higher is better',
    lower: 'Lower is better',
    accuracy: 'Answer accuracy',
    plusScore: 'Score by extraction and answer model',
    sweScore: 'Share of 731 tasks resolved',
    tradeoff: 'Accuracy and latency',
    tradeoffDetail: 'Search p95 against answer accuracy on 1,540 scored questions',
    compositionTitle: 'Question mix',
    compositionCount: '1,540 scored questions',
    resultTitle: 'PowerContext result',
    resultValue: '90.78%',
    resultDetail: '1,398 of 1,540 answered correctly',
    method: 'Run setup',
    paired: 'Paired run result',
    sources: 'Sources',
  },
  zh: {
    title: '基准测试',
    sections: '本页评测',
    published: '公开结果对照',
    higher: '越高越好',
    lower: '越低越好',
    accuracy: '问答准确率',
    plusScore: '按抽取与回答模型分组的得分',
    sweScore: '731 个任务中的解决比例',
    tradeoff: '准确率与延迟',
    tradeoffDetail: '1,540 道计分题的搜索 p95 与问答准确率',
    compositionTitle: '问题构成',
    compositionCount: '1,540 道计分题',
    resultTitle: 'PowerContext 结果',
    resultValue: '90.78%',
    resultDetail: '1,540 道题答对 1,398 道',
    method: '运行设置',
    paired: '配对运行结果',
    sources: '来源',
  },
} as const;

type PageProps = {
  params: Promise<{ lang: string }>;
};

export async function generateMetadata({ params }: PageProps): Promise<Metadata> {
  const { lang } = await params;
  if (!isLanguage(lang)) notFound();

  const benchmark = await getBenchmarkContent(lang);
  return pageMetadata({
    path: `/${lang}/benchmarks/`, lang,
    description: benchmark.hero.lead, title: pageCopy[lang].title,
    translations: { en: "/en/benchmarks/", zh: "/zh/benchmarks/" },
  });
}

const percent = (value: number) => `${value}%`;
const gain = (row: { baseline: number; jev: number }) => `+${(row.jev - row.baseline).toFixed(3)}`;

/** A square in the result colour, then the title: how every chart and section is named. */
function Marked({ children }: { children: ReactNode }) {
  return <span className="flex items-center gap-2.5"><span aria-hidden="true" className={styles.mark} />{children}</span>;
}

function Section({ id, title, lead, aside, children }: {
  id: string;
  title: string;
  lead: string;
  aside?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section aria-labelledby={`${id}-title`} className="grid scroll-mt-24 gap-6" id={id}>
      <header className="flex flex-wrap items-end justify-between gap-x-8 gap-y-3">
        <div className="grid max-w-3xl gap-3">
          <h2 className="font-display text-3xl leading-tight font-normal tracking-[-0.015em] text-balance" id={`${id}-title`}>
            <Marked>{title}</Marked>
          </h2>
          <p className="text-pretty text-fd-muted-foreground">{lead}</p>
        </div>
        {aside}
      </header>
      {children}
    </section>
  );
}

/** A section's method references, in one quiet line under its cards. */
function Sources({ benchmark, id, label }: { benchmark: BenchmarkContent; id: string; label: string }) {
  const group = benchmark.sources.groups.find((item) => item.id === id);
  if (!group) return null;
  return (
    <dl className="flex flex-wrap gap-x-8 gap-y-3 text-sm">
      <dt className="sr-only">{label}</dt>
      {group.items.map((source) => (
        <dd key={source.href}>
          <span className="block text-xs text-fd-muted-foreground">{source.type}</span>
          <ExternalTextLink href={source.href}>{source.label}</ExternalTextLink>
        </dd>
      ))}
    </dl>
  );
}

/** One of the three headline charts that open the page. */
function OverviewCard({ title, detail, href, children }: { title: string; detail: string; href: string; children: ReactNode }) {
  return (
    <Card className="grid min-w-0 content-start gap-5">
      <div>
        <CardTitle className="font-display text-2xl font-normal tracking-[-0.01em]">
          <a className="hover:text-primary-text" href={href}><Marked>{title}</Marked></a>
        </CardTitle>
        <CardDescription>{detail}</CardDescription>
      </div>
      {children}
    </Card>
  );
}

export default async function BenchmarksPage({ params }: PageProps) {
  const { lang } = await params;
  if (!isLanguage(lang)) notFound();

  const benchmark = await getBenchmarkContent(lang);
  const text = pageCopy[lang];
  const { locomo, swe } = benchmark;
  const plus = benchmark.locomo_plus;
  const metric = (id: string) => locomo.metrics.find((item) => item.id === id);
  const accuracy = metric('accuracy');
  const latency = metric('latency');
  const bestJev = plus.rows.reduce((best, row) => row.jev > best.jev ? row : best);
  const largestGain = plus.rows.reduce((best, row) => row.jev - row.baseline > best.jev - best.baseline ? row : best);
  const sweColumns = [...swe.scores]
    .sort((left) => (left.kind === 'off' ? -1 : 1))
    .map((score) => {
      const value = (score.count / swe.task_count) * 100;
      return { label: score.label, value, display: `${value.toFixed(2)}%`, highlight: score.kind === 'on' };
    });
  const sweOn = sweColumns.find((column) => column.highlight);
  const plusGroups = plus.rows.map((row) => ({
    label: row.model,
    columns: [
      { label: plus.columns.baseline, value: row.baseline, display: percent(Number(row.baseline.toFixed(1))) },
      { label: plus.columns.jev, value: row.jev, display: percent(Number(row.jev.toFixed(1))), highlight: true },
    ],
  }));
  const accuracyColumns = (accuracy?.rows ?? []).map((row) => ({
    label: row.name, value: row.value, display: row.display, highlight: row.name === 'PowerContext',
  }));

  const boards = benchmark.leaderboards;
  const sections = [
    { id: 'locomo', label: boards.locomo.tab },
    { id: 'locomo-plus', label: boards.locomo_plus.tab },
    { id: 'swe-bench', label: boards.swe.tab },
    { id: 'published-results', label: text.published },
  ];
  const headline = [
    { name: boards.locomo.tab, value: accuracy?.rows[0]?.display ?? '', detail: benchmark.hero.results[0]?.metric, target: 'locomo' },
    { name: boards.locomo_plus.tab, value: percent(bestJev.jev), detail: `${plus.columns.jev} · ${bestJev.model}`, target: 'locomo-plus' },
    { name: boards.swe.tab, value: sweOn?.display ?? '', detail: benchmark.hero.results[1]?.metric, target: 'swe-bench' },
  ];

  return (
    <HomeLayout {...baseOptions(lang)}>
      <noscript>
        <style>{`[data-count]{opacity:1!important}.pc-meter-fill{width:var(--w)!important;animation:none!important}.${styles.bar}{height:var(--h)!important;min-height:2px!important;animation:none!important}`}</style>
      </noscript>
      <main className="mx-auto w-full max-w-(--fd-layout-width) px-4 pb-20 md:pb-28" lang={lang === 'zh' ? 'zh-CN' : 'en'}>
        <section className="grid items-end gap-x-16 gap-y-10 py-14 lg:grid-cols-[minmax(0,1fr)_24rem] lg:py-20">
          <header className="grid gap-5">
            <h1 className="font-display text-5xl leading-[1.02] font-normal tracking-[-0.025em] text-balance sm:text-6xl">
              <span className="sr-only">{benchmark.hero.label}: </span>
              {benchmark.hero.title.map((line) => <span className="block" key={line}>{line}</span>)}
            </h1>
            <p className="max-w-xl text-lg text-pretty text-fd-muted-foreground">{benchmark.hero.lead}</p>
          </header>
          <ul aria-label={benchmark.hero.actions_label} className="grid divide-y divide-fd-border border-y border-fd-border">
            {headline.map((item) => (
              <li key={item.target}>
                <a className="group grid grid-cols-[1fr_auto] items-baseline gap-x-4 py-4" href={`#${item.target}`}>
                  <span className="text-sm font-medium">{item.name}</span>
                  <ArrowDown aria-hidden="true" className="size-4 self-center text-fd-muted-foreground transition-transform group-hover:translate-y-0.5" />
                  <span className="font-display text-4xl tracking-tight text-primary-text tabular-nums"><CountUp value={item.value} /></span>
                  <span className="col-span-2 text-sm text-fd-muted-foreground">{item.detail}</span>
                </a>
              </li>
            ))}
          </ul>
        </section>

        <div className="grid gap-4 lg:grid-cols-3">
          <OverviewCard detail={`${text.accuracy} · ${text.higher}`} href="#locomo" title={boards.locomo.tab}>
            <ColumnChart groups={[{ columns: accuracyColumns }]} label={accuracy?.chart_label ?? ''} max={100} />
          </OverviewCard>
          <OverviewCard detail={`${text.plusScore} · ${text.higher}`} href="#locomo-plus" title={boards.locomo_plus.tab}>
            <ColumnChart groups={plusGroups} label={plus.table_label} max={100} />
            <ChartLegend base={plus.columns.baseline} result={plus.columns.jev} />
          </OverviewCard>
          <OverviewCard detail={`${text.sweScore} · ${text.higher}`} href="#swe-bench" title={boards.swe.tab}>
            <ColumnChart groups={[{ columns: sweColumns }]} label={swe.scores.map((score) => score.accessible).join('. ')} max={100} />
          </OverviewCard>
        </div>

        <div className="mt-20 grid gap-12 lg:grid-cols-[11rem_minmax(0,1fr)] lg:gap-16">
          <aside className="hidden lg:block">
            <BenchmarkSectionNav label={text.sections} sections={sections} />
          </aside>

          <div className="grid min-w-0 gap-20">
            <Section id="locomo" lead={locomo.lead} title={locomo.title}>
              <Card className="overflow-hidden p-0">
                <Tabs defaultValue="accuracy">
                  <div className="overflow-x-auto border-b border-fd-border bg-flat-face px-5">
                    <TabsList aria-label={locomo.tabs_label} className="border-b-0">
                      {locomo.metrics.map((item) => <Tab key={item.id} value={item.id}>{item.label}</Tab>)}
                      <Tab value="tradeoff">{text.tradeoff}</Tab>
                    </TabsList>
                  </div>
                  {locomo.metrics.map((item) => (
                    <TabPanel className="grid gap-6 p-5 sm:p-6" keepMounted key={item.id} value={item.id}>
                      <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1">
                        <div>
                          <CardTitle>{item.label}</CardTitle>
                          <CardDescription>{item.direction.replace(/[.。]$/, '')}</CardDescription>
                        </div>
                        <p className="text-sm"><strong className="font-medium text-primary-text">{item.callout}</strong> <span className="text-fd-muted-foreground">{item.callout_detail}</span></p>
                      </div>
                      <div className="max-w-2xl">
                        <ColumnChart
                          groups={[{ columns: item.rows.map((row) => ({ label: row.name, value: row.value, display: row.display, highlight: row.name === 'PowerContext' })) }]}
                          label={item.chart_label}
                          max={item.id === 'accuracy' ? 100 : undefined}
                        />
                      </div>
                    </TabPanel>
                  ))}
                  <TabPanel className="grid gap-6 p-5 sm:p-6" keepMounted value="tradeoff">
                    <div>
                      <CardTitle>{text.tradeoff}</CardTitle>
                      <CardDescription>{text.tradeoffDetail}</CardDescription>
                    </div>
                    {accuracy && latency ? (
                      <LocomoTradeoffChart accuracy={accuracy.rows} label={benchmark.hero.visual_label} lang={lang} latency={latency.rows} />
                    ) : null}
                  </TabPanel>
                </Tabs>
              </Card>
              <div className="grid gap-4 md:grid-cols-2">
                <Card className="grid content-start gap-5">
                  <div>
                    <CardTitle>{text.compositionTitle}</CardTitle>
                    <CardDescription>{text.compositionCount}</CardDescription>
                  </div>
                  <LocomoComposition categories={locomo.categories} />
                </Card>
                <Card className="grid content-start gap-3">
                  <CardTitle>{text.resultTitle}</CardTitle>
                  <p className="font-display text-5xl tracking-tight text-primary-text tabular-nums"><CountUp value={text.resultValue} /></p>
                  <p className="text-sm font-medium">{text.resultDetail}</p>
                  <p className="text-sm text-pretty text-fd-muted-foreground">{locomo.scope}</p>
                </Card>
              </div>
              <Sources benchmark={benchmark} id="locomo" label={text.sources} />
            </Section>

            <Section id="locomo-plus" lead={plus.lead} title={plus.title}>
              <Card className="grid gap-6">
                <div className="grid gap-x-10 gap-y-6 md:grid-cols-[minmax(0,1fr)_auto]">
                  <div>
                    <CardTitle>{plus.results_title}</CardTitle>
                    <CardDescription className="max-w-2xl">{plus.provenance_note}</CardDescription>
                  </div>
                  <dl className="flex gap-10">
                    <div>
                      <dt className="text-xs text-fd-muted-foreground">{plus.best_label}</dt>
                      <dd className="mt-1 font-display text-3xl tracking-tight text-primary-text tabular-nums"><CountUp value={percent(bestJev.jev)} /></dd>
                      <dd className="mt-1 text-xs text-fd-muted-foreground">{bestJev.model}</dd>
                    </div>
                    <div>
                      <dt className="text-xs text-fd-muted-foreground">{plus.gain_label}</dt>
                      <dd className="mt-1 font-display text-3xl tracking-tight tabular-nums"><CountUp value={gain(largestGain)} /></dd>
                      <dd className="mt-1 text-xs text-fd-muted-foreground">{largestGain.model} · {plus.points_label}</dd>
                    </div>
                  </dl>
                </div>
                <Table aria-label={plus.table_label}>
                  <TableHeader>
                    <TableRow>
                      <TableHead>{plus.columns.model}</TableHead>
                      <TableHead align="end">{plus.columns.baseline}</TableHead>
                      <TableHead align="end">{plus.columns.jev}</TableHead>
                      <TableHead align="end">{plus.columns.gain}</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {plus.rows.map((row) => (
                      <TableRow key={row.model}>
                        <TableCell className="font-medium">{row.model}</TableCell>
                        <TableCell align="end" className="text-fd-muted-foreground">{percent(row.baseline)}</TableCell>
                        <TableCell align="end" className="font-semibold">{percent(row.jev)}</TableCell>
                        <TableCell align="end" className="text-primary-text">{gain(row)}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
                <p className="border-t border-fd-border pt-4 text-sm text-pretty text-fd-muted-foreground">
                  {plus.model_note} {plus.embedding_label}: <span className="font-medium text-fd-foreground">{plus.embedding_model}</span> · {plus.embedding_dimensions}
                </p>
              </Card>
              <Sources benchmark={benchmark} id="locomo-plus" label={text.sources} />
            </Section>

            <Section id="swe-bench" lead={swe.lead} title={swe.title}>
              <Card className="grid gap-8 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
                <div className="grid content-start gap-3">
                  <CardTitle>{text.paired}</CardTitle>
                  <p className="flex flex-wrap items-baseline gap-x-3">
                    <span className="font-display text-5xl tracking-tight text-primary-text tabular-nums"><CountUp value={swe.delta} /></span>
                    <span className="text-sm font-medium">{swe.delta_label}</span>
                  </p>
                  <p className="text-sm text-pretty text-fd-muted-foreground">{swe.caption}</p>
                </div>
                <div>
                  <CardTitle>{text.method}</CardTitle>
                  <dl className="mt-3 grid divide-y divide-fd-border text-sm">
                    {swe.method.map((item) => (
                      <div className="grid gap-1 py-3 first:pt-0" key={item.title}>
                        <dt className="font-medium">{item.title}</dt>
                        <dd className="text-fd-muted-foreground">{item.description}</dd>
                      </div>
                    ))}
                  </dl>
                </div>
                <p className="border-t border-fd-border pt-4 text-sm text-pretty text-fd-muted-foreground md:col-span-2">{swe.scope}</p>
              </Card>
              <Sources benchmark={benchmark} id="swe" label={text.sources} />
            </Section>

            <Section
              aside={<p className="text-sm text-fd-muted-foreground">{benchmark.leaderboards.updated}</p>}
              id="published-results"
              lead={benchmark.leaderboards.lead}
              title={benchmark.leaderboards.title}
            >
              <Card className="overflow-hidden p-0">
                <RevealOnView><BenchmarkLeaderboards benchmark={benchmark} lang={lang} /></RevealOnView>
              </Card>
            </Section>

            <Card className="flex flex-col justify-between gap-6 md:flex-row md:items-center md:p-8">
              <div className="grid gap-2">
                <h2 className="font-display text-2xl font-normal tracking-[-0.01em]">{benchmark.cta.title}</h2>
                <p className="max-w-2xl text-pretty text-fd-muted-foreground">{benchmark.cta.lead}</p>
              </div>
              <a className={buttonClasses('primary', 'md')} href={benchmark.cta.href} rel="noreferrer" target="_blank">
                {benchmark.cta.label} <ExternalLink aria-hidden="true" className="size-4" />
              </a>
            </Card>
          </div>
        </div>
      </main>
      <SiteFooter lang={lang} />
    </HomeLayout>
  );
}
