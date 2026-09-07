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

import type { BenchmarkContent, BenchmarkMetricRow } from '@/lib/benchmark-content';

const chartCopy = {
  en: {
    accuracy: 'Accuracy',
    accuracyAxis: 'Accuracy (%)',
    searchAxis: 'Search p95 (s)',
    searchP95: 'Search p95',
    second: 's',
    system: 'System',
  },
  zh: {
    accuracy: '准确率',
    accuracyAxis: '准确率（%）',
    searchAxis: '搜索 p95（秒）',
    searchP95: '搜索 p95',
    second: '秒',
    system: '系统',
  },
} as const;

type TradeoffPoint = {
  accuracy: number;
  latency: number;
  name: string;
};

function combineMetrics(accuracy: BenchmarkMetricRow[], latency: BenchmarkMetricRow[]): TradeoffPoint[] {
  const latencyByName = new Map(latency.map((row) => [row.name, row.value]));

  return accuracy.flatMap((row) => {
    const latencyValue = latencyByName.get(row.name);
    return latencyValue === undefined ? [] : [{ accuracy: row.value, latency: latencyValue, name: row.name }];
  });
}

export function LocomoTradeoffChart({
  accuracy,
  label,
  lang,
  latency,
}: {
  accuracy: BenchmarkMetricRow[];
  label: string;
  lang: 'en' | 'zh';
  latency: BenchmarkMetricRow[];
}) {
  const copy = chartCopy[lang];
  const points = combineMetrics(accuracy, latency);
  const plot = { bottom: 184, left: 60, right: 688, top: 26 };
  const x = (value: number) => plot.left + (value / 18) * (plot.right - plot.left);
  const y = (value: number) => plot.bottom - ((value - 50) / 42) * (plot.bottom - plot.top);

  return (
    <figure aria-label={label}>
      <div className="hidden lg:block">
        <svg className="mx-auto h-auto w-full max-w-3xl overflow-visible" role="img" viewBox="0 0 720 240">
          <title>{label}</title>
          {[50, 70, 90].map((tick) => (
            <g key={tick}>
              <line className="stroke-fd-border" strokeDasharray="3 3" x1={plot.left} x2={plot.right} y1={y(tick)} y2={y(tick)} />
              <text className="fill-fd-muted-foreground text-xs" textAnchor="end" x={plot.left - 12} y={y(tick) + 4}>{tick}</text>
            </g>
          ))}
          {[0, 5, 10, 15].map((tick) => (
            <g key={tick}>
              <line className="stroke-fd-border" strokeDasharray="3 3" x1={x(tick)} x2={x(tick)} y1={plot.top} y2={plot.bottom} />
              <text className="fill-fd-muted-foreground text-xs" textAnchor="middle" x={x(tick)} y="211">{tick}</text>
            </g>
          ))}
          <text className="fill-fd-muted-foreground text-xs" textAnchor="middle" x="374" y="235">{copy.searchAxis}</text>
          <text className="fill-fd-muted-foreground text-xs" textAnchor="middle" transform="translate(16 105) rotate(-90)">{copy.accuracyAxis}</text>
          {points.map((point, index) => {
            const isPrimary = index === 0;
            const isFullContext = index === points.length - 1;
            const labelText = `${point.name} · ${point.accuracy}% · ${point.latency} ${copy.second}`;

            return (
              <g key={point.name}>
                <circle className={isPrimary ? 'fill-fd-primary' : 'fill-fd-muted-foreground'} cx={x(point.latency)} cy={y(point.accuracy)} r={isPrimary ? 7 : 6} />
                <text
                  className={isPrimary ? 'fill-fd-primary text-sm font-semibold' : 'fill-fd-foreground text-sm'}
                  textAnchor={isFullContext ? 'end' : 'start'}
                  x={x(point.latency) + (isFullContext ? -14 : 14)}
                  y={y(point.accuracy) + (index === 1 ? 17 : -7)}
                >
                  {labelText}
                </text>
              </g>
            );
          })}
        </svg>
      </div>
      <div className="overflow-x-auto lg:hidden">
        <table className="w-full border-collapse text-sm">
          <thead>
            <tr className="border-b border-fd-border text-left text-fd-muted-foreground">
              <th className="px-3 py-2 font-medium">{copy.system}</th>
              <th className="px-3 py-2 font-medium">{copy.accuracy}</th>
              <th className="px-3 py-2 font-medium">{copy.searchP95}</th>
            </tr>
          </thead>
          <tbody>
            {points.map((point, index) => (
              <tr className={index === 0 ? 'text-fd-primary' : ''} key={point.name}>
                <th className="border-b border-fd-border px-3 py-2 text-left font-medium">{point.name}</th>
                <td className="border-b border-fd-border px-3 py-2 tabular-nums">{point.accuracy}%</td>
                <td className="border-b border-fd-border px-3 py-2 tabular-nums">{point.latency} {copy.second}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </figure>
  );
}

function parseCount(value: string) {
  return Number.parseInt(value.replaceAll(',', ''), 10);
}

export function LocomoComposition({ categories }: { categories: BenchmarkContent['locomo']['categories'] }) {
  const counts = categories.map((category) => parseCount(category.count));
  const total = counts.reduce((sum, count) => sum + count, 0);
  const opacity = ['opacity-100', 'opacity-75', 'opacity-50', 'opacity-30'];

  return (
    <figure aria-label={categories.map((category) => `${category.name} ${category.count}`).join(', ')}>
      <div className="flex h-3 overflow-hidden bg-fd-muted">
        {categories.map((category, index) => (
          <span
            aria-hidden="true"
            className={`bg-fd-primary ${opacity[index] ?? 'opacity-30'}`}
            key={category.name}
            style={{ width: `${(counts[index] / total) * 100}%` }}
          />
        ))}
      </div>
      <figcaption className="mt-6 grid sm:grid-cols-2">
        {categories.map((category, index) => (
          <div
            className={`flex justify-between border-t border-fd-border py-3 text-sm ${index % 2 === 0 ? 'sm:mr-6' : ''}`}
            key={category.name}
          >
            <span>{category.name}</span>
            <strong className="font-medium tabular-nums text-fd-muted-foreground">{category.count}</strong>
          </div>
        ))}
      </figcaption>
    </figure>
  );
}

export function SwePairedChart({
  label,
  lang,
  scores,
  taskCount,
}: {
  label: string;
  lang: 'en' | 'zh';
  scores: BenchmarkContent['swe']['scores'];
  taskCount: number;
}) {
  const results = scores.map((score) => ({ ...score, percentage: (score.count / taskCount) * 100 }));
  const off = results.find((score) => score.kind === 'off') ?? results[0];
  const on = results.find((score) => score.kind === 'on') ?? results.at(-1);
  const delta = on && off ? on.count - off.count : 0;
  const percentagePointDelta = on && off ? on.percentage - off.percentage : 0;
  const deltaLabel = lang === 'zh'
    ? `+${percentagePointDelta.toFixed(2)} 个百分点 · 多解决 ${delta} 个任务`
    : `+${percentagePointDelta.toFixed(2)} pp · ${delta} more tasks`;

  return (
    <figure aria-label={label}>
      <div className="grid gap-6">
        {results.map((score) => (
          <div aria-label={score.accessible} key={score.kind}>
            <div className="mb-2 flex flex-wrap items-baseline justify-between gap-x-5 gap-y-1 text-sm">
              <span className="font-medium">{score.label}</span>
              <span className="tabular-nums text-fd-muted-foreground">
                {score.count} / {taskCount} · {score.percentage.toFixed(2)}%
              </span>
            </div>
            <div aria-hidden="true" className="h-3 bg-fd-muted">
              <div
                className={`h-full ${score.kind === 'on' ? 'bg-fd-primary' : 'bg-fd-muted-foreground/50'}`}
                style={{ width: `${score.percentage}%` }}
              />
            </div>
          </div>
        ))}
      </div>
      <figcaption className="mt-4 text-sm font-medium text-fd-primary">{deltaLabel}</figcaption>
    </figure>
  );
}
