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

import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { TaskList } from "./TaskList";
import { apiStub, deferred, instanceId, record, summary } from "../test/fixtures";

describe("TaskList", () => {
  afterEach(() => vi.useRealTimers());
  it("shows truthful distinct statuses, fields, running emphasis, queue position, and no percent", async () => {
    const tasks = ["queued", "running", "succeeded", "failed", "interrupted", "cancelled"].map((status) =>
      summary(status as ReturnType<typeof summary>["status"]),
    );
    render(<TaskList api={apiStub({ listTasks: vi.fn().mockResolvedValue(tasks) })} onSelect={() => undefined} />);

    expect(await screen.findByText("排队中")).toBeVisible();
    for (const label of ["运行中", "已完成", "失败", "已中断", "已取消"]) {
      expect(screen.getAllByText(label).some((element) => element.matches(".status"))).toBe(true);
    }
    expect(screen.getByText("队列第 2 位")).toBeVisible();
    expect(screen.getByText("OFF 执行")).toBeVisible();
    expect(screen.getByRole("row", { name: /task-running/ })).toHaveClass("task-row--running");
    expect(screen.queryByText(/%/)).not.toBeInTheDocument();
  });

  it("applies status filter and cancels only queued tasks after confirmation then refreshes", async () => {
    const listTasks = vi
      .fn()
      .mockResolvedValueOnce([summary("queued")])
      .mockResolvedValueOnce([summary("cancelled")])
      .mockResolvedValueOnce([]);
    const cancelTask = vi.fn().mockResolvedValue(record("cancelled"));
    vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<TaskList api={apiStub({ listTasks, cancelTask })} onSelect={() => undefined} />);

    await screen.findByText("排队中");
    fireEvent.click(screen.getByRole("button", { name: "取消 task-queued" }));
    await waitFor(() => expect(cancelTask).toHaveBeenCalledWith("task-queued"));
    await waitFor(() => expect(screen.getAllByText("已取消").some((element) => element.matches(".status"))).toBe(true));

    fireEvent.change(screen.getByLabelText("状态筛选"), { target: { value: "running" } });
    await waitFor(() =>
      expect(listTasks).toHaveBeenLastCalledWith(
        { limit: 50, offset: 0, status: "running" },
        expect.any(AbortSignal),
      ),
    );
    expect(screen.getByText("没有符合条件的任务。")).toBeVisible();
  });

  it("offers retry after a safe loading error", async () => {
    const listTasks = vi.fn().mockRejectedValueOnce(new Error("secret")).mockResolvedValueOnce([]);
    render(<TaskList api={apiStub({ listTasks })} onSelect={() => undefined} />);
    expect(await screen.findByText("任务列表暂时无法加载。")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    expect(await screen.findByText("还没有测试任务。")).toBeVisible();
  });

  it("keeps a queued row actionable after cancellation rejection and succeeds on retry", async () => {
    const user = userEvent.setup();
    const listTasks = vi
      .fn()
      .mockResolvedValueOnce([summary("queued")])
      .mockResolvedValueOnce([summary("cancelled")]);
    const cancelTask = vi
      .fn()
      .mockRejectedValueOnce(new Error("<raw>private upstream</raw>"))
      .mockResolvedValueOnce(record("cancelled"));
    vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<TaskList api={apiStub({ listTasks, cancelTask })} onSelect={() => undefined} />);

    const cancel = await screen.findByRole("button", { name: "取消 task-queued" });
    await user.click(cancel);
    expect(await screen.findByText("任务取消失败，请重试。")).toBeVisible();
    expect(screen.queryByText(/private|upstream|raw/i)).not.toBeInTheDocument();
    expect(cancel).toBeEnabled();
    expect(screen.getAllByText("排队中").some((element) => element.matches(".status"))).toBe(true);

    await user.click(cancel);
    await waitFor(() => expect(cancelTask).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.getAllByText("已取消").some((element) => element.matches(".status"))).toBe(true));
  });

  it("ignores an older list response after the status filter changes", async () => {
    const all = deferred<ReturnType<typeof summary>[]>();
    const running = deferred<ReturnType<typeof summary>[]>();
    const listTasks = vi.fn().mockReturnValueOnce(all.promise).mockReturnValueOnce(running.promise);
    render(<TaskList api={apiStub({ listTasks })} onSelect={() => undefined} />);
    fireEvent.change(screen.getByLabelText("状态筛选"), { target: { value: "running" } });
    running.resolve([summary("running", "task-new")]);
    expect(await screen.findByText("task-new")).toBeVisible();
    all.resolve([summary("queued", "task-stale")]);
    await waitFor(() => expect(screen.queryByText("task-stale")).not.toBeInTheDocument());
    expect(listTasks.mock.calls[0]?.[1]).toBeInstanceOf(AbortSignal);
  });

  it("shows truthful queue wait, semantic caption, machine-readable time, and full truncated identifiers", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-07-29T01:02:05Z"));
    render(
      <TaskList
        api={apiStub({
          listTasks: vi.fn().mockResolvedValue([
            summary("queued", "task-queued"),
            summary("running", "task-running"),
            summary("cancelled", "task-cancelled"),
          ]),
        })}
        onSelect={() => undefined}
      />,
    );
    await vi.waitFor(() => expect(screen.getByText("2 分 5 秒")).toBeVisible());
    expect(screen.getByText("1 分钟")).toBeVisible();
    expect(screen.getByText("2 分钟")).toBeVisible();
    expect(screen.getByRole("table")).toHaveAccessibleName("测试任务队列");
    expect(screen.getAllByRole("time")[0]).toHaveAttribute("datetime", "2026-07-29T01:00:00Z");
    const instance = screen.getAllByTitle(instanceId)[0];
    expect(instance).toHaveAccessibleName(instanceId);
    await act(async () => vi.advanceTimersByTimeAsync(60_000));
    expect(screen.getByText("3 分 5 秒")).toBeVisible();
    vi.useRealTimers();
  });

  it("does not refresh the old filter when cancellation resolves after a filter change", async () => {
    const user = userEvent.setup();
    const cancellation = deferred<ReturnType<typeof record>>();
    const listTasks = vi
      .fn()
      .mockResolvedValueOnce([summary("queued")])
      .mockResolvedValueOnce([summary("running", "task-running-filtered")]);
    const cancelTask = vi.fn().mockReturnValue(cancellation.promise);
    vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<TaskList api={apiStub({ listTasks, cancelTask })} onSelect={() => undefined} />);
    await user.click(await screen.findByRole("button", { name: "取消 task-queued" }));
    fireEvent.change(screen.getByLabelText("状态筛选"), { target: { value: "running" } });
    expect(await screen.findByText("task-running-filtered")).toBeVisible();

    cancellation.resolve(record("cancelled"));
    await act(async () => cancellation.promise);
    expect(screen.getByText("task-running-filtered")).toBeVisible();
    expect(listTasks).toHaveBeenCalledTimes(2);
  });
});
