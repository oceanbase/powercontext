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

import { useEffect, useId, useRef, useState } from "react";
import type {
  DesktopState,
  ExactScopeStatistics,
  StatsPeriod,
} from "../generated/ipc";
import { desktopApi } from "../shared/ipc";
import { connectionError } from "./connection-messages";
import type { Language } from "./messages";
import { statisticsMessages } from "./statistics-messages";

type Props = {
  state: DesktopState | null;
  language: Language;
  onState: (state: DesktopState) => void;
  contextPending?: boolean;
};
type Cell = string | number | null;
const operations = ["generation", "embedding"] as const;
const metrics = ["requests", "input_tokens", "output_tokens"] as const;
const recallFields = [
  "preparations",
  "ready_preparations",
  "comparable_preparations",
  "baseline_tokens",
  "recalled_tokens",
  "token_reduction",
] as const;

/** Load statistics explicitly, using the native generation to isolate private results. */
export function Statistics({
  state,
  language,
  onState,
  contextPending = false,
}: Props) {
  const t = statisticsMessages[language];
  const titleId = useId();
  const generation = state?.active?.generation;
  const active = state?.active;
  const enabled =
    !contextPending &&
    !!active?.scope &&
    active.report.compatibilityVerified &&
    active.report.supportedOperations.includes("get_stats");
  const [period, setPeriod] = useState<StatsPeriod>("30d");
  const [metric, setMetric] = useState<(typeof metrics)[number]>("requests");
  const [result, setResult] = useState<{
    generation: number;
    period: StatsPeriod;
    data: ExactScopeStatistics;
  } | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const sequence = useRef(0);
  const running = useRef(false);
  const pendingCancel = useRef(Promise.resolve());
  const context = useRef({ generation, enabled, period });
  context.current = { generation, enabled, period };
  useEffect(() => {
    setResult(null);
    setError(null);
    setBusy(false);
    running.current = false;
    return () => {
      sequence.current++;
      if (generation !== undefined) {
        pendingCancel.current = pendingCancel.current
          .then(() => desktopApi.cancelStats(generation))
          .catch(() => {});
      }
    };
  }, [generation, enabled]);
  function changePeriod(next: StatsPeriod) {
    sequence.current++;
    running.current = false;
    setPeriod(next);
    setResult(null);
    setError(null);
    setBusy(false);
    if (generation !== undefined) {
      pendingCancel.current = pendingCancel.current
        .then(() => desktopApi.cancelStats(generation))
        .catch(() => {});
    }
  }
  async function load() {
    if (!enabled || generation === undefined || running.current) return;
    const ticket = ++sequence.current;
    const isCurrent = () =>
      ticket === sequence.current &&
      context.current.generation === generation &&
      context.current.enabled &&
      context.current.period === period;
    running.current = true;
    setBusy(true);
    setResult(null);
    setError(null);
    try {
      await pendingCancel.current;
      if (!isCurrent()) return;
      const data = await desktopApi.stats(generation, period);
      if (isCurrent()) setResult({ generation, period, data });
    } catch (failure) {
      if (isCurrent()) {
        setError(failure);
        try {
          const next = await desktopApi.state();
          if (isCurrent()) onState(next);
        } catch {
          /* Keep the operation's safe error. */
        }
      }
    } finally {
      if (ticket === sequence.current) {
        running.current = false;
        setBusy(false);
      }
    }
  }
  const data =
    result &&
    enabled &&
    result.generation === generation &&
    result.period === period
      ? result.data
      : null;
  return (
    <section className="card stack statistics" aria-labelledby={titleId}>
      <h2 id={titleId}>{t.title}</h2>
      {contextPending ? (
        <p role="status">{t.contextPending}</p>
      ) : !active?.scope ? (
        <p>{t.noScope}</p>
      ) : !enabled ? (
        <p>{t.unavailable}</p>
      ) : (
        <>
          <p>
            {t.scope}: <strong>{active.scope.title}</strong>{" "}
            <code>{active.scope.scope_id}</code>
          </p>
          <div className="actions statistics-controls">
            <label>
              {t.period}{" "}
              <select
                value={period}
                onChange={(event) =>
                  changePeriod(event.target.value as StatsPeriod)
                }
              >
                {(["today", "7d", "30d"] as const).map((value) => (
                  <option key={value} value={value}>
                    {t[value]}
                  </option>
                ))}
              </select>
            </label>
            <button disabled={busy} onClick={() => void load()}>
              {t.refresh}
            </button>
          </div>
          {busy && <p role="status">{t.loading}</p>}
          {error != null && (
            <p role="alert">{connectionError(error, language)}</p>
          )}
          {!busy && error == null && !data && <p>{t.initial}</p>}
          {data && (
            <StatisticsData
              data={data}
              language={language}
              metric={metric}
              onMetric={setMetric}
            />
          )}
        </>
      )}
    </section>
  );
}

function StatisticsData({
  data,
  language,
  metric,
  onMetric,
}: {
  data: ExactScopeStatistics;
  language: Language;
  metric: (typeof metrics)[number];
  onMetric: (metric: (typeof metrics)[number]) => void;
}) {
  const t = statisticsMessages[language];
  const { inventory, usage, recall } = data;
  const inventoryRows: Cell[][] = [
    [t.sources, inventory.sources.total],
    [t.processed, inventory.sources.memory_processed],
    [t.pendingSources, inventory.sources.memory_pending],
    [t.artifacts, inventory.artifacts.total],
    [t.candidates, inventory.candidates.total],
    [t.pending, inventory.candidates.pending],
    [t.approved, inventory.candidates.approved],
    [t.rejected, inventory.candidates.rejected],
    [t.memories, inventory.memory.entries.total],
    [t.active, inventory.memory.entries.active],
    [t.inactive, inventory.memory.entries.inactive],
  ];
  const table = (caption: string, columns: string[], rows: Cell[][]) => (
    <DataTable
      caption={caption}
      columns={columns}
      rows={rows}
      language={language}
    />
  );
  const usageColumns = [t.operation, ...metrics.map((key) => t[key])];
  return (
    <>
      <dl className="fact-grid">
        <div className="fact">
          <dt>{t.asOf}</dt>
          <dd>
            <time dateTime={data.as_of}>{data.as_of}</time>
          </dd>
        </div>
        <div className="fact">
          <dt>{t.coverage}</dt>
          <dd>
            {data.scope_ids.length} / 1 ·{" "}
            <code>{data.scope_ids.join(", ")}</code>
          </dd>
        </div>
        <div className="fact">
          <dt>{t.dates}</dt>
          <dd>
            {usage.period.start_date} – {usage.period.end_date} ·{" "}
            {usage.period.timezone}
          </dd>
        </div>
      </dl>
      <section className="stack">
        <h3>{t.inventory}</h3>
        <p className="small">{t.snapshot}</p>
        <Bars
          title={t.inventory}
          rows={[
            [t.sources, inventory.sources.total],
            [t.artifacts, inventory.artifacts.total],
            [t.candidates, inventory.candidates.total],
            [t.memories, inventory.memory.entries.total],
          ]}
          language={language}
        />
        {table(t.inventory, [t.item, t.count], inventoryRows)}
        {table(
          t.artifactFamilies,
          [t.family, t.total],
          inventory.artifacts.by_family.map((row) => [row.family, row.total]),
        )}
        {table(
          t.candidateFamilies,
          [t.family, t.total, t.pending, t.approved, t.rejected],
          inventory.candidates.by_family.map((row) => [
            row.family,
            row.total,
            row.pending,
            row.approved,
            row.rejected,
          ]),
        )}
        {table(
          t.memoryKinds,
          [t.family, t.total, t.active, t.inactive],
          inventory.memory.entries.by_kind.map((row) => [
            row.kind,
            row.total,
            row.active,
            row.inactive,
          ]),
        )}
      </section>
      <section className="stack">
        <h3>{t.usage}</h3>
        <p className="small">{t.usageNote}</p>
        {table(
          t.totals,
          usageColumns,
          operations.map((operation) => [
            t[operation],
            ...metrics.map((key) => usage.totals[operation][key]),
          ]),
        )}
        <label>
          {t.metric}{" "}
          <select
            value={metric}
            onChange={(event) => onMetric(event.target.value as typeof metric)}
          >
            {metrics.map((key) => (
              <option key={key} value={key}>
                {t[key]}
              </option>
            ))}
          </select>
        </label>
        <Bars
          title={`${t.daily} · ${t[metric]}`}
          rows={usage.daily.flatMap((day) =>
            operations.map((operation): [string, number | null] => [
              `${day.date} · ${t[operation]}`,
              day[operation][metric],
            ]),
          )}
          language={language}
        />
        {table(
          t.daily,
          [t.date, ...usageColumns],
          usage.daily.flatMap((day) =>
            operations.map((operation) => [
              day.date,
              t[operation],
              ...metrics.map((key) => day[operation][key]),
            ]),
          ),
        )}
        {table(
          t.purposes,
          [t.purpose, ...usageColumns],
          usage.by_purpose.flatMap((row) =>
            operations.map((operation) => [
              row.purpose,
              t[operation],
              ...metrics.map((key) => row[operation][key]),
            ]),
          ),
        )}
      </section>
      <section className="stack">
        <h3>{t.recall}</h3>
        <p className="small">{t.recallNote}</p>
        <p>
          {t.estimator}:{" "}
          {recall.estimator
            ? `${recall.estimator.estimator_id} / ${recall.estimator.version}`
            : t.unknown}
        </p>
        {table(
          t.recall,
          [t.item, t.count],
          recallFields.map((key) => [t[key], recall.totals[key]]),
        )}
        <Bars
          title={`${t.recallDaily} · ${t.token_reduction}`}
          rows={recall.daily.map((day) => [day.date, day.token_reduction])}
          language={language}
        />
        {table(
          t.recallDaily,
          [t.date, ...recallFields.map((key) => t[key])],
          recall.daily.map((day) => [
            day.date,
            ...recallFields.map((key) => day[key]),
          ]),
        )}
      </section>
    </>
  );
}

function DataTable({
  caption,
  columns,
  rows,
  language,
}: {
  caption: string;
  columns: string[];
  rows: Cell[][];
  language: Language;
}) {
  const t = statisticsMessages[language];
  const format = new Intl.NumberFormat(language);
  return (
    <div
      className="statistics-table"
      role="region"
      aria-label={caption}
      tabIndex={0}
    >
      <table>
        <caption>{caption}</caption>
        <thead>
          <tr>
            {columns.map((column) => (
              <th key={column} scope="col">
                {column}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.length ? (
            rows.map((row, index) => (
              <tr key={index}>
                {row.map((cell, column) =>
                  column === 0 ? (
                    <th key={column} scope="row">
                      {cell}
                    </th>
                  ) : (
                    <td key={column}>
                      {cell === null
                        ? t.unknown
                        : typeof cell === "number"
                          ? format.format(cell)
                          : cell}
                    </td>
                  ),
                )}
              </tr>
            ))
          ) : (
            <tr>
              <td colSpan={columns.length}>{t.noRows}</td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

function Bars({
  title,
  rows,
  language,
}: {
  title: string;
  rows: [string, number | null][];
  language: Language;
}) {
  const t = statisticsMessages[language];
  if (!rows.length) return null;
  const values = rows.flatMap(([, value]) => (value === null ? [] : [value]));
  const minimum = Math.min(0, ...values);
  const maximum = Math.max(0, ...values);
  const position = (value: number) =>
    220 + (240 * (value - minimum)) / (maximum - minimum || 1);
  const zero = position(0);
  const format = new Intl.NumberFormat(language);
  return (
    <div
      className="statistics-chart"
      role="region"
      aria-label={title}
      tabIndex={0}
    >
      <svg
        viewBox={`0 0 560 ${rows.length * 32 + 8}`}
        role="img"
        aria-label={title}
      >
        <title>{title}</title>
        <desc>{t.chartNote}</desc>
        <line
          x1={zero}
          x2={zero}
          y1={0}
          y2={rows.length * 32}
          className="statistics-zero"
        />
        {rows.map(([label, value], index) => (
          <g key={index}>
            <text x={0} y={index * 32 + 22}>
              {label.length > 30 ? `${label.slice(0, 29)}…` : label}
            </text>
            {value !== null && (
              <rect
                x={Math.min(zero, position(value))}
                y={index * 32 + 7}
                width={Math.abs(position(value) - zero)}
                height={19}
                rx={3}
              />
            )}
            <text x={550} y={index * 32 + 22} textAnchor="end">
              {value === null ? t.unknown : format.format(value)}
            </text>
          </g>
        ))}
      </svg>
    </div>
  );
}
