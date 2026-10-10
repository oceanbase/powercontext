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

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { apiStub, batchEstimate, batchRecord, usageSnapshot } from "../test/fixtures";
import { BatchLauncher } from "./BatchLauncher";

function preview(overrides: Record<string, unknown> = {}) {
  return {
    powercontext_ref: "latest",
    benchmark: "swebench-pro" as const,
    task_set: "swebench-pro-public-v2" as const,
    model: "gpt-5.6-sol",
    reasoning_effort: "medium" as const,
    treatment_mode: "off_on" as const,
    total_tasks: 731,
    usage_pause_percent: 80,
    usage: usageSnapshot,
    estimate: batchEstimate,
    can_start: true,
    block_reason: null,
    ...overrides,
  };
}

describe("BatchLauncher", () => {
  it("creates the default paired batch in one step without rendering a run preview", async () => {
    const user = userEvent.setup();
    const previewBatch = vi.fn().mockResolvedValue(preview());
    const createBatch = vi.fn().mockResolvedValue(batchRecord({ batch_id: "batch-created" }));
    const onCreated = vi.fn();
    render(<BatchLauncher api={apiStub({ previewBatch, createBatch })} onCreated={onCreated} />);

    expect(screen.queryByText("确认信息")).not.toBeInTheDocument();
    await user.click(await screen.findByRole("button", { name: "开始评测" }));

    expect(previewBatch).toHaveBeenCalledWith(
      expect.objectContaining({ treatment_mode: "off_on", task_set: "swebench-pro-public-v2" }),
      expect.any(AbortSignal),
    );
    await waitFor(() => expect(createBatch).toHaveBeenCalledWith(
      expect.objectContaining({ treatment_mode: "off_on", initial_control_intent: "run" }),
      expect.any(AbortSignal),
    ));
    expect(onCreated).toHaveBeenCalledWith(expect.objectContaining({ batch_id: "batch-created" }));
  });

  it("submits ON-only mode and preserves its lower-cost execution contract", async () => {
    const user = userEvent.setup();
    const previewBatch = vi.fn().mockResolvedValue(preview({ treatment_mode: "on_only" }));
    const createBatch = vi.fn().mockResolvedValue(batchRecord({
      request: { ...batchRecord().request, treatment_mode: "on_only" },
    }));
    render(<BatchLauncher api={apiStub({ previewBatch, createBatch })} onCreated={() => undefined} />);

    await user.click(await screen.findByRole("radio", { name: "仅 ON" }));
    await user.click(screen.getByRole("button", { name: "开始评测" }));

    await waitFor(() => expect(createBatch).toHaveBeenCalledWith(
      expect.objectContaining({ treatment_mode: "on_only" }),
      expect.any(AbortSignal),
    ));
  });

  it("submits OFF-only mode and removes ON-only environment controls", async () => {
    const user = userEvent.setup();
    const previewBatch = vi.fn().mockResolvedValue(preview({ treatment_mode: "off_only" }));
    const createBatch = vi.fn().mockResolvedValue(batchRecord());
    render(<BatchLauncher api={apiStub({ previewBatch, createBatch })} onCreated={() => undefined} />);

    await user.click(await screen.findByRole("radio", { name: "仅 OFF" }));
    expect(screen.queryByRole("group", { name: "容器环境变量（可选，仅 ON 臂）" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "开始评测" }));

    await waitFor(() => expect(createBatch).toHaveBeenCalledWith(
      expect.objectContaining({ treatment_mode: "off_only" }),
      expect.any(AbortSignal),
    ));
  });

  it("persists multiple initial baseline selections separately from execution", async () => {
    const user = userEvent.setup();
    const baseline = (id: string, name: string) => ({
      baseline_id: id,
      name,
      source_batch_id: "source-batch",
      source_arm: "on" as const,
      source_report_revision: 1,
      benchmark: "swebench-pro" as const,
      task_set: "swebench-pro-public-v2" as const,
      instance_set_digest: "a".repeat(64),
      total_tasks: 731,
      resolved_tasks: 42,
      execution_failures: 0,
      model: "gpt-5.6-sol",
      reasoning_effort: "medium" as const,
      dataset_revision: "dataset",
      harness_revision: "harness",
      powercontext_sha: "b".repeat(40),
      codex_version: "0.145.0",
      created_at: "2026-08-23T01:00:00Z",
    });
    const updateBaselineSelections = vi.fn().mockResolvedValue([]);
    render(<BatchLauncher api={apiStub({
      listBaselines: vi.fn().mockResolvedValue([baseline("base-2", "新基线"), baseline("base-1", "旧基线")]),
      updateBaselineSelections,
    })} onCreated={() => undefined} />);

    await user.click(await screen.findByRole("checkbox", { name: /新基线/ }));
    await user.click(screen.getByRole("checkbox", { name: /旧基线/ }));
    await user.click(screen.getByRole("button", { name: "开始评测" }));

    await waitFor(() => expect(updateBaselineSelections).toHaveBeenCalledWith(
      "batch-001",
      [
        { baseline_id: "base-2", current_arm: "on" },
        { baseline_id: "base-1", current_arm: "on" },
      ],
      expect.any(AbortSignal),
    ));
  });

  it("opens an already-created report when an initial baseline selection is incompatible", async () => {
    const user = userEvent.setup();
    const onCreated = vi.fn();
    render(<BatchLauncher api={apiStub({
      listBaselines: vi.fn().mockResolvedValue([{
        baseline_id: "base-incompatible",
        name: "历史基线",
        source_batch_id: "source-batch",
        source_arm: "on",
        source_report_revision: 1,
        benchmark: "swebench-pro",
        task_set: "swebench-pro-public-v2",
        instance_set_digest: "a".repeat(64),
        total_tasks: 731,
        resolved_tasks: 42,
        execution_failures: 0,
        model: "gpt-5.6-sol",
        reasoning_effort: "medium",
        dataset_revision: "old-dataset",
        harness_revision: "old-harness",
        powercontext_sha: "b".repeat(40),
        codex_version: "0.145.0",
        created_at: "2026-08-23T01:00:00Z",
      }]),
      updateBaselineSelections: vi.fn().mockRejectedValue(new Error("incompatible")),
    })} onCreated={onCreated} />);

    await user.click(await screen.findByRole("checkbox", { name: /历史基线/ }));
    await user.click(screen.getByRole("button", { name: "开始评测" }));

    await waitFor(() => expect(onCreated).toHaveBeenCalledWith(expect.objectContaining({ batch_id: "batch-001" })));
    expect(screen.queryByText("提交失败；幂等键已保留，可以安全重试。")).not.toBeInTheDocument();
  });

  it("does not create work when admission preview reports a usage block", async () => {
    const user = userEvent.setup();
    const createBatch = vi.fn();
    render(<BatchLauncher api={apiStub({
      previewBatch: vi.fn().mockResolvedValue(preview({ can_start: false, block_reason: "usage_threshold_reached" })),
      createBatch,
    })} onCreated={() => undefined} />);

    await user.click(await screen.findByRole("button", { name: "开始评测" }));

    expect(await screen.findByText("当前用量已达到暂停阈值，暂时不能创建评测。")).toBeVisible();
    expect(createBatch).not.toHaveBeenCalled();
  });
});
