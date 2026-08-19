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

import { useEffect, useRef, useState, type FormEvent } from "react";

import type { EvaluationApi } from "../api";
import type { BatchCreate, BatchPreview, BatchRecord, BatchTaskSet } from "../types";
import { formatUsageWindow } from "../usageFormat";

interface BatchLauncherProps {
  api: EvaluationApi;
  onCreated(batch: BatchRecord): void;
}

const revisionPattern = /^(latest|commit:[0-9a-fA-F]{40})$/;
const modelPattern = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/;

function idempotencyKey(): string {
  if (typeof crypto.randomUUID === "function") return `web-${crypto.randomUUID()}`;
  return `web-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
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

export function BatchLauncher({ api, onCreated }: BatchLauncherProps) {
  const [revision, setRevision] = useState("latest");
  const [taskSet, setTaskSet] = useState<BatchTaskSet>("swebench-pro-public-v2");
  const [model, setModel] = useState("gpt-5.6-sol");
  const [models, setModels] = useState<string[]>(["gpt-5.6-sol"]);
  const [startPaused, setStartPaused] = useState(false);
  const [threshold, setThreshold] = useState(80);
  const [envRows, setEnvRows] = useState<{ key: string; value: string }[]>([]);
  const [preview, setPreview] = useState<BatchPreview | null>(null);
  const [pending, setPending] = useState<"preview" | "submitting" | null>(null);
  const [message, setMessage] = useState("");
  const controller = useRef<AbortController | null>(null);
  const generation = useRef(0);
  const confirmationKey = useRef<{
    revision: string;
    taskSet: BatchTaskSet;
    model: string;
    threshold: number;
    initialControlIntent: "run" | "pause";
    key: string;
  } | null>(null);

  useEffect(
    () => () => {
      controller.current?.abort();
      generation.current += 1;
    },
    [],
  );

  useEffect(() => {
    const capabilitiesController = new AbortController();
    api.getCapabilities(capabilitiesController.signal).then((capabilities) => {
      if (capabilitiesController.signal.aborted) return;
      setModels(capabilities.models);
      setModel((current) => capabilities.models.includes(current) ? current : (capabilities.models[0] ?? ""));
    }).catch(() => undefined);
    return () => capabilitiesController.abort();
  }, [api]);

  const invalidatePreview = () => {
    controller.current?.abort();
    generation.current += 1;
    setPending(null);
    setPreview(null);
    setMessage("");
    confirmationKey.current = null;
  };

  const requestPreview = async (event: FormEvent) => {
    event.preventDefault();
    setMessage("");
    if (!revisionPattern.test(revision)) {
      setMessage("请输入 latest 或 commit: 开头的 40 位提交哈希。");
      return;
    }
    if (!modelPattern.test(model) || !models.includes(model)) {
      setMessage("请选择当前评测服务已启用的 Codex 模型。");
      return;
    }
    if (!Number.isInteger(threshold) || threshold < 1 || threshold > 100) {
      setMessage("暂停阈值必须是 1 到 100 之间的整数。");
      return;
    }
    controller.current?.abort();
    const nextController = new AbortController();
    controller.current = nextController;
    const currentGeneration = ++generation.current;
    setPending("preview");
    try {
      const result = await api.previewBatch(
        { powercontext_ref: revision, task_set: taskSet, model, usage_pause_percent: threshold },
        nextController.signal,
      );
      if (nextController.signal.aborted || generation.current !== currentGeneration) return;
      confirmationKey.current = null;
      setPreview(result);
    } catch {
      if (!nextController.signal.aborted && generation.current === currentGeneration) {
        setMessage("当前无法读取 Codex 用量或评测预览，请稍后重试。");
      }
    } finally {
      if (!nextController.signal.aborted && generation.current === currentGeneration) setPending(null);
    }
  };

  const confirm = async () => {
    if (preview === null || !preview.can_start || pending !== null) return;
    const intent = {
      revision: preview.powercontext_ref,
      taskSet: preview.task_set,
      model: preview.model,
      threshold: preview.usage_pause_percent,
      initialControlIntent: startPaused ? "pause" as const : "run" as const,
    };
    if (
      confirmationKey.current?.revision !== intent.revision
      || confirmationKey.current.taskSet !== intent.taskSet
      || confirmationKey.current.model !== intent.model
      || confirmationKey.current.threshold !== intent.threshold
      || confirmationKey.current.initialControlIntent !== intent.initialControlIntent
    ) {
      confirmationKey.current = { ...intent, key: idempotencyKey() };
    }
    const request: BatchCreate = {
      powercontext_ref: preview.powercontext_ref,
      benchmark: preview.benchmark,
      task_set: preview.task_set,
      model: preview.model,
      reasoning_effort: preview.reasoning_effort,
      treatment_mode: preview.treatment_mode,
      usage_pause_percent: preview.usage_pause_percent,
      idempotency_key: confirmationKey.current.key,
      initial_control_intent: intent.initialControlIntent,
      container_env: envRows.filter((row) => row.key.trim()).reduce(
        (acc, row) => ({ ...acc, [row.key.trim()]: row.value }),
        {} as Record<string, string>,
      ),
    };
    controller.current?.abort();
    const nextController = new AbortController();
    controller.current = nextController;
    const currentGeneration = ++generation.current;
    setMessage("");
    setPending("submitting");
    try {
      const batch = await api.createBatch(request, nextController.signal);
      if (nextController.signal.aborted || generation.current !== currentGeneration) return;
      confirmationKey.current = null;
      onCreated(batch);
    } catch {
      if (!nextController.signal.aborted && generation.current === currentGeneration) {
        setMessage("提交失败，未创建新的确认意图；可以安全重试。");
      }
    } finally {
      if (!nextController.signal.aborted && generation.current === currentGeneration) setPending(null);
    }
  };

  return (
    <section className="panel task-form-panel batch-launcher">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">新批次</p>
          <h2>运行评测批次</h2>
        </div>
        <span className="safe-badge">固定任务集</span>
      </div>

      <div className="batch-contract" aria-label="固定评测范围">
        <strong>{taskSet === "swebench-pro-public-v2" ? "SWE-bench Pro public v2" : "稳定性回归 v1"}</strong>
        <span>{taskSet === "swebench-pro-public-v2" ? "731" : "24"} 个任务，每个任务依次运行 OFF / ON</span>
        <span>{model} · medium</span>
        <span>Worker 按配置并行运行独立任务对</span>
      </div>

      <form onSubmit={requestPreview} className="launcher-form">
        <label>
          任务集
          <select
            aria-label="任务集"
            value={taskSet}
            onChange={(event) => {
              invalidatePreview();
              setTaskSet(event.target.value as BatchTaskSet);
            }}
          >
            <option value="swebench-pro-public-v2">完整 public v2（731 项）</option>
            <option value="swebench-pro-stability-v1">稳定性回归 v1（24 项）</option>
          </select>
          <span className="field-hint">固定清单；稳定性回归包含 20 路首批和 4 项队列补位</span>
        </label>
        <label>
          PowerContext 版本
          <input
            aria-label="PowerContext 版本"
            value={revision}
            onChange={(event) => {
              invalidatePreview();
              setRevision(event.target.value);
            }}
            spellCheck={false}
          />
          <span className="field-hint">latest 或 commit: 加 40 位提交哈希</span>
        </label>
        <label>
          Codex 模型
          <select
            aria-label="Codex 模型"
            value={model}
            onChange={(event) => {
              invalidatePreview();
              setModel(event.target.value);
            }}
          >
            {models.map((availableModel) => (
              <option key={availableModel} value={availableModel}>{availableModel}</option>
            ))}
          </select>
          <span className="field-hint">批次创建后固定，重试也保持不变</span>
        </label>
        <fieldset className="env-editor">
          <legend>容器环境变量（可选，仅 ON 臂）</legend>
          <span className="field-hint">PowerContext Server 在 ON 臂启动时读取这些变量，例如 POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL</span>
          {envRows.map((row, index) => (
            <div key={index} className="env-row">
              <input
                aria-label={`环境变量名 ${index + 1}`}
                placeholder="KEY"
                value={row.key}
                onChange={(event) => {
                  setEnvRows((rows) => rows.map((r, i) => i === index ? { ...r, key: event.target.value } : r));
                }}
                spellCheck={false}
              />
              <input
                aria-label={`环境变量值 ${index + 1}`}
                placeholder="value"
                value={row.value}
                onChange={(event) => {
                  setEnvRows((rows) => rows.map((r, i) => i === index ? { ...r, value: event.target.value } : r));
                }}
                spellCheck={false}
              />
              <button
                type="button"
                className="env-remove"
                onClick={() => setEnvRows((rows) => rows.filter((_, i) => i !== index))}
              >
                ×
              </button>
            </div>
          ))}
          <button
            type="button"
            className="env-add"
            onClick={() => setEnvRows((rows) => [...rows, { key: "", value: "" }])}
          >
            + 添加变量
          </button>
        </fieldset>
        <label className="threshold-field">
          <span className="threshold-field__label">暂停阈值</span>
          <span className="threshold-input">
            <input
              aria-label="暂停阈值"
              type="number"
              min={1}
              max={100}
              value={threshold}
              onChange={(event) => {
                invalidatePreview();
                setThreshold(event.target.valueAsNumber);
              }}
            />
            <span>%</span>
          </span>
          <span className="field-hint">达到阈值后，在当前完整 OFF / ON 任务结束时暂停</span>
        </label>
        <label className="checkbox-field">
          <input
            aria-label="创建后保持暂停"
            type="checkbox"
            checked={startPaused}
            onChange={(event) => {
              invalidatePreview();
              setStartPaused(event.target.checked);
            }}
          />
          创建后保持暂停
          <span className="field-hint">批次和任务原子写入暂停状态，显式恢复前 Worker 不会领取</span>
        </label>
        <button className="primary-button" type="submit" disabled={pending !== null}>
          {pending === "preview" ? "正在读取…" : "预览评测"}
        </button>
      </form>

      {preview !== null && (
        <section className="launch-preview" aria-label="评测确认">
          <div className="launch-preview__head">
            <div>
              <p className="eyebrow">确认信息</p>
              <h3>{number(preview.total_tasks)} 个基准任务</h3>
            </div>
            <strong className="usage-reading">
              {preview.usage === null ? "API Key 计费" : `当前用量 ${preview.usage.used_percent}%`}
            </strong>
          </div>
          <dl className="preview-facts">
            <div><dt>任务集</dt><dd>SWE-bench Pro public v2</dd></div>
            <div><dt>运行方式</dt><dd>每个任务 OFF / ON 配对执行</dd></div>
            <div><dt>Codex 模型</dt><dd>{preview.model} · {preview.reasoning_effort}</dd></div>
            <div><dt>暂停阈值</dt><dd>{preview.usage === null ? "不适用" : `${preview.usage_pause_percent}%`}</dd></div>
            <div><dt>计量窗口</dt><dd>{preview.usage === null ? "API Key" : formatUsageWindow(preview.usage.window_duration_minutes)}</dd></div>
            <div><dt>额度重置</dt><dd>{preview.usage === null ? "由 Provider 管理" : dateTime(preview.usage.resets_at)}</dd></div>
            <div><dt>用量采样</dt><dd>{preview.usage === null ? "不采集订阅用量" : dateTime(preview.usage.observed_at)}</dd></div>
            <div>
              <dt>剩余估算</dt>
              <dd>
                {preview.estimate.quality === "unavailable"
                  ? "暂无可靠估算"
                  : `${preview.estimate.quality === "preliminary" ? "初步估算" : "已测量"} · ${preview.estimate.sample_size} 个样本`}
              </dd>
            </div>
          </dl>
          {!preview.can_start && (
            <p className="usage-blocked">当前用量已达到暂停阈值</p>
          )}
          <button
            className="primary-button"
            type="button"
            disabled={!preview.can_start || pending !== null}
            onClick={confirm}
          >
            {pending === "submitting" ? "正在提交…" : "确认并开始评测"}
          </button>
        </section>
      )}

      <div className="form-feedback" aria-live="polite">
        {message && <p className="error-message">{message}</p>}
      </div>
    </section>
  );
}
