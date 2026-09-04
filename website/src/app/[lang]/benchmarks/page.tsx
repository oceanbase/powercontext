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

import { ExternalLink } from 'lucide-react';
import type { Metadata } from 'next';
import { notFound } from 'next/navigation';
import type { ReactNode } from 'react';
import { buttonVariants } from 'fumadocs-ui/components/ui/button';
import { DocsDescription, DocsTitle } from 'fumadocs-ui/layouts/docs/page';
import { HomeLayout } from 'fumadocs-ui/layouts/home';
import { LocomoComposition, LocomoTradeoffChart, SwePairedChart } from '@/components/benchmark-charts';
import { BenchmarkLeaderboards } from '@/components/benchmark-leaderboards';
import { ExternalTextLink } from '@/components/external-text-link';
import { SiteFooter } from '@/components/site-footer';
import { getBenchmarkContent } from '@/lib/benchmark-content';
import { isLanguage } from '@/lib/i18n';
import { baseOptions } from '@/lib/site';

const pageCopy = {
  en: {
    title: 'Benchmarks',
    heroLink: 'Review both evaluations',
    heroChartTitle: 'LoCoMo accuracy and search latency',
    heroChartCount: '1,540 scored questions',
    compositionTitle: 'Question mix',
    compositionCount: '1,540 total',
    resultTitle: 'PowerContext result',
    resultScope: 'Categories 1 through 4',
    resultValue: '90.78% answer accuracy',
    resultSummary: 'PowerContext answered 1,398 of 1,540 questions correctly, 37.88 percentage points above full-context prompting. Search p95 was 1.38 seconds, with about 1.65k answer tokens per question.',
    pairedTitle: 'SWE-bench Pro paired runs',
    pairedRule: 'Passing the benchmark tests counts as resolved',
  },
  zh: {
    title: '基准测试',
    heroLink: '查看两项评测',
    heroChartTitle: 'LoCoMo 准确率与搜索延迟',
    heroChartCount: '1,540 道计分题',
    compositionTitle: '问题构成',
    compositionCount: '共 1,540 道',
    resultTitle: 'PowerContext 结果',
    resultScope: '类别 1 至 4',
    resultValue: '90.78% 问答准确率',
    resultSummary: 'PowerContext 答对 1,540 道题中的 1,398 道，比完整上下文方案高 37.88 个百分点。搜索 p95 为 1.38 秒，单题回答约使用 1.65k Token。',
    pairedTitle: 'SWE-bench Pro 配对运行',
    pairedRule: '通过基准测试即计为解决',
  },
} as const;

type PageProps = {
  params: Promise<{ lang: string }>;
};

export async function generateMetadata({ params }: PageProps): Promise<Metadata> {
  const { lang } = await params;
  if (!isLanguage(lang)) notFound();

  const benchmark = await getBenchmarkContent(lang);
  return {
    description: benchmark.hero.lead,
    title: pageCopy[lang].title,
  };
}

function ChartTitle({ children, detail }: { children: ReactNode; detail: string }) {
  return (
    <div className="mb-4 flex flex-wrap items-baseline justify-between gap-x-5 gap-y-1">
      <strong className="text-sm font-medium">{children}</strong>
      <span className="text-xs text-fd-muted-foreground">{detail}</span>
    </div>
  );
}

function SectionHeader({ children, description }: { children: ReactNode; description: string }) {
  return (
    <header className="prose max-w-3xl">
      <h2>{children}</h2>
      <p className="text-fd-muted-foreground">{description}</p>
    </header>
  );
}

export default async function BenchmarksPage({ params }: PageProps) {
  const { lang } = await params;
  if (!isLanguage(lang)) notFound();

  const benchmark = await getBenchmarkContent(lang);
  const text = pageCopy[lang];
  const accuracy = benchmark.locomo.metrics.find((metric) => metric.id === 'accuracy');
  const latency = benchmark.locomo.metrics.find((metric) => metric.id === 'latency');
  return (
    <HomeLayout {...baseOptions(lang)}>
      <main lang={lang === 'zh' ? 'zh-CN' : 'en'}>
        <section>
          <div className="mx-auto grid w-full max-w-(--fd-layout-width) items-center gap-12 px-4 py-16 lg:grid-cols-3 lg:py-20">
            <header className="grid gap-4">
              <p className="text-fd-primary">{benchmark.hero.label}</p>
              <DocsTitle>
                {benchmark.hero.title.map((line) => <span className="block" key={line}>{line}</span>)}
              </DocsTitle>
              <DocsDescription className="mb-0">{benchmark.hero.lead}</DocsDescription>
              <a className="inline-flex text-fd-primary hover:underline" href="#locomo">{text.heroLink} ↓</a>
            </header>
            {accuracy && latency ? (
              <div className="lg:col-span-2">
                <ChartTitle detail={text.heroChartCount}>{text.heroChartTitle}</ChartTitle>
                <LocomoTradeoffChart accuracy={accuracy.rows} label={benchmark.hero.visual_label} lang={lang} latency={latency.rows} />
              </div>
            ) : null}
          </div>
        </section>

        <div className="mx-auto w-full max-w-(--fd-layout-width) px-4 pb-20 md:pb-28">
          <section className="mt-16 grid scroll-mt-20 gap-10 border-t border-fd-border pt-10 lg:grid-cols-3" id="locomo">
            <SectionHeader description={benchmark.locomo.lead}>{benchmark.locomo.title}</SectionHeader>
            <div className="grid min-w-0 gap-8 lg:col-span-2 lg:grid-cols-2">
              <div>
                <ChartTitle detail={text.compositionCount}>{text.compositionTitle}</ChartTitle>
                <LocomoComposition categories={benchmark.locomo.categories} />
              </div>
              <div>
                <ChartTitle detail={text.resultScope}>{text.resultTitle}</ChartTitle>
                <div className="prose">
                  <p><strong className="text-fd-primary">{text.resultValue}</strong></p>
                  <p className="text-fd-muted-foreground">{text.resultSummary}</p>
                </div>
              </div>
            </div>
          </section>

          <section className="mt-16 grid scroll-mt-20 gap-10 border-t border-fd-border pt-10 lg:grid-cols-3" id="swe-bench">
            <SectionHeader description={benchmark.swe.lead}>{benchmark.swe.title}</SectionHeader>
            <div className="min-w-0 lg:col-span-2">
              <ChartTitle detail={text.pairedRule}>{text.pairedTitle}</ChartTitle>
              <SwePairedChart
                label={benchmark.swe.scores.map((score) => score.accessible).join('. ')}
                lang={lang}
                scores={benchmark.swe.scores}
                taskCount={benchmark.swe.task_count}
              />
              <p className="mt-4 max-w-3xl text-fd-muted-foreground">{benchmark.swe.scope}</p>
            </div>
          </section>

          <section className="mt-16 grid scroll-mt-20 gap-10 border-t border-fd-border pt-10 lg:grid-cols-3" id="published-results">
            <div>
              <SectionHeader description={benchmark.leaderboards.lead}>{benchmark.leaderboards.title}</SectionHeader>
              <p className="mt-3 text-sm text-fd-muted-foreground">{benchmark.leaderboards.updated}</p>
            </div>
            <div className="min-w-0 lg:col-span-2"><BenchmarkLeaderboards benchmark={benchmark} lang={lang} /></div>
          </section>

          <section className="mt-16 grid gap-10 border-t border-fd-border pt-10 lg:grid-cols-3" id="methods">
            <SectionHeader description={benchmark.sources.lead}>{benchmark.sources.title}</SectionHeader>
            <div className="grid gap-10 lg:col-span-2">
              {benchmark.sources.groups.map((group) => (
                <section aria-labelledby={`method-${group.id}`} className="grid gap-4" key={group.id}>
                  <h3 className="font-medium text-fd-primary" id={`method-${group.id}`}>
                    {group.title}
                  </h3>
                  <div className="grid gap-7 sm:grid-cols-2">
                    {group.items.map((source) => (
                      <div key={source.href}>
                        <span className="text-sm text-fd-muted-foreground">{source.type}</span>
                        <span className="mt-1 block"><ExternalTextLink href={source.href}>{source.label}</ExternalTextLink></span>
                      </div>
                    ))}
                  </div>
                </section>
              ))}
            </div>
          </section>

          <section className="mt-16 flex flex-col justify-between gap-7 bg-fd-primary/8 p-8 md:flex-row md:items-center">
            <div className="prose">
              <h2>{benchmark.cta.title}</h2>
              <p className="max-w-2xl text-fd-muted-foreground">{benchmark.cta.lead}</p>
            </div>
            <a className={buttonVariants({ variant: 'primary' })} href={benchmark.cta.href} rel="noreferrer" target="_blank">
              {benchmark.cta.label} <ExternalLink aria-hidden="true" className="size-4" />
            </a>
          </section>
        </div>
      </main>
      <SiteFooter lang={lang} />
    </HomeLayout>
  );
}
