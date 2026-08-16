import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { BatchLauncher } from "./BatchLauncher";
import { apiStub, batchEstimate, batchRecord, usageSnapshot } from "../test/fixtures";

function preview(overrides: Record<string, unknown> = {}) {
  return {
    powercontext_ref: "latest",
    benchmark: "swebench-pro" as const,
    task_set: "swebench-pro-public-v2" as const,
    model: "gpt-5.6-sol" as const,
    reasoning_effort: "medium" as const,
    treatment_mode: "off_on" as const,
    total_tasks: 731,
    usage_pause_percent: 80,
    usage: { ...usageSnapshot, used_percent: 9, remaining_percent: 91 },
    estimate: { ...batchEstimate, quality: "preliminary" as const, sample_size: 4 },
    can_start: true,
    block_reason: null,
    ...overrides,
  };
}

describe("BatchLauncher", () => {
  it("shows API-key accounting without a subscription usage snapshot", async () => {
    const user = userEvent.setup();
    const previewBatch = vi.fn().mockResolvedValue(preview({ usage: null }));
    render(<BatchLauncher api={apiStub({ previewBatch })} onCreated={() => undefined} />);

    await user.click(screen.getByRole("button", { name: "预览评测" }));

    expect(await screen.findByText("API Key 计费")).toBeVisible();
    expect(screen.getByText("不适用")).toBeVisible();
    expect(screen.getByText("不采集订阅用量")).toBeVisible();
  });

  it("previews without creating work, then confirms the exact fixed batch", async () => {
    const user = userEvent.setup();
    const previewBatch = vi.fn().mockResolvedValue(preview());
    const createBatch = vi.fn().mockResolvedValue(batchRecord({ batch_id: "batch-created" }));
    const onCreated = vi.fn();
    render(<BatchLauncher api={apiStub({ previewBatch, createBatch })} onCreated={onCreated} />);

    expect(screen.getByLabelText("暂停阈值")).toHaveValue(80);
    await user.click(screen.getByRole("button", { name: "预览评测" }));

    expect(previewBatch).toHaveBeenCalledWith(
      {
        powercontext_ref: "latest",
        task_set: "swebench-pro-public-v2",
        model: "gpt-5.6-sol",
        usage_pause_percent: 80,
      },
      expect.any(AbortSignal),
    );
    expect(createBatch).not.toHaveBeenCalled();
    expect(await screen.findByText("731 个基准任务")).toBeVisible();
    expect(screen.getByText("当前用量 9%")).toBeVisible();
    expect(screen.getByText("7 天")).toBeVisible();
    expect(screen.getByText("初步估算 · 4 个样本")).toBeVisible();

    await user.click(screen.getByRole("button", { name: "确认并开始评测" }));

    await waitFor(() => expect(createBatch).toHaveBeenCalledTimes(1));
    expect(createBatch).toHaveBeenCalledWith(
      expect.objectContaining({
        powercontext_ref: "latest",
        benchmark: "swebench-pro",
        task_set: "swebench-pro-public-v2",
        model: "gpt-5.6-sol",
        reasoning_effort: "medium",
        treatment_mode: "off_on",
        usage_pause_percent: 80,
        idempotency_key: expect.stringMatching(/^[A-Za-z0-9._-]{8,128}$/),
      }),
      expect.any(AbortSignal),
    );
    expect(onCreated).toHaveBeenCalledWith(expect.objectContaining({ batch_id: "batch-created" }));
    expect(document.body.textContent).not.toMatch(/¥|￥|美元|人民币|费用|金额/);
  });

  it("previews and confirms the operator-selected safe model", async () => {
    const user = userEvent.setup();
    const previewBatch = vi.fn().mockResolvedValue(preview({ model: "gpt-5.6-luna" }));
    const createBatch = vi.fn().mockResolvedValue(
      batchRecord({ request: { ...batchRecord().request, model: "gpt-5.6-luna" } }),
    );
    render(
      <BatchLauncher
        api={apiStub({
          previewBatch,
          createBatch,
          getCapabilities: vi.fn().mockResolvedValue({
            benchmarks: ["swebench-pro"],
            instances: ["instance_flipt-io__flipt-518ec324b66a07fdd95464a5e9ca5fe7681ad8f9"],
            models: ["gpt-5.6-sol", "gpt-5.6-luna"],
            reasoning_efforts: ["medium"],
            treatment_modes: ["off_on"],
          }),
        })}
        onCreated={() => undefined}
      />,
    );

    await user.selectOptions(await screen.findByLabelText("Codex 模型"), "gpt-5.6-luna");
    await user.click(screen.getByRole("button", { name: "预览评测" }));

    expect(previewBatch).toHaveBeenCalledWith(
      {
        powercontext_ref: "latest",
        task_set: "swebench-pro-public-v2",
        model: "gpt-5.6-luna",
        usage_pause_percent: 80,
      },
      expect.any(AbortSignal),
    );
    await user.click(await screen.findByRole("button", { name: "确认并开始评测" }));
    await waitFor(() => expect(createBatch).toHaveBeenCalledWith(
      expect.objectContaining({ model: "gpt-5.6-luna", reasoning_effort: "medium" }),
      expect.any(AbortSignal),
    ));
  });

  it("previews and creates the pinned 24-task stability suite", async () => {
    const user = userEvent.setup();
    const previewBatch = vi.fn().mockResolvedValue(
      preview({ task_set: "swebench-pro-stability-v1", total_tasks: 24 }),
    );
    const createBatch = vi.fn().mockResolvedValue(
      batchRecord({
        total_tasks: 24,
        request: { ...batchRecord().request, task_set: "swebench-pro-stability-v1" },
      }),
    );
    render(<BatchLauncher api={apiStub({ previewBatch, createBatch })} onCreated={() => undefined} />);

    await user.selectOptions(screen.getByRole("combobox", { name: "任务集" }), "swebench-pro-stability-v1");
    await user.click(screen.getByRole("button", { name: "预览评测" }));

    expect(previewBatch).toHaveBeenCalledWith(
      expect.objectContaining({ task_set: "swebench-pro-stability-v1" }),
      expect.any(AbortSignal),
    );
    expect(await screen.findByText("24 个基准任务")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "确认并开始评测" }));
    await waitFor(() => expect(createBatch).toHaveBeenCalledWith(
      expect.objectContaining({ task_set: "swebench-pro-stability-v1" }),
      expect.any(AbortSignal),
    ));
  });

  it("only offers models published by runtime capabilities", async () => {
    render(<BatchLauncher api={apiStub()} onCreated={() => undefined} />);

    const model = await screen.findByRole("combobox", { name: "Codex 模型" });
    expect(model).toHaveValue("gpt-5.6-sol");
    expect(within(model).getAllByRole("option").map((option) => option.textContent)).toEqual(["gpt-5.6-sol"]);
  });

  it("creates a batch already paused when the operator selects the atomic pause option", async () => {
    const user = userEvent.setup();
    const previewBatch = vi.fn().mockResolvedValue(preview());
    const createBatch = vi.fn().mockResolvedValue(
      batchRecord({
        request: { ...batchRecord().request, initial_control_intent: "pause" },
        status: "paused",
      }),
    );
    render(<BatchLauncher api={apiStub({ previewBatch, createBatch })} onCreated={() => undefined} />);

    await user.click(screen.getByRole("checkbox", { name: "创建后保持暂停" }));
    await user.click(screen.getByRole("button", { name: "预览评测" }));
    await user.click(await screen.findByRole("button", { name: "确认并开始评测" }));

    await waitFor(() => expect(createBatch).toHaveBeenCalledWith(
      expect.objectContaining({ initial_control_intent: "pause" }),
      expect.any(AbortSignal),
    ));
  });

  it("invalidates stale previews and clearly represents unavailable estimates or blocked usage", async () => {
    const user = userEvent.setup();
    const previewBatch = vi
      .fn()
      .mockResolvedValueOnce(
        preview({
          estimate: {
            ...batchEstimate,
            quality: "unavailable",
            basis: "none",
            sample_size: 0,
            remaining_tokens: null,
            remaining_duration_seconds: null,
            low_tokens: null,
            high_tokens: null,
            low_duration_seconds: null,
            high_duration_seconds: null,
          },
        }),
      )
      .mockResolvedValueOnce(
        preview({
          usage: { ...usageSnapshot, used_percent: 80, remaining_percent: 20 },
          can_start: false,
          block_reason: "usage_threshold_reached",
        }),
      );
    const createBatch = vi.fn();
    render(<BatchLauncher api={apiStub({ previewBatch, createBatch })} onCreated={() => undefined} />);

    await user.click(screen.getByRole("button", { name: "预览评测" }));
    expect(await screen.findByText("暂无可靠估算")).toBeVisible();

    await user.clear(screen.getByLabelText("PowerContext 版本"));
    await user.type(screen.getByLabelText("PowerContext 版本"), "latest");
    expect(screen.queryByRole("button", { name: "确认并开始评测" })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "预览评测" }));
    expect(await screen.findByText("当前用量已达到暂停阈值")).toBeVisible();
    expect(screen.getByRole("button", { name: "确认并开始评测" })).toBeDisabled();
    expect(createBatch).not.toHaveBeenCalled();
  });

  it("keeps one confirmation key across a transient submission failure", async () => {
    const user = userEvent.setup();
    const createBatch = vi
      .fn()
      .mockRejectedValueOnce(new Error("network"))
      .mockResolvedValueOnce(batchRecord({ batch_id: "batch-retried" }));
    render(
      <BatchLauncher
        api={apiStub({ previewBatch: vi.fn().mockResolvedValue(preview()), createBatch })}
        onCreated={() => undefined}
      />,
    );

    await user.click(screen.getByRole("button", { name: "预览评测" }));
    await user.click(await screen.findByRole("button", { name: "确认并开始评测" }));
    expect(await screen.findByText("提交失败，未创建新的确认意图；可以安全重试。")).toBeVisible();
    const firstKey = createBatch.mock.calls[0]?.[0].idempotency_key;

    await user.click(screen.getByRole("button", { name: "确认并开始评测" }));
    await waitFor(() => expect(createBatch).toHaveBeenCalledTimes(2));
    expect(createBatch.mock.calls[1]?.[0].idempotency_key).toBe(firstKey);
  });
});
