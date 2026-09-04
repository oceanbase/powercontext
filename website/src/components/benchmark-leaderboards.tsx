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

function ResultAxis({ label, metric, rank }: { label: string; metric: string; rank: string }) {
  return (
    <div aria-hidden="true" className="grid grid-cols-12 items-end gap-1.5 py-3 text-xs text-fd-muted-foreground sm:gap-2">
      <span className="col-span-5">{rank} · {label}</span>
      <span className="col-span-5 flex justify-between">
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
        <ResultAxis label={`${board.columns.system} · ${board.columns.evidence}`} metric={board.columns.score} rank={board.columns.rank} />
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
        <ResultAxis
          label={`${board.columns.system} · ${board.columns.provider} / ${board.columns.harness}`}
          metric={board.columns.score}
          rank={board.columns.rank}
        />
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
  const [activeTab, setActiveTab] = useState<'locomo' | 'swe'>('locomo');

  return (
    <div>
      <div aria-label={benchmark.leaderboards.tabs_label} className="mb-6 flex gap-6 overflow-x-auto border-b border-fd-border" role="tablist">
        <button
          aria-controls="locomo-results-panel"
          aria-selected={activeTab === 'locomo'}
          className={`whitespace-nowrap border-b-2 pb-2.5 text-sm ${activeTab === 'locomo' ? 'border-fd-primary font-medium text-fd-foreground' : 'border-transparent text-fd-muted-foreground'}`}
          id="locomo-results-tab"
          onClick={() => setActiveTab('locomo')}
          role="tab"
          type="button"
        >
          {benchmark.leaderboards.locomo.tab}
        </button>
        <button
          aria-controls="swe-results-panel"
          aria-selected={activeTab === 'swe'}
          className={`whitespace-nowrap border-b-2 pb-2.5 text-sm ${activeTab === 'swe' ? 'border-fd-primary font-medium text-fd-foreground' : 'border-transparent text-fd-muted-foreground'}`}
          id="swe-results-tab"
          onClick={() => setActiveTab('swe')}
          role="tab"
          type="button"
        >
          {benchmark.leaderboards.swe.tab}
        </button>
      </div>
      {activeTab === 'locomo' ? <LocomoResults benchmark={benchmark} lang={lang} /> : <SweResults benchmark={benchmark} lang={lang} />}
    </div>
  );
}
