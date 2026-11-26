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

import type { CSSProperties } from 'react';
import type { BenchmarkContent, BenchmarkMetricRow } from '@/lib/benchmark-content';
import { CountUp, RevealOnView } from './benchmark-motion';
import styles from './benchmarks.module.css';

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
                <circle className={isPrimary ? 'fill-primary' : 'fill-fd-muted-foreground'} cx={x(point.latency)} cy={y(point.accuracy)} r={isPrimary ? 7 : 6} />
                <text
                  className={isPrimary ? 'fill-primary text-sm font-semibold' : 'fill-fd-foreground text-sm'}
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
              <tr className={index === 0 ? 'text-primary-text' : ''} key={point.name}>
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
      <RevealOnView className="flex h-3 overflow-hidden rounded-full bg-fd-muted">
        {categories.map((category, index) => (
          <span
            aria-hidden="true"
            className={`pc-meter-fill bg-fd-foreground ${opacity[index] ?? 'opacity-30'}`}
            key={category.name}
            style={{ '--w': `${(counts[index] / total) * 100}%`, '--i': index } as CSSProperties}
          />
        ))}
      </RevealOnView>
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

export type ChartColumn = {
  display: string;
  /** The result being reported, drawn in the accent; comparisons stay neutral. */
  highlight?: boolean;
  label: string;
  value: number;
};

/**
 * Columns on a shared scale, each with its value above and its name below.
 * Groups sit side by side under their own label, so paired runs read as
 * pairs. The figure carries the whole reading for assistive technology.
 */
export function ColumnChart({ groups, label, max }: {
  groups: Array<{ columns: ChartColumn[]; label?: string }>;
  label: string;
  /** The top of the scale; defaults to the largest value. */
  max?: number;
}) {
  const top = max ?? Math.max(...groups.flatMap((group) => group.columns.map((column) => column.value)));
  // The drawing is hidden from assistive technology, so the name carries every reading.
  const readings = groups.map((group) => {
    const values = group.columns.map((column) => `${column.label} ${column.display}`).join(', ');
    return group.label ? `${group.label}: ${values}` : values;
  }).join('; ');
  return (
    <figure aria-label={`${label} ${readings}.`} role="img">
      <RevealOnView className={styles.chart}>
      {groups.map((group, index) => (
        <div aria-hidden="true" className={styles.group} key={group.label ?? index}>
          <div className={styles.columns}>
            {group.columns.map((column, place) => (
              <div className={styles.column} key={column.label}>
                <div className={styles.track}>
                  <span className={styles.bar} data-highlight={column.highlight ? '' : undefined}
                    style={{ '--h': `${(column.value / top) * 100}%`, '--i': index * group.columns.length + place } as CSSProperties}>
                    <span className={styles.value}><CountUp delay={(index * group.columns.length + place) * 0.08} value={column.display} /></span>
                  </span>
                </div>
                {/* In a group the legend names each column; the group label names the pair. */}
                {group.label ? null : <span className={styles.label} data-highlight={column.highlight ? '' : undefined}>{column.label}</span>}
              </div>
            ))}
          </div>
          {group.label ? <span className={styles.groupLabel}>{group.label}</span> : null}
        </div>
      ))}
      </RevealOnView>
    </figure>
  );
}

/** Names the two tones of a paired chart. */
export function ChartLegend({ base, result }: { base: string; result: string }) {
  return (
    <p className={styles.legend}>
      <span>{base}</span>
      <span data-highlight="">{result}</span>
    </p>
  );
}
