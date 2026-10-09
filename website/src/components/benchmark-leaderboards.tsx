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

'use client';

import { useState } from 'react';
import { ExternalTextLink } from '@/components/external-text-link';
import type { BenchmarkContent } from '@/lib/benchmark-content';

const copy = {
  en: {
    completeScope: 'All 1,540 scored questions',
    comparisonLimit: 'Readers, judges, and answer-matching rules differ, so this is not an official ranking.',
    independentRun: 'Same 731 tasks · independent paired run',
    independentRunNote: 'The paired runs use a different protocol, so no official rank is assigned.',
    officialBoard: 'Open the official SWE-bench Pro Public leaderboard',
    officialEntries: 'Official Public entries',
  },
  zh: {
    completeScope: '全部 1,540 道计分题',
    comparisonLimit: 'Reader、Judge 与答案匹配规则并不统一，因此不作为官方排名。',
    independentRun: '同一组 731 个任务 · 独立配对运行',
    independentRunNote: '配对运行采用不同协议，因此不分配官方名次。',
    officialBoard: '前往 SWE-bench Pro 官方 Public 榜',
    officialEntries: '官方 Public 条目',
  },
} as const;

function percentage(score: string) {
  return Number.parseFloat(score.replace('%', ''));
}

function ResultAxis({ metric, system }: { metric: string; system: string }) {
  return (
    <div aria-hidden="true" className="grid grid-cols-12 items-end gap-1.5 py-3 text-xs text-fd-muted-foreground sm:gap-2">
      <span className="col-span-4 col-start-2">{system}</span>
      <span className="col-span-5 col-start-6 flex justify-between">
        <span>0</span>
        <span>{metric}</span>
        <span>100%</span>
      </span>
    </div>
  );
}

function ResultBar({ highlight = false, value }: { highlight?: boolean; value: number }) {
  return (
    <span className="block h-2 bg-fd-muted">
      <span
        className={`block h-full ${highlight ? 'bg-fd-primary' : 'bg-fd-foreground'}`}
        style={{ width: `${value}%` }}
      />
    </span>
  );
}

function LocomoResults({ benchmark, lang }: { benchmark: BenchmarkContent; lang: 'en' | 'zh' }) {
  const board = benchmark.leaderboards.locomo;

  return (
    <div aria-labelledby="locomo-results-tab" id="locomo-results-panel" role="tabpanel">
      <p className="text-sm text-fd-muted-foreground">{board.count} · {copy[lang].completeScope}</p>
      <figure aria-label={board.table_label}>
        <ResultAxis metric={board.columns.score} system={board.columns.system} />
        <ol className="grid">
          {board.rows.map((row) => (
            <li
              aria-label={`${row.rank}. ${row.name}, ${row.score}, ${row.evidence}. ${row.protocol}`}
              className="grid min-h-11 grid-cols-12 items-center gap-1.5 py-1 sm:gap-2"
              key={row.name}
            >
              <span className="col-span-1 text-right text-xs tabular-nums text-fd-muted-foreground">{row.rank}</span>
              <span className="col-span-4 min-w-0 leading-tight">
                <a
                  className={`block truncate text-sm hover:text-fd-primary ${row.highlight ? 'font-medium text-fd-primary' : ''}`}
                  href={row.source}
                  rel="noreferrer"
                  target="_blank"
                  title={row.name}
                >
                  {row.name}
                </a>
                <span
                  className={`mt-1 inline-block max-w-full truncate px-1.5 py-0.5 text-xs ${row.highlight ? 'bg-fd-primary/10 text-fd-primary' : 'bg-fd-muted text-fd-muted-foreground'}`}
                  title={row.protocol}
                >
                  {row.evidence}
                </span>
              </span>
              <span className="col-span-5">
                <ResultBar highlight={row.highlight} value={percentage(row.score)} />
              </span>
              <strong className={`col-span-2 text-right text-sm font-medium tabular-nums ${row.highlight ? 'text-fd-primary' : ''}`}>
                {row.score}
              </strong>
            </li>
          ))}
        </ol>
      </figure>
      <footer className="mt-4 border-t border-fd-border pt-4 text-sm text-fd-muted-foreground">
        <p className="max-w-xl">{copy[lang].comparisonLimit}</p>
      </footer>
    </div>
  );
}

function LocomoPlusResults({ benchmark }: { benchmark: BenchmarkContent }) {
  const board = benchmark.leaderboards.locomo_plus;
  const experiment = benchmark.locomo_plus;
  const spotlight = experiment.rows.find((row) => row.model === board.spotlight.model);

  return (
    <div aria-labelledby="locomo-plus-results-tab" id="locomo-plus-results-panel" role="tabpanel">
      {spotlight && (
        <section className="mb-6 bg-fd-primary/10 p-4" aria-label={board.spotlight.title}>
          <div className="flex flex-wrap items-baseline justify-between gap-2 text-sm">
            <strong className="font-medium">{board.spotlight.title}</strong>
            <a className="text-fd-primary underline underline-offset-4" href="#locomo-plus">{board.spotlight.link_label}</a>
          </div>
          <dl className="mt-4 grid grid-cols-2 gap-4">
            {(['baseline', 'jev'] as const).map((kind) => (
              <div key={kind}>
                <dt className="text-xs text-fd-muted-foreground">{experiment.columns[kind]}</dt>
                <dd className="mt-1 text-2xl font-medium tabular-nums text-fd-primary">{`${spotlight[kind]}%`}</dd>
              </div>
            ))}
          </dl>
          <p className="mt-4 text-sm leading-relaxed text-fd-muted-foreground">{experiment.provenance_note}</p>
        </section>
      )}
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="text-sm font-medium">{board.title}</h3>
        <span className="text-xs text-fd-muted-foreground">{board.count} · {board.updated}</span>
      </div>
      <figure aria-label={board.table_label}>
        <div aria-hidden="true" className="grid grid-cols-12 gap-2 py-3 text-xs text-fd-muted-foreground">
          <span className="col-span-5 col-start-6 flex justify-between"><span>0</span><span>{board.score_label}</span><span>100%</span></span>
        </div>
        <ul className="grid divide-y divide-fd-border">
          {board.rows.map((row) => (
            <li className="grid grid-cols-12 items-center gap-x-2 gap-y-2 py-3" key={row.name}>
              <span className="col-span-5 min-w-0 text-sm font-medium">
                <a className="hover:text-fd-primary" href={row.source} rel="noreferrer" target="_blank">{row.name}</a>
              </span>
              <span aria-hidden="true" className="col-span-5"><ResultBar value={percentage(row.score)} /></span>
              <strong className="col-span-2 text-right text-sm font-medium tabular-nums">{row.score}</strong>
              <div className="col-span-12 flex flex-wrap items-baseline gap-x-3 gap-y-1 text-xs text-fd-muted-foreground">
                <a className="bg-fd-muted px-1.5 py-0.5 hover:text-fd-primary" href={row.source} rel="noreferrer" target="_blank">{row.evidence}</a>
                <span>{row.protocol}</span>
              </div>
            </li>
          ))}
        </ul>
      </figure>
    </div>
  );
}

function SweResults({ benchmark, lang }: { benchmark: BenchmarkContent; lang: 'en' | 'zh' }) {
  const board = benchmark.leaderboards.swe;
  const pairedScores = [...benchmark.swe.scores].sort((left) => (left.kind === 'on' ? -1 : 1));

  return (
    <div aria-labelledby="swe-results-tab" id="swe-results-panel" role="tabpanel">
      <section className="mb-4 bg-fd-primary/10 p-4" aria-label={board.spotlight.label}>
        <div className="flex flex-col justify-between gap-1 text-sm sm:flex-row">
          <strong className="font-medium">{board.spotlight.label}</strong>
          <span className="text-fd-muted-foreground">{copy[lang].independentRun}</span>
        </div>
        <div className="mt-2 grid gap-1">
          {pairedScores.map((score) => {
            const value = (score.count / benchmark.swe.task_count) * 100;
            return (
              <div
                aria-label={score.accessible}
                className="grid min-h-9 grid-cols-12 items-center gap-1.5 sm:gap-2"
                key={score.kind}
              >
                <span className="col-span-5 text-sm text-fd-primary">{score.kind.toUpperCase()} · {score.count} / {benchmark.swe.task_count}</span>
                <span className="col-span-5">
                  <span className="block h-2 bg-fd-muted">
                    <span className={`block h-full ${score.kind === 'on' ? 'bg-fd-primary' : 'bg-fd-primary/40'}`} style={{ width: `${value}%` }} />
                  </span>
                </span>
                <strong className="col-span-2 text-right text-sm font-medium tabular-nums text-fd-primary">{value.toFixed(2)}%</strong>
              </div>
            );
          })}
        </div>
      </section>

      <div className="flex flex-col justify-between gap-1 text-sm sm:flex-row">
        <strong className="font-medium">{copy[lang].officialEntries}</strong>
        <span className="text-fd-muted-foreground">{board.count}</span>
      </div>
      <p className="mt-1 text-xs text-fd-muted-foreground">{board.rank_note}</p>
      <figure aria-label={board.table_label}>
        <ResultAxis metric={board.columns.score} system={board.columns.system} />
        <ol className="grid">
          {board.rows.map((row) => {
            const harness = row.star ? board.harness_star : board.harness_default;
            const source = `${row.provider} · ${harness}`;
            return (
              <li
                aria-label={`${row.rank}. ${row.name}, ${row.score} ${row.ci}, ${source}`}
                className="grid min-h-11 grid-cols-12 items-center gap-1.5 py-1 sm:gap-2"
                key={`${row.rank}-${row.name}`}
              >
                <span className="col-span-1 text-right text-xs tabular-nums text-fd-muted-foreground">{row.rank}</span>
                <span className="col-span-4 min-w-0 leading-tight">
                  <span className="block truncate text-sm" title={row.name}>{row.name}</span>
                  <span className="mt-1 inline-block max-w-full truncate bg-fd-muted px-1.5 py-0.5 text-xs text-fd-muted-foreground" title={source}>
                    {source}
                  </span>
                </span>
                <span className="col-span-5"><ResultBar value={percentage(row.score)} /></span>
                <strong className="col-span-2 text-right text-sm font-medium tabular-nums">
                  {row.score}
                  <span className="mt-0.5 block text-xs font-normal text-fd-muted-foreground">{row.ci}</span>
                </strong>
              </li>
            );
          })}
        </ol>
      </figure>
      <footer className="mt-4 flex flex-col justify-between gap-3 border-t border-fd-border pt-4 text-sm text-fd-muted-foreground sm:flex-row sm:items-start">
        <p className="max-w-xl">{copy[lang].independentRunNote}</p>
        <p className="shrink-0"><ExternalTextLink href={board.source}>{copy[lang].officialBoard}</ExternalTextLink></p>
      </footer>
    </div>
  );
}

export function BenchmarkLeaderboards({ benchmark, lang }: { benchmark: BenchmarkContent; lang: 'en' | 'zh' }) {
  const tabs = ['locomo', 'locomo_plus', 'swe'] as const;
  const [activeTab, setActiveTab] = useState<typeof tabs[number]>('locomo');

  return (
    <div>
      <div aria-label={benchmark.leaderboards.tabs_label} className="mb-6 flex gap-6 overflow-x-auto border-b border-fd-border" role="tablist">
        {tabs.map((tab, index) => (
          <button
            aria-controls={`${tab.replace('_', '-')}-results-panel`}
            aria-selected={activeTab === tab}
            className={`whitespace-nowrap border-b-2 pb-2.5 text-sm ${activeTab === tab ? 'border-fd-primary font-medium text-fd-foreground' : 'border-transparent text-fd-muted-foreground'}`}
            id={`${tab.replace('_', '-')}-results-tab`}
            key={tab}
            onClick={() => setActiveTab(tab)}
            onKeyDown={(event) => {
              let next: number;
              if (event.key === 'ArrowRight') next = (index + 1) % tabs.length;
              else if (event.key === 'ArrowLeft') next = (index + tabs.length - 1) % tabs.length;
              else if (event.key === 'Home') next = 0;
              else if (event.key === 'End') next = tabs.length - 1;
              else return;
              event.preventDefault();
              setActiveTab(tabs[next]);
              document.getElementById(`${tabs[next].replace('_', '-')}-results-tab`)?.focus();
            }}
            role="tab"
            tabIndex={activeTab === tab ? 0 : -1}
            type="button"
          >
            {benchmark.leaderboards[tab].tab}
          </button>
        ))}
      </div>
      <div hidden={activeTab !== 'locomo'}><LocomoResults benchmark={benchmark} lang={lang} /></div>
      <div hidden={activeTab !== 'locomo_plus'}><LocomoPlusResults benchmark={benchmark} /></div>
      <div hidden={activeTab !== 'swe'}><SweResults benchmark={benchmark} lang={lang} /></div>
    </div>
  );
}
