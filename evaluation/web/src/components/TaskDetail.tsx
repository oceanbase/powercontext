import { useCallback, useEffect, useRef, useState } from "react";

import type { EvaluationApi } from "../api";
import type { FailureCategory, TaskRecord } from "../types";
import { phaseLabels, statusLabels } from "./TaskList";

interface TaskDetailProps {
  api: EvaluationApi;
  taskId: string;
  onTaskChanged?(task: TaskRecord): void;
}

const failureLabels: Record<FailureCategory, string> = {
  invalid_request: "请求无效",
  queue_unavailable: "队列不可用",
  source_resolution_failure: "源码解析失败",
  environment_preparation_failure: "环境准备失败",
  gold_validation_failure: "Gold 验证失败",
  codex_execution_failure: "Codex 执行失败",
  codex_capacity_failure: "上游模型容量不足（可重试）",
  treatment_validation_failure: "处理组验证失败",
  official_evaluator_failure: "官方评测失败",
  report_generation_failure: "报告生成失败",
  worker_interruption: "Worker 中断",
  internal: "内部错误",
};

function shownTime(value: string | null): string {
  return value === null ? "—" : new Date(value).toLocaleString("zh-CN", { hour12: false });
}

export function TaskDetail({ api, taskId, onTaskChanged }: TaskDetailProps) {
  const [task, setTask] = useState<TaskRecord | null>(null);
  const [error, setError] = useState(false);
  const [reconnecting, setReconnecting] = useState(false);
  const requestGeneration = useRef(0);
  const requestController = useRef<AbortController | null>(null);

  const load = useCallback(async () => {
    requestController.current?.abort();
    const controller = new AbortController();
    requestController.current = controller;
    const generation = ++requestGeneration.current;
    try {
      const nextTask = await api.getTask(taskId, controller.signal);
      if (controller.signal.aborted || generation !== requestGeneration.current) return;
      setTask(nextTask);
      onTaskChanged?.(nextTask);
      setError(false);
    } catch {
      if (!controller.signal.aborted && generation === requestGeneration.current) setError(true);
    }
  }, [api, onTaskChanged, taskId]);

  useEffect(() => {
    setTask(null);
    setReconnecting(false);
    void load();
    const subscription = api.subscribeTaskEvents(
      taskId,
      () => {
        setReconnecting(false);
        void load();
      },
      () => setReconnecting(true),
    );
    return () => {
      subscription.close();
      requestController.current?.abort();
      requestGeneration.current += 1;
    };
  }, [api, taskId, load]);

  useEffect(() => {
    if (!reconnecting || task === null || ["succeeded", "failed", "interrupted", "cancelled"].includes(task.status)) {
      return;
    }
    const timer = window.setInterval(() => void load(), 5000);
    return () => window.clearInterval(timer);
  }, [load, reconnecting, task]);

  if (error) {
    return (
      <section className="panel empty-state">
        <p>任务详情暂时无法加载。</p>
        <button className="secondary-button" type="button" onClick={() => void load()}>
          重试
        </button>
      </section>
    );
  }
  if (task === null) return <section className="panel state-message">正在加载任务详情…</section>;

  return (
    <section className="panel task-detail" aria-live="polite">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">任务详情</p>
          <h2>{task.task_id}</h2>
        </div>
        <span className={`status status--${task.status}`}>{statusLabels[task.status]}</span>
      </div>
      {reconnecting && <p className="connection-note">实时连接中断，正在定时刷新。</p>}
      <div className="detail-block">
        <h3>当前阶段</h3>
        <p className="phase-value">{task.phase ? phaseLabels[task.phase] : statusLabels[task.status]}</p>
        {task.queue_position !== null && <p>队列位置：{task.queue_position}</p>}
      </div>
      <div className="detail-block">
        <h3>不可变提交参数</h3>
        <dl className="detail-grid">
          <div><dt>PowerContext 版本</dt><dd>{task.request.powercontext_ref}</dd></div>
          <div><dt>基准测试</dt><dd>{task.request.benchmark}</dd></div>
          <div><dt>测试实例</dt><dd>{task.request.instance_id}</dd></div>
          <div><dt>模型</dt><dd>{task.request.model}</dd></div>
          <div><dt>推理强度</dt><dd>{task.request.reasoning_effort}</dd></div>
          <div><dt>测试方式</dt><dd>OFF / ON 对照</dd></div>
        </dl>
      </div>
      <div className="detail-block">
        <h3>时间线</h3>
        <ol className="timeline">
          <li><span>已提交</span><time dateTime={task.created_at}>{shownTime(task.created_at)}</time></li>
          <li><span>开始执行</span><time dateTime={task.started_at ?? undefined}>{shownTime(task.started_at)}</time></li>
          <li><span>结束</span><time dateTime={task.finished_at ?? undefined}>{shownTime(task.finished_at)}</time></li>
        </ol>
      </div>
      {(task.status === "failed" || task.status === "interrupted") && (
        <div className="failure-box">
          <strong>{failureLabels[task.failure_category]}</strong>
          <p>{task.failure_summary}</p>
        </div>
      )}
      {task.status === "succeeded" && (
        <a className="primary-link" href={`/reports/${encodeURIComponent(task.task_id)}`}>
          查看验收报告
        </a>
      )}
    </section>
  );
}
