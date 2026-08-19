import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { TaskRunDetail } from "./TaskRunDetail";
import { apiStub, batchRecord, batchTaskDetail } from "../test/fixtures";

describe("TaskRunDetail", () => {
  it("shows independent per-arm TokensFlow finalization without overriding evaluation success", async () => {
    const api = apiStub({
      getBatch: vi.fn().mockResolvedValue(batchRecord({ status: "completed" })),
      getBatchTask: vi.fn().mockResolvedValue({
        ...batchTaskDetail,
        tokensflow_finalization: {
          off: {
            state: "passed",
            registered_at: "2026-08-03T01:00:00Z",
            deadline_at: "2026-08-03T01:10:00Z",
            finished_at: "2026-08-03T01:01:00Z",
            attempts: 1,
            queue_passed: true,
            doctor_rc: 0,
            error_category: null,
            reason: null,
          },
          on: {
            state: "timed_out",
            registered_at: "2026-08-03T01:02:00Z",
            deadline_at: "2026-08-03T01:12:00Z",
            finished_at: "2026-08-03T01:12:00Z",
            attempts: 8,
            queue_passed: false,
            doctor_rc: 1,
            error_category: null,
            reason: "deadline",
          },
        },
      }),
    });

    render(
      <TaskRunDetail
        api={api}
        batchId="batch-001"
        taskId="task-001"
        search=""
        navigate={() => undefined}
      />,
    );

    expect(await screen.findByRole("heading", { name: "单任务详情" })).toBeVisible();
    expect(screen.getByLabelText("任务对比汇总")).toHaveTextContent("OFF 通过");
    expect(screen.getByText("OFF TokensFlow 收尾完成")).toBeVisible();
    expect(screen.getByText("ON TokensFlow 收尾超时")).toBeVisible();
  });

  it("does not request a complete timeline for an infrastructure-failed attempt", async () => {
    const listContextEvents = vi.fn();
    const api = apiStub({
      getBatch: vi.fn().mockResolvedValue(batchRecord({ status: "completed" })),
      getBatchTask: vi.fn().mockResolvedValue({
        ...batchTaskDetail,
        task: {
          ...batchTaskDetail.task,
          status: "failed",
          retryable: true,
          pair_category: "execution_failure",
          off: null,
          on: null,
          tokens: { off: null, on: null, delta: null },
          failure_category: "codex_execution_failure",
          failure_summary: "Codex execution failed.",
        },
        off: null,
        on: null,
      }),
      listContextEvents,
    });

    render(
      <TaskRunDetail
        api={api}
        batchId="batch-001"
        taskId="task-001"
        search=""
        navigate={() => undefined}
      />,
    );

    expect(await screen.findByRole("heading", { name: "单任务详情" })).toBeVisible();
    expect(screen.getByText("Codex execution failed.")).toBeVisible();
    expect(screen.getByText("本次尝试没有形成可用的完整上下文时间线。")).toBeVisible();
    expect(listContextEvents).not.toHaveBeenCalled();
  });

  it("renders a cancelled task as not executed instead of failed", async () => {
    const api = apiStub({
      getBatch: vi.fn().mockResolvedValue(batchRecord({ status: "cancelled" })),
      getBatchTask: vi.fn().mockResolvedValue({
        ...batchTaskDetail,
        task: {
          ...batchTaskDetail.task,
          status: "cancelled",
          pair_category: null,
          off: null,
          on: null,
          tokens: { off: null, on: null, delta: null },
        },
        off: null,
        on: null,
      }),
    });

    render(
      <TaskRunDetail
        api={api}
        batchId="batch-001"
        taskId="task-001"
        search=""
        navigate={() => undefined}
      />,
    );

    expect(await screen.findByRole("heading", { name: "单任务详情" })).toBeVisible();
    expect(screen.getByLabelText("任务对比汇总")).toHaveTextContent("已取消");
    expect(screen.getByText("任务未执行，因此没有官方评测结果。")).toBeVisible();
    expect(screen.queryByText("OFF 未通过")).not.toBeInTheDocument();
    expect(screen.queryByText("ON 未通过")).not.toBeInTheDocument();
    expect(screen.queryByText("评测执行失败")).not.toBeInTheDocument();
  });

  it("renders official result evidence once and expands the complete task in place", async () => {
    const longProblem = "完整问题：" + "需要保留的上下文。".repeat(80);
    const api = apiStub({
      getBatch: vi.fn().mockResolvedValue(
        batchRecord({
          status: "completed",
          resolved_powercontext_sha: "a".repeat(40),
        }),
      ),
      getBatchTask: vi.fn().mockResolvedValue({ ...batchTaskDetail, problem_statement: longProblem }),
    });
    render(
      <TaskRunDetail
        api={api}
        batchId="batch-001"
        taskId="task-001"
        search="?category=off_pass_on_fail&sort=token_delta_desc"
        navigate={() => undefined}
      />,
    );

    expect(await screen.findByRole("heading", { name: "单任务详情" })).toBeVisible();
    expect(screen.getByText("instance_owner__repo-001")).toBeVisible();
    expect(screen.getByText("owner/repo")).toBeVisible();
    expect(screen.getByText("OFF 通过")).toBeVisible();
    expect(screen.getByText("ON 未通过")).toBeVisible();
    expect(screen.getByText("OFF 110")).toBeVisible();
    expect(screen.getByText("ON 135")).toBeVisible();
    expect(screen.getByText("差值 +25")).toBeVisible();
    expect(screen.getByRole("link", { name: "返回任务详细报告" })).toHaveAttribute(
      "href",
      "/report/batch-001/tasks?category=off_pass_on_fail&sort=token_delta_desc",
    );

    expect(screen.queryByText(longProblem)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "展开完整任务描述" }));
    expect(screen.getByText(longProblem)).toBeVisible();

    const off = screen.getByLabelText("OFF 官方评测");
    expect(within(off).getByText("补丁应用成功")).toBeVisible();
    expect(within(off).getByText("FAIL_TO_PASS 1 / 1")).toBeVisible();
    expect(within(off).getByText("PASS_TO_PASS 1 / 1")).toBeVisible();
    expect(within(off).getAllByText("已解决")).toHaveLength(1);

    const on = screen.getByLabelText("ON 官方评测");
    expect(within(on).getByText("补丁应用成功")).toBeVisible();
    expect(within(on).getByText("FAIL_TO_PASS 0 / 1")).toBeVisible();
    expect(within(on).getByText("失败测试：test_issue")).toBeVisible();
    expect(within(on).getByText("test_issue failed")).toBeVisible();
    expect(within(on).getAllByText("未解决")).toHaveLength(1);
    expect(screen.queryByText(/生命周期|处理有效性|补丁大小|N\/A|验收/)).not.toBeInTheDocument();
  });

  it("decodes quoted dataset sections for display without exposing literal newline escapes", async () => {
    const encodedSection = JSON.stringify(`**Title**\n\n${"Readable description. ".repeat(24)}`);
    const encodedRequirement = JSON.stringify("- first requirement\n- second requirement");
    const rawProblem = `${encodedSection}\n\nRequirements:\n${encodedRequirement}`;
    const readableProblem = `${JSON.parse(encodedSection)}\n\nRequirements:\n${JSON.parse(encodedRequirement)}`;
    const api = apiStub({
      getBatch: vi.fn().mockResolvedValue(batchRecord({ status: "completed" })),
      getBatchTask: vi.fn().mockResolvedValue({ ...batchTaskDetail, problem_statement: rawProblem }),
    });

    render(
      <TaskRunDetail
        api={api}
        batchId="batch-001"
        taskId="task-001"
        search=""
        navigate={() => undefined}
      />,
    );

    expect(await screen.findByRole("heading", { name: "单任务详情" })).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "展开完整任务描述" }));
    expect(screen.getByLabelText("任务描述")).toHaveTextContent(readableProblem, { normalizeWhitespace: false });
    expect(screen.queryByText(/\\n/)).not.toBeInTheDocument();
  });
});
