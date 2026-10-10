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
import type {
  BatchRecord,
  BatchTaskDetail,
  TaskDetailArm,
  TokensFlowFinalizationSummary,
} from "../types";
import { AttemptHistory } from "./AttemptHistory";
import { ContextTimeline } from "./ContextTimeline";

interface TaskRunDetailProps {
  api: EvaluationApi;
  batchId: string;
  taskId: string;
  search: string;
  navigate(path: string): void;
}

function number(value: number): string {
  return new Intl.NumberFormat("zh-CN").format(value);
}

function readableProblemStatement(value: string): string {
  return value
    .split("\n")
    .map((line) => {
      if (!line.startsWith("\"") || !line.endsWith("\"")) return line;
      try {
        const decoded: unknown = JSON.parse(line);
        return typeof decoded === "string" ? decoded : line;
      } catch {
        return line;
      }
    })
    .join("\n");
}

export function TaskRunDetail({ api, batchId, taskId, search, navigate }: TaskRunDetailProps) {
  const [batch, setBatch] = useState<BatchRecord | null>(null);
  const [detail, setDetail] = useState<BatchTaskDetail | null>(null);
  const [expanded, setExpanded] = useState(false);
  const [selectedAttemptId, setSelectedAttemptId] = useState<string | null>(null);
  const [refreshVersion, setRefreshVersion] = useState(0);
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
      api.getBatchTask(batchId, taskId, nextController.signal, selectedAttemptId ?? undefined),
    ])
      .then(([nextBatch, nextDetail]) => {
        if (nextController.signal.aborted || currentGeneration !== generation.current) return;
        setBatch(nextBatch);
        setDetail(nextDetail);
      })
      .catch(() => {
        if (!nextController.signal.aborted && currentGeneration === generation.current) setError(true);
      });
  }, [api, batchId, refreshVersion, selectedAttemptId, taskId]);

  useEffect(() => {
    setExpanded(false);
    load();
    return () => {
      controller.current?.abort();
      generation.current += 1;
    };
  }, [load]);

  useEffect(() => {
    setSelectedAttemptId(null);
  }, [taskId]);

  const listPath = `/report/${encodeURIComponent(batchId)}/tasks${search}`;
  const onBack = (event: MouseEvent<HTMLAnchorElement>) => {
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    navigate(listPath);
  };

  if (error) {
    return (
      <section className="panel empty-state">
        <p>单任务数据暂时无法加载。</p>
        <button type="button" className="secondary-button" onClick={load}>重试</button>
      </section>
    );
  }
  if (batch === null || detail === null) {
    return <section className="panel state-message">正在读取单任务数据…</section>;
  }

  const task = detail.task;
  const off = task.off;
  const on = task.on;
  const offTokens = task.tokens.off;
  const onTokens = task.tokens.on;
  const delta = task.tokens.delta;
  const problemStatement = readableProblemStatement(detail.problem_statement);
  const problemPreview = problemStatement.length > 360
    ? `${problemStatement.slice(0, 360)}…`
    : problemStatement;
  const didNotRun = task.status === "queued" || task.status === "cancelled";

  return (
    <div className="task-run-detail">
      <div className="breadcrumb">
        <a href={listPath} onClick={onBack} aria-label="返回任务详细报告">任务详细报告</a> / 单任务详情
      </div>
      <header className="report-page-head">
        <div>
          <h1>单任务详情</h1>
          <p className="task-instance">{task.instance_id}</p>
          <p>{task.repository}</p>
        </div>
        <div className="task-result-summary" aria-label="任务对比汇总">
          {off !== null || on !== null ? (
            <>
              {off !== null && <span className={off.resolved ? "resolution--pass" : "resolution--fail"}>
                OFF {off.resolved ? "通过" : "未通过"}
              </span>}
              {on !== null && <span className={on.resolved ? "resolution--pass" : "resolution--fail"}>
                ON {on.resolved ? "通过" : "未通过"}
              </span>}
            </>
          ) : (
            <span>{task.status === "cancelled" ? "已取消" : task.status === "queued" ? "排队中" : "评测执行失败"}</span>
          )}
          {offTokens !== null && <span>OFF {number(offTokens)}</span>}
          {onTokens !== null && <span>ON {number(onTokens)}</span>}
          {delta !== null && <span>差值 {delta > 0 ? "+" : ""}{number(delta)}</span>}
        </div>
      </header>

      <section className="task-config" aria-label="固定评测配置">
        <span>批次 {batch.batch_id}</span>
        <span>PowerContext {(batch.resolved_powercontext_sha ?? batch.request.powercontext_ref).slice(0, 12)}</span>
        <span>{batch.request.model}</span>
        <span>{batch.request.reasoning_effort}</span>
        <span>{batch.request.task_set}</span>
      </section>

      <AttemptHistory
        api={api}
        batchId={batchId}
        task={task}
        onSelect={setSelectedAttemptId}
        onRetried={() => {
          setSelectedAttemptId(null);
          setRefreshVersion((value) => value + 1);
        }}
      />

      {(detail.tokensflow_finalization.off !== null || detail.tokensflow_finalization.on !== null) && (
        <section className="report-section" aria-label="TokensFlow 收尾状态">
          <div className="section-heading">
            <div>
              <h2>TokensFlow 收尾</h2>
              <p>独立于官方评测结果；超时或清理告警不会改变任务成功状态。</p>
            </div>
          </div>
          <div className="official-grid">
            {detail.tokensflow_finalization.off !== null && (
              <p>OFF {tokensflowFinalizationLabel(detail.tokensflow_finalization.off)}</p>
            )}
            {detail.tokensflow_finalization.on !== null && (
              <p>ON {tokensflowFinalizationLabel(detail.tokensflow_finalization.on)}</p>
            )}
          </div>
        </section>
      )}

      <section className="report-section task-problem">
        <div className="section-heading">
          <div>
            <h2>原始任务</h2>
            <p>来自固定 SWE-bench Pro 数据集。</p>
          </div>
          {problemStatement.length > 360 && (
            <button type="button" className="secondary-button" onClick={() => setExpanded((value) => !value)}>
              {expanded ? "收起完整任务描述" : "展开完整任务描述"}
            </button>
          )}
        </div>
        <pre aria-label="任务描述">{expanded ? problemStatement : problemPreview}</pre>
      </section>

      <section className="report-section">
        <div className="section-heading">
          <div>
            <h2>官方评测结果</h2>
            <p>由固定 SWE-bench Pro evaluator 应用补丁并运行目标测试。</p>
          </div>
        </div>
        {didNotRun ? (
          <div className="empty-state">
            {task.status === "cancelled" ? "任务未执行，因此没有官方评测结果。" : "任务尚未执行。"}
          </div>
        ) : detail.off === null && detail.on === null ? (
          <div className="failure-box">
            <strong>评测执行失败</strong>
            {task.failure_summary && <p>{task.failure_summary}</p>}
          </div>
        ) : (
          <div className="official-grid">
            {detail.off !== null && <OfficialArm label="OFF" arm={detail.off} />}
            {detail.on !== null && <OfficialArm label="ON" arm={detail.on} />}
          </div>
        )}
        <details className="required-tests">
          <summary>查看官方测试输入</summary>
          <h3>FAIL_TO_PASS</h3>
          <pre>{detail.required_tests.fail_to_pass.join("\n")}</pre>
          <h3>PASS_TO_PASS</h3>
          <pre>{detail.required_tests.pass_to_pass.join("\n")}</pre>
          <h3>选定测试文件</h3>
          <pre>{detail.required_tests.selected_test_files_to_run}</pre>
          <h3>测试补丁</h3>
          <pre>{detail.required_tests.test_patch}</pre>
        </details>
      </section>

      {didNotRun ? (
        <section className="report-section empty-state">
          <h2>完整上下文时间线</h2>
          <p>任务未执行，因此没有上下文时间线。</p>
        </section>
      ) : off === null && on === null ? (
        <section className="report-section empty-state">
          <h2>完整上下文时间线</h2>
          <p>
            {task.status === "failed" || task.status === "interrupted"
              ? "本次尝试没有形成可用的完整上下文时间线。"
              : "本次尝试尚未形成可用的完整上下文时间线。"}
          </p>
        </section>
      ) : (
        <ContextTimeline
          api={api}
          batchId={batchId}
          taskId={taskId}
          {...(task.attempt_id === null ? {} : { attemptId: task.attempt_id })}
          availableArms={[
            ...(off === null ? [] : ["off" as const]),
            ...(on === null ? [] : ["on" as const]),
          ]}
        />
      )}
    </div>
  );
}

function tokensflowFinalizationLabel(value: TokensFlowFinalizationSummary): string {
  return {
    pending: "TokensFlow 收尾中",
    passed: "TokensFlow 收尾完成",
    timed_out: "TokensFlow 收尾超时",
    capacity_evicted: "TokensFlow 容量回收",
    cleanup_failed: "TokensFlow 清理失败",
  }[value.state];
}

function OfficialArm({ label, arm }: { label: "OFF" | "ON"; arm: TaskDetailArm }) {
  return (
    <article className="official-arm" aria-label={`${label} 官方评测`}>
      <header>
        <h3>{label}</h3>
        <strong className={arm.resolved ? "resolution--pass" : "resolution--fail"}>
          {arm.resolved ? "已解决" : "未解决"}
        </strong>
      </header>
      <div className="official-facts">
        {arm.patch_applied !== null && <p>{arm.patch_applied ? "补丁应用成功" : "补丁应用失败"}</p>}
        <p>FAIL_TO_PASS {arm.fail_to_pass.passed} / {arm.fail_to_pass.total}</p>
        <p>PASS_TO_PASS {arm.pass_to_pass.passed} / {arm.pass_to_pass.total}</p>
      </div>
      {arm.fail_to_pass.failed.length > 0 && <p>失败测试：{arm.fail_to_pass.failed.join("、")}</p>}
      {arm.pass_to_pass.failed.length > 0 && <p>回归失败：{arm.pass_to_pass.failed.join("、")}</p>}
      {arm.log_excerpt !== null && <pre className="evaluator-log">{arm.log_excerpt}</pre>}
    </article>
  );
}
