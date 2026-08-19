import { useCallback, useEffect, useRef, useState } from "react";

import type { EvaluationApi } from "../api";
import type { BatchTaskItem, TaskAttempt } from "../types";

interface AttemptHistoryProps {
  api: EvaluationApi;
  batchId: string;
  task: BatchTaskItem;
  onSelect(attemptId: string): void;
  onRetried(): void;
}

function idempotencyKey(): string {
  if (typeof crypto.randomUUID === "function") return `retry-${crypto.randomUUID()}`;
  return `retry-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
}

const statusLabels = {
  queued: "排队中",
  running: "运行中",
  succeeded: "已完成",
  failed: "执行失败",
  interrupted: "已中断",
  cancelled: "已取消",
} as const;

export function AttemptHistory({ api, batchId, task, onSelect, onRetried }: AttemptHistoryProps) {
  const [attempts, setAttempts] = useState<TaskAttempt[] | null>(null);
  const [selected, setSelected] = useState(task.attempt_id);
  const [confirming, setConfirming] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState(false);
  const controller = useRef<AbortController | null>(null);
  const retryKey = useRef<string | null>(null);

  const load = useCallback(() => {
    controller.current?.abort();
    const nextController = new AbortController();
    controller.current = nextController;
    setError(false);
    api.listTaskAttempts(batchId, task.task_id, nextController.signal)
      .then((records) => {
        if (!nextController.signal.aborted) setAttempts(records);
      })
      .catch(() => {
        if (!nextController.signal.aborted) setError(true);
      });
  }, [api, batchId, task.task_id]);

  useEffect(() => {
    setSelected(task.attempt_id);
    setConfirming(false);
    load();
    return () => controller.current?.abort();
  }, [load, task.attempt_id]);

  const retry = async () => {
    if (!task.retryable || pending) return;
    retryKey.current ??= idempotencyKey();
    controller.current?.abort();
    const nextController = new AbortController();
    controller.current = nextController;
    setPending(true);
    setError(false);
    try {
      await api.retryTask(batchId, task.task_id, retryKey.current, nextController.signal);
      if (nextController.signal.aborted) return;
      retryKey.current = null;
      setConfirming(false);
      onRetried();
      load();
    } catch {
      if (!nextController.signal.aborted) setError(true);
    } finally {
      if (!nextController.signal.aborted) setPending(false);
    }
  };

  return (
    <section className="report-section attempt-history" aria-label="任务尝试历史">
      <div className="section-heading">
        <div>
          <h2>任务尝试历史</h2>
          <p>每次基础设施重试都会创建新记录，旧结果和证据保持不变。</p>
        </div>
        <span>{task.attempt_count} 次尝试</span>
      </div>

      {attempts === null ? (
        <p>正在读取尝试记录…</p>
      ) : (
        <div className="attempt-list">
          {attempts.map((attempt) => (
            <button
              type="button"
              className={selected === attempt.attempt_id ? "attempt-card attempt-card--selected" : "attempt-card"}
              aria-label={`尝试 ${attempt.attempt_number}`}
              aria-pressed={selected === attempt.attempt_id}
              onClick={() => {
                setSelected(attempt.attempt_id);
                onSelect(attempt.attempt_id);
              }}
              key={attempt.attempt_id}
            >
              <strong>尝试 {attempt.attempt_number}</strong>
              <span>{statusLabels[attempt.status]}</span>
              {attempt.failure_summary !== null && <small>{attempt.failure_summary}</small>}
            </button>
          ))}
        </div>
      )}

      {task.retryable && !confirming && (
        <button type="button" className="secondary-button" onClick={() => setConfirming(true)}>
          重试此任务
        </button>
      )}
      {task.retryable && confirming && (
        <div className="retry-confirmation">
          <p>只重新运行这个基础设施失败的任务，已有尝试和证据不会被覆盖。</p>
          <button type="button" className="primary-button" disabled={pending} onClick={retry}>
            {pending ? "正在提交…" : "确认重试"}
          </button>
          <button type="button" className="secondary-button" disabled={pending} onClick={() => setConfirming(false)}>
            返回
          </button>
        </div>
      )}
      {error && <p className="error-message">尝试记录或重试请求暂时无法处理。</p>}
    </section>
  );
}
