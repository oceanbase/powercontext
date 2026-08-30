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
import type {
  Arm,
  BaselineCandidate,
  BaselineComparisonResponse,
  BaselineSelection,
  BatchRecord,
  BatchReport,
  PairCategory,
  TokenMetricAggregate,
} from "../types";
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
  const [candidates, setCandidates] = useState<BaselineCandidate[]>([]);
  const [selections, setSelections] = useState<BaselineSelection[]>([]);
  const [comparisons, setComparisons] = useState<BaselineComparisonResponse | null>(null);
  const [comparisonArm, setComparisonArm] = useState<Arm>("on");
  const [baselineName, setBaselineName] = useState("");
  const [baselineMessage, setBaselineMessage] = useState("");
  const [baselinePending, setBaselinePending] = useState(false);
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
      api.listBaselineSelections(batchId, nextController.signal),
      api.getBaselineComparisons(batchId, nextController.signal),
    ])
      .then(([nextBatch, nextReport, nextSelections, nextComparisons]) => {
        if (nextController.signal.aborted || currentGeneration !== generation.current) return;
        setBatch(nextBatch);
        setReport(nextReport);
        setSelections(nextSelections);
        setComparisons(nextComparisons);
        const nextArm = nextBatch.request.treatment_mode === "off_only" ? "off" : "on";
        setComparisonArm(nextArm);
        return api.listBaselineCandidates(batchId, nextArm, nextController.signal);
      })
      .then((nextCandidates) => {
        if (nextCandidates !== undefined && !nextController.signal.aborted && currentGeneration === generation.current) {
          setCandidates(nextCandidates);
        }
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

  const refreshCandidates = async (arm: Arm) => {
    setComparisonArm(arm);
    try {
      setCandidates(await api.listBaselineCandidates(batchId, arm));
    } catch {
      setBaselineMessage("当前无法读取兼容基线。");
    }
  };

  const saveBaseline = async (arm: Arm) => {
    if (report === null || batch === null || baselinePending) return;
    const name = baselineName.trim() || `${batch.batch_id} ${arm.toUpperCase()}`;
    setBaselinePending(true);
    setBaselineMessage("");
    try {
      await api.createBaseline({
        name,
        source_batch_id: batch.batch_id,
        source_arm: arm,
        expected_report_revision: report.report_revision,
        idempotency_key: `web-baseline-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`,
      });
      setBaselineName("");
      setBaselineMessage(`${arm.toUpperCase()} 基线已保存。`);
      await refreshCandidates(comparisonArm);
    } catch {
      setBaselineMessage("基线保存失败，请刷新报告后重试。");
    } finally {
      setBaselinePending(false);
    }
  };

  const toggleBaseline = async (baselineId: string, checked: boolean) => {
    const retained = selections.filter(
      (selection) => !(selection.baseline_id === baselineId && selection.current_arm === comparisonArm),
    );
    const next = checked ? [...retained, { baseline_id: baselineId, current_arm: comparisonArm }] : retained;
    setBaselinePending(true);
    setBaselineMessage("");
    try {
      const saved = await api.updateBaselineSelections(batchId, next);
      setSelections(saved);
      setComparisons(await api.getBaselineComparisons(batchId));
    } catch {
      setBaselineMessage("基线选择更新失败。");
    } finally {
      setBaselinePending(false);
    }
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
  const paired = report.treatment_mode === "off_on" && report.off !== null && report.on !== null;
  const singleArm = report.treatment_mode === "off_only" ? "off" : "on";
  const singleResolution = singleArm === "off" ? report.off : report.on;
  const pairCategories = report.pair_categories;
  const comparablePairs = report.comparable_pairs;

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
        {paired && report.off !== null && report.on !== null && report.resolution_rate_delta_points !== null ? (
          <>
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
          </>
        ) : singleResolution !== null ? (
          <>
            <MetricCard
              label={`${singleArm.toUpperCase()} 解决率`}
              value={percent(singleResolution.rate_percent)}
              detail={`${number(singleResolution.resolved)} / ${number(singleResolution.total)} 个任务`}
            />
            <MetricCard label="执行失败" value={number(report.execution_failures)} detail="不计为历史翻转" />
            <MetricCard label="已取消" value={number(report.cancelled_tasks)} detail="没有生成 Arm 结果" />
          </>
        ) : null}
      </section>

      {paired && pairCategories !== null && comparablePairs !== null && <section className="report-section report-section--batch">
        <div className="section-heading">
          <div>
            <h2>实验对比结果</h2>
            <p>可比较任务 {number(comparablePairs)} / {number(report.total_tasks)}</p>
          </div>
          <a href={taskListPath} onClick={(event) => onLink(event, taskListPath)}>查看全部任务</a>
        </div>
        <div className="pair-grid">
          {categories.map(([category, label]) => {
            const count = pairCategories[category];
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
                <small>{number(count)} / {number(comparablePairs)} 个可比较任务</small>
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
      </section>}

      <section className="report-section report-section--batch">
        <div className="section-heading">
          <div>
            <h2>Token 总量</h2>
            <p>分别展示输入、输出和总 Token 的 OFF / ON 事实值。</p>
          </div>
        </div>
        <div className="token-grid">
          {paired ? (
            <>
              <TokenCard label="输入 Token" metric={report.tokens.input} total={report.total_tasks} />
              <TokenCard label="输出 Token" metric={report.tokens.output} total={report.total_tasks} />
              <TokenCard label="总 Token" metric={report.tokens.total} total={report.total_tasks} />
            </>
          ) : (
            <>
              <SingleTokenCard label="输入 Token" metric={report.tokens.input} arm={singleArm} total={report.total_tasks} />
              <SingleTokenCard label="输出 Token" metric={report.tokens.output} arm={singleArm} total={report.total_tasks} />
              <SingleTokenCard label="总 Token" metric={report.tokens.total} arm={singleArm} total={report.total_tasks} />
            </>
          )}
        </div>
      </section>

      <section className="report-section report-section--batch baseline-comparison-section">
        <div className="section-heading">
          <div>
            <h2>历史基线对比</h2>
            <p>可选择多个基线；增删对比不会重新运行任务。</p>
          </div>
          {batch.request.treatment_mode === "off_on" && (
            <select
              aria-label="当前对比 Arm"
              value={comparisonArm}
              onChange={(event) => refreshCandidates(event.target.value as Arm)}
            >
              <option value="on">当前 ON</option>
              <option value="off">当前 OFF</option>
            </select>
          )}
        </div>
        <div className="baseline-picker">
          {candidates.filter((candidate) => candidate.compatibility.status !== "incompatible").length === 0 ? (
            <p className="field-hint">没有兼容基线。可以在下方先保存当前结果。</p>
          ) : candidates.filter((candidate) => candidate.compatibility.status !== "incompatible").map((candidate) => (
            <label className="checkbox-field" key={candidate.baseline.baseline_id}>
              <input
                type="checkbox"
                disabled={baselinePending}
                checked={selections.some((selection) => selection.baseline_id === candidate.baseline.baseline_id
                  && selection.current_arm === comparisonArm)}
                onChange={(event) => toggleBaseline(candidate.baseline.baseline_id, event.target.checked)}
              />
              {candidate.baseline.name} · {candidate.baseline.source_arm.toUpperCase()}
              {candidate.compatibility.status === "warning" && " · 跨臂"}
            </label>
          ))}
        </div>
        {comparisons?.comparisons.map((comparison) => (
          <article className="baseline-comparison-card" key={`${comparison.baseline.baseline_id}-${comparison.current_arm}`}>
            <div><strong>{comparison.baseline.name}</strong><span>{comparison.baseline.source_arm.toUpperCase()} → 当前 {comparison.current_arm.toUpperCase()}</span></div>
            <dl>
              <div><dt>基线解决率</dt><dd>{percent(comparison.resolution.baseline_rate_percent)}</dd></div>
              <div><dt>当前解决率</dt><dd>{percent(comparison.resolution.current_rate_percent)}</dd></div>
              <div><dt>差值</dt><dd>{signed(comparison.resolution.delta_points, " pp")}</dd></div>
              <div><dt>可比较任务</dt><dd>{number(comparison.coverage.comparable_tasks)}</dd></div>
            </dl>
          </article>
        ))}
        {comparisons !== null && comparisons.comparisons.length === 0 && (
          <p className="field-hint">尚未选择基线；当前报告只展示本次真实结果。</p>
        )}
      </section>

      {batch.status === "completed" && (
        <section className="report-section report-section--batch">
          <div className="section-heading"><div><h2>保存为基线</h2><p>每次保存一个不可变 Arm 快照。</p></div></div>
          <div className="baseline-save-row">
            <input
              aria-label="基线名称"
              placeholder="基线名称（可选）"
              value={baselineName}
              onChange={(event) => setBaselineName(event.target.value)}
            />
            {report.off !== null && <button disabled={baselinePending} onClick={() => saveBaseline("off")}>保存 OFF 基线</button>}
            {report.on !== null && <button disabled={baselinePending} onClick={() => saveBaseline("on")}>保存 ON 基线</button>}
          </div>
          {baselineMessage && <p className="field-hint" aria-live="polite">{baselineMessage}</p>}
        </section>
      )}
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
  if (
    metric.off === null
    || metric.on === null
    || metric.delta === null
    || metric.off_measured_tasks === null
    || metric.on_measured_tasks === null
  ) return null;
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

function SingleTokenCard({
  label,
  metric,
  arm,
  total,
}: {
  label: string;
  metric: TokenMetricAggregate;
  arm: Arm;
  total: number;
}) {
  const value = arm === "off" ? metric.off : metric.on;
  const measured = arm === "off" ? metric.off_measured_tasks : metric.on_measured_tasks;
  return (
    <article className="token-card">
      <h3>{label}</h3>
      <dl><div><dt>{arm.toUpperCase()}</dt><dd>{value === null ? "—" : number(value)}</dd></div></dl>
      <p>{number(measured ?? 0)} / {number(total)} 个任务有记录</p>
    </article>
  );
}
