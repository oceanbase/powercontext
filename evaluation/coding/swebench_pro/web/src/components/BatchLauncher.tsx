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
import type { BaselineRecord, BatchCreate, BatchRecord, BatchTaskSet, TreatmentMode } from "../types";

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

export function BatchLauncher({ api, onCreated }: BatchLauncherProps) {
  const [revision, setRevision] = useState("latest");
  const [taskSet, setTaskSet] = useState<BatchTaskSet>("swebench-pro-public-v2");
  const [model, setModel] = useState("gpt-5.6-sol");
  const [models, setModels] = useState<string[]>(["gpt-5.6-sol"]);
  const [treatmentMode, setTreatmentMode] = useState<TreatmentMode>("off_on");
  const [baselines, setBaselines] = useState<BaselineRecord[]>([]);
  const [selectedBaselineIds, setSelectedBaselineIds] = useState<string[]>([]);
  const [startPaused, setStartPaused] = useState(false);
  const [threshold, setThreshold] = useState(80);
  const [envRows, setEnvRows] = useState<{ key: string; value: string }[]>([]);
  const [pending, setPending] = useState(false);
  const [message, setMessage] = useState("");
  const controller = useRef<AbortController | null>(null);
  const generation = useRef(0);
  const confirmationKey = useRef<{
    revision: string;
    taskSet: BatchTaskSet;
    model: string;
    treatmentMode: TreatmentMode;
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
    Promise.all([
      api.getCapabilities(capabilitiesController.signal),
      api.listBaselines(capabilitiesController.signal),
    ]).then(([capabilities, availableBaselines]) => {
      if (capabilitiesController.signal.aborted) return;
      setModels(capabilities.models);
      setModel((current) => capabilities.models.includes(current) ? current : (capabilities.models[0] ?? ""));
      setBaselines(availableBaselines);
    }).catch(() => undefined);
    return () => capabilitiesController.abort();
  }, [api]);

  const invalidatePreview = () => {
    controller.current?.abort();
    generation.current += 1;
    setPending(false);
    setMessage("");
    confirmationKey.current = null;
  };

  const submit = async (event: FormEvent) => {
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
    setPending(true);
    try {
      const preview = await api.previewBatch(
        { powercontext_ref: revision, task_set: taskSet, model, treatment_mode: treatmentMode, usage_pause_percent: threshold },
        nextController.signal,
      );
      if (nextController.signal.aborted || generation.current !== currentGeneration) return;
      if (!preview.can_start) {
        setMessage("当前用量已达到暂停阈值，暂时不能创建评测。");
        return;
      }
      const intent = {
        revision: preview.powercontext_ref,
        taskSet: preview.task_set,
        model: preview.model,
        treatmentMode: preview.treatment_mode,
        threshold: preview.usage_pause_percent,
        initialControlIntent: startPaused ? "pause" as const : "run" as const,
      };
      if (
        confirmationKey.current?.revision !== intent.revision
        || confirmationKey.current.taskSet !== intent.taskSet
        || confirmationKey.current.model !== intent.model
        || confirmationKey.current.treatmentMode !== intent.treatmentMode
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
      const batch = await api.createBatch(request, nextController.signal);
      if (selectedBaselineIds.length > 0) {
        const currentArm = treatmentMode === "off_only" ? "off" : "on";
        try {
          await api.updateBaselineSelections(
            batch.batch_id,
            selectedBaselineIds.map((baselineId) => ({ baseline_id: baselineId, current_arm: currentArm })),
            nextController.signal,
          );
        } catch {
          // The batch already exists. Continue to its report so an incompatible
          // presentation-only baseline can be replaced without submitting work again.
        }
      }
      if (nextController.signal.aborted || generation.current !== currentGeneration) return;
      confirmationKey.current = null;
      onCreated(batch);
    } catch {
      if (!nextController.signal.aborted && generation.current === currentGeneration) {
        setMessage("提交失败；幂等键已保留，可以安全重试。");
      }
    } finally {
      if (!nextController.signal.aborted && generation.current === currentGeneration) setPending(false);
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
        <span>
          {taskSet === "swebench-pro-public-v2" ? "731" : "24"} 个任务 · {treatmentMode === "off_on"
            ? "OFF + ON"
            : treatmentMode === "on_only" ? "仅 ON" : "仅 OFF"}
        </span>
        <span>{model} · medium</span>
        <span>Worker 按配置并行运行独立任务</span>
      </div>

      <form onSubmit={submit} className="launcher-form">
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
        <fieldset className="env-editor">
          <legend>运行方式</legend>
          <label className="checkbox-field">
            <input
              type="radio"
              name="treatment-mode"
              checked={treatmentMode === "off_on"}
              onChange={() => { invalidatePreview(); setTreatmentMode("off_on"); }}
            />
            OFF + ON
          </label>
          <label className="checkbox-field">
            <input
              type="radio"
              name="treatment-mode"
              checked={treatmentMode === "on_only"}
              onChange={() => { invalidatePreview(); setTreatmentMode("on_only"); }}
            />
            仅 ON
          </label>
          <label className="checkbox-field">
            <input
              type="radio"
              name="treatment-mode"
              checked={treatmentMode === "off_only"}
              onChange={() => { invalidatePreview(); setTreatmentMode("off_only"); }}
            />
            仅 OFF
          </label>
        </fieldset>
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
        {treatmentMode !== "off_only" && <fieldset className="env-editor">
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
        </fieldset>}
        <fieldset className="env-editor">
          <legend>历史基线（可选，可多选）</legend>
          <span className="field-hint">这里只设置初始对比，报告完成后仍可随时增删，不会重新运行评测。</span>
          {baselines.filter((baseline) => baseline.task_set === taskSet && baseline.model === model).length === 0 ? (
            <span className="field-hint">暂无与当前任务集和模型匹配的基线。</span>
          ) : baselines.filter((baseline) => baseline.task_set === taskSet && baseline.model === model).map((baseline) => (
            <label className="checkbox-field" key={baseline.baseline_id}>
              <input
                type="checkbox"
                checked={selectedBaselineIds.includes(baseline.baseline_id)}
                onChange={(event) => setSelectedBaselineIds((current) => event.target.checked
                  ? [...current, baseline.baseline_id]
                  : current.filter((baselineId) => baselineId !== baseline.baseline_id))}
              />
              {baseline.name} · {baseline.source_arm.toUpperCase()} · {baseline.resolved_tasks}/{baseline.total_tasks}
            </label>
          ))}
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
          <span className="field-hint">达到阈值后，在当前任务结束时暂停</span>
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
        <button className="primary-button" type="submit" disabled={pending}>
          {pending ? "正在提交…" : "开始评测"}
        </button>
      </form>

      <div className="form-feedback" aria-live="polite">
        {message && <p className="error-message">{message}</p>}
      </div>
    </section>
  );
}
