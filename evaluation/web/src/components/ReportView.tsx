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

import { useCallback, useEffect, useRef, useState } from "react";

import type { EvaluationApi } from "../api";
import type { ArmResponse, MetricComparison, ReportResponse, TreatmentEvidence } from "../types";

interface ReportViewProps {
  api: EvaluationApi;
  taskId: string;
}

const integerFormat = new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 0 });
const decimalFormat = new Intl.NumberFormat("zh-CN", { minimumFractionDigits: 0, maximumFractionDigits: 2 });
const percentFormat = new Intl.NumberFormat("zh-CN", {
  minimumFractionDigits: 1,
  maximumFractionDigits: 1,
});

function number(value: number | null, kind: "integer" | "seconds" = "integer"): string {
  if (value === null) return "N/A";
  return kind === "seconds" ? `${decimalFormat.format(value)} 秒` : integerFormat.format(value);
}

function signed(value: number, kind: "integer" | "seconds" = "integer"): string {
  const magnitude = kind === "seconds" ? decimalFormat.format(Math.abs(value)) : integerFormat.format(Math.abs(value));
  const sign = value < 0 ? "−" : value > 0 ? "+" : "";
  return `${sign}${magnitude}${kind === "seconds" ? " 秒" : ""}`;
}

function percent(value: number | null): string {
  if (value === null) return "N/A";
  const sign = value < 0 ? "−" : value > 0 ? "+" : "";
  return `${sign}${percentFormat.format(Math.abs(value))}%`;
}

const metricRows: Array<{
  key: keyof ReportResponse["comparison"];
  label: string;
  kind: "integer" | "seconds";
}> = [
  { key: "patch_bytes", label: "补丁大小（字节）", kind: "integer" },
  { key: "input_tokens", label: "输入 Token", kind: "integer" },
  { key: "output_tokens", label: "输出 Token", kind: "integer" },
  { key: "elapsed_seconds", label: "执行时间", kind: "seconds" },
];

export function ReportView({ api, taskId }: ReportViewProps) {
  const [report, setReport] = useState<ReportResponse | null>(null);
  const [error, setError] = useState(false);
  const generation = useRef(0);
  const controller = useRef<AbortController | null>(null);

  const load = useCallback(() => {
    controller.current?.abort();
    const nextController = new AbortController();
    controller.current = nextController;
    const requestGeneration = ++generation.current;
    setReport(null);
    setError(false);
    api
      .getReport(taskId, nextController.signal)
      .then((nextReport) => {
        if (!nextController.signal.aborted && requestGeneration === generation.current) setReport(nextReport);
      })
      .catch(() => {
        if (!nextController.signal.aborted && requestGeneration === generation.current) setError(true);
      });
  }, [api, taskId]);

  useEffect(() => {
    load();
    return () => {
      controller.current?.abort();
      generation.current += 1;
    };
  }, [load]);

  if (error) {
    return (
      <section className="panel empty-state" aria-live="polite">
        <p>验收报告暂时无法加载。</p>
        <button className="secondary-button" type="button" onClick={load}>
          重试
        </button>
      </section>
    );
  }
  if (report === null) return <section className="panel state-message">正在加载验收报告…</section>;

  const comparable = Object.values(report.comparison).some((metric) => metric !== null);
  return (
    <article className={`report-view${report.acceptance_valid ? "" : " report-view--invalid"}`}>
      <header className="report-identity">
        <div>
          <p className="eyebrow">任务</p>
          <h2 className="report-task-id">{report.task_id}</h2>
        </div>
        <span className={`acceptance-badge acceptance-badge--${report.acceptance_valid ? "valid" : "invalid"}`}>
          {report.acceptance_valid ? "验收有效" : "验收无效"}
        </span>
      </header>

      {!report.acceptance_valid && <p className="invalid-note">报告保留实际结果，但不构成有效验收结论。</p>}

      <section className="report-section" aria-labelledby="comparison-heading">
        <h3 id="comparison-heading">OFF / ON 指标对照</h3>
        {!comparable && <p className="comparison-unavailable">当前报告不具备有效的 OFF / ON 对照数据。</p>}
        <div className="table-scroll">
          <table>
            <thead><tr><th scope="col">指标</th><th scope="col">OFF</th><th scope="col">ON</th><th scope="col">变化量（ON − OFF）</th><th scope="col">变化率</th></tr></thead>
            <tbody>
              {metricRows.map(({ key, label, kind }) => {
                const metric = report.comparison[key];
                return <MetricRow key={key} label={label} metric={metric} kind={kind} />;
              })}
            </tbody>
          </table>
        </div>
      </section>

      <section className="report-section" aria-labelledby="arms-heading">
        <h3 id="arms-heading">评测臂结果</h3>
        <div className="arm-grid">
          <ArmCard arm={report.off} />
          <ArmCard arm={report.on} />
        </div>
      </section>

      <section className="report-section" aria-labelledby="evidence-heading">
        <h3 id="evidence-heading">处理证据</h3>
        <div className="arm-grid">
          <EvidenceCard label="OFF" evidence={report.evidence.off} />
          <EvidenceCard label="ON" evidence={report.evidence.on} />
        </div>
      </section>

      <section className="report-section" aria-labelledby="metadata-heading" aria-label="复现信息">
        <h3 id="metadata-heading">复现信息</h3>
        <Metadata title="版本修订" values={report.revisions} />
        <Metadata title="运行配置" values={report.configuration} />
        <dl className="metadata-list">
          <div><dt>生成时间</dt><dd><time dateTime={report.generated_at}>{new Date(report.generated_at).toLocaleString("zh-CN", { hour12: false })}</time></dd></div>
        </dl>
      </section>

      {report.gold_validation != null && (
        <section className="report-section" aria-labelledby="gold-validation-heading">
          <h3 id="gold-validation-heading">Gold 校验审计</h3>
          <div className="metadata-group">
            <dl className="metadata-list">
              <div><dt>校验模式</dt><dd>{report.gold_validation.mode}</dd></div>
              <div><dt>原始 Gold 状态</dt><dd>{report.gold_validation.dataset_patch_status}</dd></div>
              <div><dt>参考补丁校验</dt><dd>{report.gold_validation.reference_validation_status}</dd></div>
              <div><dt>本次 Gold 校验</dt><dd>{report.gold_validation.attempt_gold_validation_status}</dd></div>
              <div><dt>原始补丁 SHA</dt><dd>{report.gold_validation.dataset_patch_sha256}</dd></div>
              <div><dt>校验补丁 SHA</dt><dd>{report.gold_validation.validation_patch_sha256}</dd></div>
              {report.gold_validation.source_dataset !== null && <div><dt>来源数据集</dt><dd>{report.gold_validation.source_dataset}</dd></div>}
              {report.gold_validation.source_revision !== null && <div><dt>来源 revision</dt><dd>{report.gold_validation.source_revision}</dd></div>}
              {report.gold_validation.source_file_oid !== null && <div><dt>来源文件 OID</dt><dd>{report.gold_validation.source_file_oid}</dd></div>}
            </dl>
          </div>
        </section>
      )}

      <a
        className="primary-link raw-report-link"
        href={`/api/tasks/${encodeURIComponent(taskId)}/report.md`}
        target="_blank"
        rel="noopener noreferrer"
      >
        查看原始 Markdown
      </a>
    </article>
  );
}

function MetricRow({
  label,
  metric,
  kind,
}: {
  label: string;
  metric: MetricComparison | null;
  kind: "integer" | "seconds";
}) {
  return (
    <tr>
      <th scope="row">{label}</th>
      <td>{metric === null ? "N/A" : number(metric.off, kind)}</td>
      <td>{metric === null ? "N/A" : number(metric.on, kind)}</td>
      <td>{metric === null ? "N/A" : signed(metric.delta, kind)}</td>
      <td>{metric === null ? "N/A" : percent(metric.percent)}</td>
    </tr>
  );
}

function ArmCard({ arm }: { arm: ArmResponse }) {
  const label = arm.arm.toUpperCase();
  const patchSize = arm.patch_bytes === null ? "N/A" : `${number(arm.patch_bytes)} 字节`;
  return (
    <section className="arm-panel" aria-label={`${label} 评测臂`}>
      <h4>{label} · {arm.resolution.toUpperCase()}</h4>
      <dl className="arm-metrics">
        <div><dt>生命周期</dt><dd>{arm.state.replaceAll("_", " ").toUpperCase()}</dd></div>
        <div><dt>官方结果</dt><dd>{arm.passed === null ? "N/A" : arm.passed ? "PASS" : "FAIL"}</dd></div>
        <div><dt>处理有效性</dt><dd>{arm.treatment_valid ? "VALID" : "INVALID"}</dd></div>
        <div><dt>补丁大小</dt><dd>{patchSize}</dd></div>
        <div><dt>输入 Token</dt><dd>{number(arm.input_tokens)}</dd></div>
        <div><dt>输出 Token</dt><dd>{number(arm.output_tokens)}</dd></div>
        <div><dt>执行时间</dt><dd>{number(arm.elapsed_seconds, "seconds")}</dd></div>
      </dl>
    </section>
  );
}

function EvidenceCard({ label, evidence }: { label: string; evidence: TreatmentEvidence }) {
  return (
    <section className="arm-panel evidence-panel" aria-label={`${label} 处理证据`}>
      <h4>{label}</h4>
      <dl>
        <div><dt>MCP 请求</dt><dd>{integerFormat.format(evidence.mcp_requests)}</dd></div>
        <div><dt>Prompt 来源</dt><dd>{integerFormat.format(evidence.prompt_sources)}</dd></div>
        <div><dt>插件</dt><dd>{evidence.plugin_id} · {evidence.plugin_version}</dd></div>
        <div><dt>安装状态</dt><dd>{evidence.plugin_installed ? "已安装" : "未安装"}</dd></div>
        <div><dt>服务状态</dt><dd>{evidence.server_ready ? "已就绪" : "未就绪"}</dd></div>
        <div><dt>Scope</dt><dd>{evidence.scope_id}</dd></div>
        <div><dt>插件版本 SHA</dt><dd>{evidence.plugin_checkout_sha}</dd></div>
      </dl>
    </section>
  );
}

function Metadata({ title, values }: { title: string; values: Record<string, string> }) {
  return (
    <div className="metadata-group">
      <h4>{title}</h4>
      <dl className="metadata-list">
        {Object.entries(values)
          .sort(([left], [right]) => left.localeCompare(right, "en"))
          .map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{value}</dd></div>)}
      </dl>
    </div>
  );
}
