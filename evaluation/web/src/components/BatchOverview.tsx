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

import { useCallback, useEffect, useRef, useState, type MouseEvent } from "react";

import type { EvaluationApi } from "../api";
import { batchStatusLabel } from "../batchStatus";
import type { BatchRecord, BatchReport, PairCategory, TokenMetricAggregate } from "../types";
import { BatchControls } from "./BatchControls";

interface BatchOverviewProps {
  api: EvaluationApi;
  batchId: string;
  navigate(path: string): void;
}

const pairLabels: Record<Exclude<PairCategory, "execution_failure">, string> = {
  off_fail_on_pass: "OFF 未通过 / ON 通过",
  off_pass_on_fail: "OFF 通过 / ON 未通过",
  both_pass: "OFF / ON 均通过",
  both_fail: "OFF / ON 均未通过",
};

function number(value: number): string {
  return new Intl.NumberFormat("zh-CN").format(value);
}

function percent(value: number): string {
  return `${new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 2 }).format(value)}%`;
}

function signed(value: number, suffix = ""): string {
  return `${value > 0 ? "+" : ""}${new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 2 }).format(value)}${suffix}`;
}

export function BatchOverview({ api, batchId, navigate }: BatchOverviewProps) {
  const [batch, setBatch] = useState<BatchRecord | null>(null);
  const [report, setReport] = useState<BatchReport | null>(null);
  const [error, setError] = useState(false);
  const generation = useRef(0);
  const controller = useRef<AbortController | null>(null);

  const load = useCallback(() => {
    controller.current?.abort();
    const nextController = new AbortController();
    controller.current = nextController;
    const currentGeneration = ++generation.current;
    setError(false);
    Promise.all([
      api.getBatch(batchId, nextController.signal),
      api.getBatchReport(batchId, nextController.signal),
    ])
      .then(([nextBatch, nextReport]) => {
        if (nextController.signal.aborted || currentGeneration !== generation.current) return;
        setBatch(nextBatch);
        setReport(nextReport);
      })
      .catch(() => {
        if (!nextController.signal.aborted && currentGeneration === generation.current) setError(true);
      });
  }, [api, batchId]);

  useEffect(() => {
    load();
    return () => {
      controller.current?.abort();
      generation.current += 1;
    };
  }, [load]);

  const onLink = (event: MouseEvent<HTMLAnchorElement>, path: string) => {
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    navigate(path);
  };

  if (error) {
    return (
      <section className="panel empty-state">
        <p>批次总体数据暂时无法加载。</p>
        <button type="button" className="secondary-button" onClick={load}>重试</button>
      </section>
    );
  }
  if (batch === null || report === null) {
    return <section className="panel state-message">正在读取批次总体数据…</section>;
  }

  const encodedBatchId = encodeURIComponent(batchId);
  const categories = Object.entries(pairLabels) as [Exclude<PairCategory, "execution_failure">, string][];
  const taskListPath = `/report/${encodedBatchId}/tasks`;
  const progress = `${report.terminal_tasks} / ${report.total_tasks}`;
  const status = batchStatusLabel[batch.status];

  return (
    <div className="batch-overview">
      <div className="breadcrumb">评测报告 / {batch.batch_id}</div>
      <header className="report-page-head">
        <div>
          <h1>总体报告</h1>
          <p>
            SWE-bench Pro public v2 · {number(batch.total_tasks)} 个任务 · PowerContext{" "}
            {(batch.resolved_powercontext_sha ?? batch.request.powercontext_ref).slice(0, 12)} ·{" "}
            {batch.request.model} · {batch.request.reasoning_effort}
          </p>
        </div>
        <span className="batch-status">{status} · {progress}</span>
      </header>

      <BatchControls api={api} batch={batch} report={report} onUpdated={load} />

      <section className="kpi-grid" aria-label="正确性汇总">
        <MetricCard label="总任务数" value={number(report.total_tasks)} detail={`${progress} 已结束`} />
        <MetricCard
          label="OFF 解决率"
          value={percent(report.off.rate_percent)}
          detail={`${number(report.off.resolved)} / ${number(report.off.total)} 个任务`}
        />
        <MetricCard
          label="ON 解决率"
          value={percent(report.on.rate_percent)}
          detail={`${number(report.on.resolved)} / ${number(report.on.total)} 个任务`}
        />
        <MetricCard
          label="解决率差值"
          value={signed(report.resolution_rate_delta_points, " pp")}
          detail="ON − OFF"
        />
      </section>

      <section className="report-section report-section--batch">
        <div className="section-heading">
          <div>
            <h2>实验对比结果</h2>
            <p>可比较任务 {number(report.comparable_pairs)} / {number(report.total_tasks)}</p>
          </div>
          <a href={taskListPath} onClick={(event) => onLink(event, taskListPath)}>查看全部任务</a>
        </div>
        <div className="pair-grid">
          {categories.map(([category, label]) => {
            const count = report.pair_categories[category];
            const path = `${taskListPath}?category=${category}`;
            return (
              <a
                className={`pair-card pair-card--${category}`}
                href={path}
                onClick={(event) => onLink(event, path)}
                key={category}
              >
                <span>{label}</span>
                <strong>{number(count)}</strong>
                <small>{number(count)} / {number(report.comparable_pairs)} 个可比较任务</small>
              </a>
            );
          })}
        </div>
        {(report.execution_failures > 0 || report.cancelled_tasks > 0) && (
          <div className="factual-failures">
            <span>评测执行失败 {number(report.execution_failures)}</span>
            <span>已取消 {number(report.cancelled_tasks)}</span>
          </div>
        )}
      </section>

      <section className="report-section report-section--batch">
        <div className="section-heading">
          <div>
            <h2>Token 总量</h2>
            <p>分别展示输入、输出和总 Token 的 OFF / ON 事实值。</p>
          </div>
        </div>
        <div className="token-grid">
          <TokenCard label="输入 Token" metric={report.tokens.input} total={report.total_tasks} />
          <TokenCard label="输出 Token" metric={report.tokens.output} total={report.total_tasks} />
          <TokenCard label="总 Token" metric={report.tokens.total} total={report.total_tasks} />
        </div>
      </section>
    </div>
  );
}

function MetricCard({ label, value, detail }: { label: string; value: string; detail: string }) {
  return (
    <article className="metric-card">
      <span>{label}</span>
      <strong>{value}</strong>
      <small>{detail}</small>
    </article>
  );
}

function TokenCard({ label, metric, total }: { label: string; metric: TokenMetricAggregate; total: number }) {
  const measured = Math.min(metric.off_measured_tasks, metric.on_measured_tasks);
  const deltaPercent = metric.off === 0 ? null : metric.delta / metric.off * 100;
  return (
    <article className="token-card">
      <h3>{label}</h3>
      <dl>
        <div><dt>OFF</dt><dd>{number(metric.off)}</dd></div>
        <div><dt>ON</dt><dd>{number(metric.on)}</dd></div>
        <div>
          <dt>差值</dt>
          <dd>{signed(metric.delta)}{deltaPercent === null ? "" : ` (${signed(deltaPercent, "%")})`}</dd>
        </div>
      </dl>
      <p>{number(measured)} / {number(total)} 个任务有记录</p>
    </article>
  );
}
