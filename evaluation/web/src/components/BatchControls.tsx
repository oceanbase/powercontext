import { useCallback, useEffect, useState } from "react";

import type { EvaluationApi } from "../api";
import type { AccountUsage, BatchControlEvent, BatchRecord, BatchReport } from "../types";
import { formatUsageWindow } from "../usageFormat";

interface BatchControlsProps {
  api: EvaluationApi;
  batch: BatchRecord;
  report: BatchReport;
  onUpdated(): void;
}

function number(value: number): string {
  return new Intl.NumberFormat("zh-CN").format(value);
}

function dateTime(value: string): string {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date(value));
}

const reasonLabels = {
  user: "用户请求",
  usage_threshold: "达到用量阈值",
  usage_unavailable: "无法读取 Codex 用量",
  quota_limit: "Codex 额度限制",
  infrastructure_failure: "基础设施失败",
  codex_capacity: "上游模型容量不足（自动重试耗尽）",
  resource_pressure: "评测文件系统容量不足",
} as const;

export function BatchControls({ api, batch, report, onUpdated }: BatchControlsProps) {
  const [accountUsage, setAccountUsage] = useState<AccountUsage | null>(
    report.latest_usage === null
      ? null
      : {
          mode: "subscription",
          sufficient: report.latest_usage.used_percent < batch.control.usage_pause_percent,
          usage: report.latest_usage,
        },
  );
  const [events, setEvents] = useState<BatchControlEvent[]>([]);
  const [threshold, setThreshold] = useState(batch.control.usage_pause_percent);
  const [pending, setPending] = useState<string | null>(null);
  const [message, setMessage] = useState("");

  const refreshFacts = useCallback(() => {
    const controller = new AbortController();
    Promise.allSettled([
      api.getAccountUsage(controller.signal),
      api.listBatchControlEvents(batch.batch_id, controller.signal),
    ])
      .then(([usageResult, eventsResult]) => {
        if (controller.signal.aborted) return;
        if (usageResult.status === "fulfilled") setAccountUsage(usageResult.value);
        if (eventsResult.status === "fulfilled") setEvents(eventsResult.value);
        if (usageResult.status === "rejected" && eventsResult.status === "rejected") {
          setMessage("最新用量和控制记录暂时无法读取。");
        } else if (usageResult.status === "rejected") {
          setMessage("最新用量暂时无法读取；控制记录已更新。");
        } else if (eventsResult.status === "rejected") {
          setMessage("控制记录暂时无法读取；用量状态已更新。");
        } else {
          setMessage("");
        }
      });
    return controller;
  }, [api, batch.batch_id]);

  useEffect(() => {
    setThreshold(batch.control.usage_pause_percent);
  }, [batch.control.usage_pause_percent]);

  useEffect(() => {
    const controller = refreshFacts();
    const timer = window.setInterval(refreshFacts, 15_000);
    return () => {
      controller.abort();
      window.clearInterval(timer);
    };
  }, [refreshFacts]);

  const mutate = async (name: string, operation: () => Promise<BatchRecord>) => {
    if (pending !== null) return;
    setPending(name);
    setMessage("");
    try {
      await operation();
      onUpdated();
      refreshFacts();
    } catch {
      setMessage("控制请求未生效，请刷新后重试。");
    } finally {
      setPending(null);
    }
  };

  const estimate = report.estimate;
  const apiKeyMode = accountUsage?.mode === "api_key";
  const usage = accountUsage?.usage ?? report.latest_usage;
  const estimateLabel = estimate.quality === "unavailable"
    ? "暂无可靠估算"
    : `${estimate.quality === "preliminary" ? "初步估算" : "已测量"} · ${number(estimate.sample_size)} 个样本`;

  return (
    <section className="report-section batch-controls" aria-label="批次运行控制">
      <div className="section-heading">
        <div>
          <h2>运行控制</h2>
          <p>所有暂停和取消都在当前完整 OFF / ON 任务结束后生效。</p>
        </div>
        <span className="control-intent">意图：{batch.control.intent}</span>
      </div>

      <div className="control-facts">
        <article>
          <span>进度</span>
          <strong>{number(report.terminal_tasks)} / {number(report.total_tasks)}</strong>
        </article>
        <article>
          <span>Codex 账户用量</span>
          <strong>{apiKeyMode ? "API Key 模式" : usage === null ? "正在读取" : `${usage.used_percent}%`}</strong>
          {apiKeyMode ? (
            <small>不检查订阅用量，运行准入始终视为充足</small>
          ) : usage !== null && (
            <small>
              计量窗口 {formatUsageWindow(usage.window_duration_minutes)}
              {" · "}采样 {dateTime(usage.observed_at)}
              {" · "}重置 {dateTime(usage.resets_at)}
            </small>
          )}
        </article>
        <article>
          <span>剩余用量</span>
          <strong>{apiKeyMode ? "充足" : usage === null ? "—" : `${usage.remaining_percent}%`}</strong>
        </article>
        <article>
          <span>剩余估算</span>
          <strong>{estimateLabel}</strong>
          {estimate.remaining_tokens !== null && (
            <small>{number(estimate.remaining_tokens)} Token · {number(estimate.remaining_duration_seconds ?? 0)} 秒</small>
          )}
        </article>
      </div>

      {batch.control.pause_reason !== null && (
        <p className="pause-reason">暂停原因：{reasonLabels[batch.control.pause_reason]}</p>
      )}

      <div className="control-actions">
        {batch.status === "running" || batch.status === "queued" ? (
          <button
            type="button"
            className="secondary-button"
            disabled={pending !== null}
            onClick={() => mutate("pause", () => api.pauseBatch(batch.batch_id))}
          >
            暂停
          </button>
        ) : batch.status === "pausing" ? (
          <strong>等待当前任务完成</strong>
        ) : batch.status === "paused" ? (
          <button
            type="button"
            className="primary-button"
            disabled={pending !== null}
            onClick={() => mutate("resume", () => api.resumeBatch(batch.batch_id))}
          >
            继续运行
          </button>
        ) : batch.status === "cancelling" ? (
          <strong>等待当前任务完成后取消</strong>
        ) : null}

        {!["completed", "cancelled", "cancelling"].includes(batch.status) && (
          <button
            type="button"
            className="secondary-button danger-button"
            disabled={pending !== null}
            onClick={() => mutate("cancel", () => api.cancelBatch(batch.batch_id))}
          >
            取消批次
          </button>
        )}

        {apiKeyMode ? (
          <span className="control-threshold-note">API Key 模式不使用用量暂停阈值</span>
        ) : (
          <>
            <label className="control-threshold">
              批次暂停阈值
              <input
                aria-label="批次暂停阈值"
                type="number"
                min={1}
                max={100}
                value={Number.isNaN(threshold) ? "" : threshold}
                onChange={(event) => setThreshold(event.target.valueAsNumber)}
              />
            </label>
            <button
              type="button"
              className="secondary-button"
              disabled={
                pending !== null
                || !Number.isInteger(threshold)
                || threshold < 1
                || threshold > 100
                || threshold === batch.control.usage_pause_percent
              }
              onClick={() => mutate(
                "threshold",
                () => api.updateBatchThreshold(batch.batch_id, threshold, batch.control.version),
              )}
            >
              保存阈值
            </button>
          </>
        )}
      </div>

      {message && <p className="error-message">{message}</p>}
      {events.length > 0 && (
        <details className="control-events">
          <summary>查看控制记录（{events.length}）</summary>
          <ol>
            {events.map((event) => (
              <li key={event.sequence}>
                <time>{dateTime(event.occurred_at)}</time>
                <span>{event.event_type}</span>
                <small>{event.actor}</small>
              </li>
            ))}
          </ol>
        </details>
      )}
    </section>
  );
}
