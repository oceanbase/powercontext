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

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";
import { apiStub, batchRecord } from "./test/fixtures";

describe("App batch report navigation", () => {
  beforeEach(() => window.history.replaceState({}, "", "/"));

  it("shows the report destinations and a complete-batch launcher", async () => {
    render(<App api={apiStub()} />);

    expect(screen.getByRole("heading", { name: "总体报告" })).toBeVisible();
    expect(screen.getByRole("navigation", { name: "报告导航" })).toBeVisible();
    expect(screen.getByRole("link", { name: "总体报告" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: "任务详细报告" })).toHaveAttribute("aria-disabled", "true");
    expect(screen.getByRole("link", { name: "当前运行任务" })).toHaveAttribute("aria-disabled", "true");
    expect(screen.getAllByRole("navigation")).toHaveLength(1);
    expect(screen.getAllByRole("link")).toHaveLength(5);
    expect(screen.queryByRole("link", { name: /工作台|测试任务|验收报告|单任务详情/ })).not.toBeInTheDocument();
    expect(await screen.findByRole("button", { name: "开始评测" })).toBeVisible();
    expect(await screen.findByText("Worker 工作中")).toBeVisible();
    expect(screen.getByText("任务对 3 / 4")).toBeVisible();
    expect(screen.getByText("队列 1")).toBeVisible();
    expect(screen.getByText("资源门禁开放")).toBeVisible();
    expect(screen.getByText("Worker 按配置并行运行独立任务")).toBeVisible();
    expect(screen.queryByText("全局同时只运行一个任务，其余任务排队")).not.toBeInTheDocument();
  });

  it("describes an inactive worker lease as idle instead of disconnected", async () => {
    const api = apiStub({
      getHealth: vi.fn().mockResolvedValue({
        service: "ok",
        worker_lease_active: false,
        queued_tasks: 0,
        running_tasks: 0,
        active_task_pairs: 0,
        task_parallelism: 4,
        resource_admission_open: true,
        filesystem_free_bytes: 200_000_000_000,
        filesystem_total_bytes: 400_000_000_000,
        filesystem_min_free_bytes: 20_000_000_000,
        filesystem_free_inodes: 20_000_000,
        filesystem_total_inodes: 40_000_000,
        filesystem_min_free_inodes: 1_000_000,
      }),
    });

    render(<App api={api} />);

    expect(await screen.findByText("Worker 空闲")).toBeVisible();
    expect(screen.queryByText("Worker 未连接")).not.toBeInTheDocument();
  });

  it("shows when filesystem resource admission is closed", async () => {
    const api = apiStub({
      getHealth: vi.fn().mockResolvedValue({
        service: "ok",
        worker_lease_active: false,
        queued_tasks: 1,
        running_tasks: 0,
        active_task_pairs: 0,
        task_parallelism: 20,
        resource_admission_open: false,
        filesystem_free_bytes: 1,
        filesystem_total_bytes: 400_000_000_000,
        filesystem_min_free_bytes: 80 * 1024 ** 3,
        filesystem_free_inodes: 1,
        filesystem_total_inodes: 40_000_000,
        filesystem_min_free_inodes: 5_000_000,
      }),
    });

    render(<App api={api} />);

    expect(await screen.findByText("资源门禁关闭")).toBeVisible();
  });

  it("navigates a newly created batch to its aggregate report", async () => {
    const created = batchRecord({ batch_id: "batch/new" });
    const api = apiStub({ createBatch: vi.fn().mockResolvedValue(created) });
    render(<App api={api} />);

    fireEvent.click(await screen.findByRole("button", { name: "开始评测" }));

    await waitFor(() => expect(window.location.pathname).toBe("/report/batch%2Fnew"));
    expect(await screen.findByRole("heading", { name: "总体报告" })).toBeVisible();
    expect(screen.getByRole("link", { name: "任务详细报告" })).not.toHaveAttribute("aria-disabled");
    expect(screen.getByRole("link", { name: "当前运行任务" })).not.toHaveAttribute("aria-disabled");
  });

  it("routes the current-running destination to its live subpage", async () => {
    window.history.replaceState({}, "", "/report/batch-one/running");
    render(<App api={apiStub()} />);

    expect(await screen.findByRole("heading", { name: "当前运行任务" })).toBeVisible();
    expect(screen.getByRole("link", { name: "当前运行任务" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: "总体报告" })).not.toHaveAttribute("aria-current");
  });

  it("keeps the task-report destination active on a contextual task detail route", async () => {
    window.history.replaceState({}, "", "/report/batch-one/tasks/task-one");
    render(<App api={apiStub()} />);

    expect(await screen.findByRole("heading", { name: "单任务详情" })).toBeVisible();
    expect(screen.getByRole("link", { name: "任务详细报告" })).toHaveAttribute("aria-current", "page");
    expect(screen.queryByRole("link", { name: "单任务详情" })).not.toBeInTheDocument();
  });

  it("never invents a task detail when no concrete task was selected", async () => {
    window.history.replaceState({}, "", "/report/batch-one/tasks");
    render(<App api={apiStub()} />);

    expect(await screen.findByRole("heading", { name: "任务详细报告" })).toBeVisible();
    expect(screen.queryByRole("heading", { name: "单任务详情" })).not.toBeInTheDocument();
  });
});
