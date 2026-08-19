import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { AttemptHistory } from "./AttemptHistory";
import { apiStub, batchTask } from "../test/fixtures";
import type { TaskAttempt } from "../types";

const firstAttempt: TaskAttempt = {
  attempt_id: "task-001.attempt-0001",
  task_id: "task-001",
  attempt_number: 1,
  status: "failed",
  phase: "running_on",
  created_at: "2026-07-29T01:00:00Z",
  started_at: "2026-07-29T01:01:00Z",
  finished_at: "2026-07-29T01:02:00Z",
  version: 3,
  failure_category: "codex_execution_failure",
  failure_phase: "running_on",
  failure_summary: "Codex execution did not complete",
  result: null,
  retryable: true,
};

describe("AttemptHistory", () => {
  it("requires confirmation and sends one retry intent only for a retryable task", async () => {
    const user = userEvent.setup();
    const retryTask = vi.fn().mockResolvedValue({ ...firstAttempt, attempt_id: "task-001.attempt-0002", attempt_number: 2 });
    const onRetried = vi.fn();
    render(
      <AttemptHistory
        api={apiStub({
          listTaskAttempts: vi.fn().mockResolvedValue([firstAttempt]),
          retryTask,
        })}
        batchId="batch-001"
        task={batchTask({
          status: "failed",
          pair_category: "execution_failure",
          retryable: true,
          failure_category: "codex_execution_failure",
          failure_summary: "Codex execution did not complete",
          off: null,
          on: null,
          tokens: { off: null, on: null, delta: null },
        })}
        onSelect={() => undefined}
        onRetried={onRetried}
      />,
    );

    await user.click(await screen.findByRole("button", { name: "重试此任务" }));
    expect(screen.getByText("只重新运行这个基础设施失败的任务，已有尝试和证据不会被覆盖。")).toBeVisible();
    expect(retryTask).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "确认重试" }));
    await waitFor(() => expect(retryTask).toHaveBeenCalledTimes(1));
    expect(retryTask).toHaveBeenCalledWith(
      "batch-001",
      "task-001",
      expect.stringMatching(/^[A-Za-z0-9._-]{8,128}$/),
      expect.any(AbortSignal),
    );
    expect(onRetried).toHaveBeenCalled();
  });

  it("keeps every immutable attempt selectable and hides retry for a valid outcome", async () => {
    const user = userEvent.setup();
    const secondAttempt = {
      ...firstAttempt,
      attempt_id: "task-001.attempt-0002",
      attempt_number: 2,
      status: "succeeded" as const,
      failure_category: null,
      failure_phase: null,
      failure_summary: null,
      result: {
        artifact_dir: "runs/task-001.attempt-0002",
        report_path: "runs/task-001.attempt-0002/report.md",
        off_resolved: false,
        on_resolved: true,
      },
      retryable: false,
    };
    const onSelect = vi.fn();
    render(
      <AttemptHistory
        api={apiStub({ listTaskAttempts: vi.fn().mockResolvedValue([firstAttempt, secondAttempt]) })}
        batchId="batch-001"
        task={batchTask({ attempt_number: 2, attempt_count: 2 })}
        onSelect={onSelect}
        onRetried={() => undefined}
      />,
    );

    await user.click(await screen.findByRole("button", { name: "尝试 1" }));
    expect(onSelect).toHaveBeenCalledWith("task-001.attempt-0001");
    expect(screen.getByRole("button", { name: "尝试 2" })).toBeVisible();
    expect(screen.queryByRole("button", { name: "重试此任务" })).not.toBeInTheDocument();
  });
});
