import { act, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { TaskEvent } from "../types";
import { TaskDetail } from "./TaskDetail";
import { apiStub, deferred, record } from "../test/fixtures";

describe("TaskDetail", () => {
  afterEach(() => vi.useRealTimers());
  it("shows immutable parameters, truthful timeline, safe failure, and successful report link", async () => {
    const api = apiStub({ getTask: vi.fn().mockResolvedValue(record("succeeded", "task-done")) });
    const { rerender } = render(<TaskDetail api={api} taskId="task-done" />);
    expect(await screen.findByText("不可变提交参数")).toBeVisible();
    expect(screen.getByText("生成报告")).toBeVisible();
    expect(screen.getByRole("link", { name: "查看验收报告" })).toHaveAttribute("href", "/reports/task-done");
    expect(screen.getAllByRole("time")).toHaveLength(3);
    expect(screen.getAllByRole("time").map((element) => element.getAttribute("datetime"))).toEqual([
      "2026-07-29T01:00:00Z",
      "2026-07-29T01:01:00Z",
      "2026-07-29T01:02:00Z",
    ]);

    rerender(
      <TaskDetail
        api={apiStub({ getTask: vi.fn().mockResolvedValue(record("failed", "task-failed")) })}
        taskId="task-failed"
      />,
    );
    expect(await screen.findByText("安全的失败摘要")).toBeVisible();
    expect(screen.getByText("Codex 执行失败")).toBeVisible();
  });

  it("labels an upstream capacity failure as retryable", async () => {
    const failed = { ...record("failed", "task-capacity"), failure_category: "codex_capacity_failure" as const };
    render(<TaskDetail api={apiStub({ getTask: vi.fn().mockResolvedValue(failed) })} taskId="task-capacity" />);

    expect(await screen.findByText("上游模型容量不足（可重试）")).toBeVisible();
  });

  it("subscribes once, refreshes on SSE, exposes reconnect state, and cleans up on task change", async () => {
    let onEvent!: (event: TaskEvent) => void;
    let onError!: (error: { message: string; reconnecting: boolean; code: "event_stream_disconnected" }) => void;
    const close = vi.fn();
    const subscribeTaskEvents = vi.fn((_id, eventHandler, errorHandler) => {
      onEvent = eventHandler;
      onError = errorHandler;
      return { close };
    });
    const getTask = vi.fn().mockResolvedValue(record("running", "task-a"));
    const api = apiStub({ getTask, subscribeTaskEvents });
    const onTaskChanged = vi.fn();
    const { rerender, unmount } = render(<TaskDetail api={api} taskId="task-a" onTaskChanged={onTaskChanged} />);
    await screen.findByText("OFF 执行");
    expect(subscribeTaskEvents).toHaveBeenCalledTimes(1);
    expect(onTaskChanged).toHaveBeenCalledWith(expect.objectContaining({ task_id: "task-a" }));

    act(() =>
      onError({
        code: "event_stream_disconnected",
        message: "disconnected",
        reconnecting: true,
      }),
    );
    expect(screen.getByText("实时连接中断，正在定时刷新。")).toBeVisible();
    act(() =>
      onEvent({
        task_id: "task-a",
        status: "running",
        phase: "running_on",
        version: 2,
        occurred_at: "2026-07-29T01:02:00Z",
      }),
    );
    await waitFor(() => expect(getTask).toHaveBeenCalledTimes(2));
    expect(onTaskChanged).toHaveBeenCalledTimes(2);

    rerender(<TaskDetail api={api} taskId="task-b" onTaskChanged={onTaskChanged} />);
    expect(close).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(subscribeTaskEvents).toHaveBeenCalledTimes(2));
    unmount();
    expect(close).toHaveBeenCalledTimes(2);
  });

  it("stops fallback polling after a valid event and can restart it after another error", async () => {
    vi.useFakeTimers();
    let onEvent!: (event: TaskEvent) => void;
    let onError!: () => void;
    const subscribeTaskEvents = vi.fn((_id, eventHandler, errorHandler) => {
      onEvent = eventHandler;
      onError = errorHandler;
      return { close: vi.fn() };
    });
    const getTask = vi.fn().mockResolvedValue(record("running", "task-recovery"));
    render(<TaskDetail api={apiStub({ getTask, subscribeTaskEvents })} taskId="task-recovery" />);
    await vi.waitFor(() => expect(getTask).toHaveBeenCalledTimes(1));

    act(() => onError());
    expect(screen.getByText("实时连接中断，正在定时刷新。")).toBeVisible();
    await act(async () => vi.advanceTimersByTimeAsync(5000));
    expect(getTask).toHaveBeenCalledTimes(2);

    act(() =>
      onEvent({
        task_id: "task-recovery",
        status: "running",
        phase: "running_on",
        version: 2,
        occurred_at: "2026-07-29T01:02:00Z",
      }),
    );
    await vi.waitFor(() => expect(getTask).toHaveBeenCalledTimes(3));
    expect(screen.queryByText("实时连接中断，正在定时刷新。")).not.toBeInTheDocument();
    await act(async () => vi.advanceTimersByTimeAsync(10000));
    expect(getTask).toHaveBeenCalledTimes(3);

    act(() => onError());
    await act(async () => vi.advanceTimersByTimeAsync(5000));
    expect(getTask).toHaveBeenCalledTimes(4);
  });

  it("ignores a stale task response after the route changes", async () => {
    const first = deferred<ReturnType<typeof record>>();
    const second = deferred<ReturnType<typeof record>>();
    const getTask = vi.fn().mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
    const changed = vi.fn();
    const api = apiStub({ getTask });
    const { rerender } = render(<TaskDetail api={api} taskId="task-a" onTaskChanged={changed} />);
    rerender(<TaskDetail api={api} taskId="task-b" onTaskChanged={changed} />);

    second.resolve(record("queued", "task-b"));
    expect(await screen.findByText("task-b")).toBeVisible();
    first.resolve(record("failed", "task-a"));
    await act(async () => first.promise);
    expect(screen.queryByText("task-a")).not.toBeInTheDocument();
    expect(changed).not.toHaveBeenCalledWith(expect.objectContaining({ task_id: "task-a" }));
    expect(getTask.mock.calls[0]?.[1]).toBeInstanceOf(AbortSignal);
  });
});
