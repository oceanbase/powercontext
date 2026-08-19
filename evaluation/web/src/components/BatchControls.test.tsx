import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { BatchControls } from "./BatchControls";
import { apiStub, batchRecord, batchReport, usageSnapshot } from "../test/fixtures";

describe("BatchControls", () => {
  it("shows account usage and updates the threshold without implicitly resuming", async () => {
    const user = userEvent.setup();
    const updateBatchThreshold = vi.fn().mockResolvedValue(batchRecord());
    const resumeBatch = vi.fn();
    const api = apiStub({
      getAccountUsage: vi.fn().mockResolvedValue(usageSnapshot),
      listBatchControlEvents: vi.fn().mockResolvedValue([]),
      updateBatchThreshold,
      resumeBatch,
    });
    render(
      <BatchControls
        api={api}
        batch={batchRecord({ status: "running" })}
        report={{ ...batchReport, terminal_tasks: 18, total_tasks: 731 }}
        onUpdated={() => undefined}
      />,
    );

    expect(await screen.findByText("Codex 账户用量")).toBeVisible();
    expect(screen.getByText("32%")).toBeVisible();
    expect(screen.getByText(/计量窗口 7 天/)).toBeVisible();
    expect(screen.getByText("18 / 731")).toBeVisible();
    expect(screen.getByText("已测量 · 100 个样本")).toBeVisible();
    expect(screen.getByRole("button", { name: "暂停" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "取消批次" })).toBeEnabled();

    const threshold = screen.getByLabelText("批次暂停阈值");
    await user.clear(threshold);
    await user.type(threshold, "90");
    await user.click(screen.getByRole("button", { name: "保存阈值" }));

    await waitFor(() => expect(updateBatchThreshold).toHaveBeenCalledWith("batch-001", 90, 0));
    expect(resumeBatch).not.toHaveBeenCalled();
  });

  it.each([
    ["pausing", "等待当前任务完成"],
    ["paused", "继续运行"],
    ["cancelling", "等待当前任务完成后取消"],
  ] as const)("renders the %s control state factually", async (status, label) => {
    render(
      <BatchControls
        api={apiStub()}
        batch={batchRecord({ status })}
        report={batchReport}
        onUpdated={() => undefined}
      />,
    );

    expect(await screen.findByText(label)).toBeVisible();
  });

  it("labels an infrastructure-failure pause", async () => {
    render(
      <BatchControls
        api={apiStub()}
        batch={batchRecord({
          status: "paused",
          control: {
            intent: "pause",
            usage_pause_percent: 80,
            pause_reason: "infrastructure_failure",
            updated_at: "2026-08-03T00:00:00Z",
            version: 1,
          },
        })}
        report={batchReport}
        onUpdated={() => undefined}
      />,
    );

    expect(await screen.findByText("暂停原因：基础设施失败")).toBeVisible();
  });

  it("labels a pause caused by an exhausted upstream capacity retry budget", async () => {
    render(
      <BatchControls
        api={apiStub()}
        batch={batchRecord({
          status: "paused",
          control: {
            intent: "pause",
            usage_pause_percent: 95,
            pause_reason: "codex_capacity",
            updated_at: "2026-08-04T00:00:00Z",
            version: 31,
          },
        })}
        report={batchReport}
        onUpdated={() => undefined}
      />,
    );

    expect(await screen.findByText("暂停原因：上游模型容量不足（自动重试耗尽）")).toBeVisible();
  });
});
