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

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { apiStub } from "../test/fixtures";
import { BatchRuntime } from "./BatchRuntime";

describe("BatchRuntime", () => {
  it("shows running and retry-waiting task details and links to the task page", async () => {
    const navigate = vi.fn();
    const api = apiStub({
      getBatchRuntime: vi.fn().mockResolvedValue({
        batch_id: "batch-live",
        generated_at: "2026-08-16T09:30:00Z",
        status_counts: {
          queued: 3,
          running: 1,
          succeeded: 20,
          failed: 0,
          interrupted: 0,
          cancelled: 0,
        },
        tasks: [
          {
            task_id: "task-running",
            attempt_id: "task-running.attempt-0001",
            instance_id: "instance_org__repo-running",
            source_index: 7,
            status: "running",
            phase: "running_on",
            attempt_number: 1,
            attempt_count: 1,
            created_at: "2026-08-16T09:00:00Z",
            eligible_at: "2026-08-16T09:00:00Z",
            started_at: "2026-08-16T09:10:00Z",
            last_failure: null,
          },
          {
            task_id: "task-retry",
            attempt_id: "task-retry.attempt-0003",
            instance_id: "instance_org__repo-retry",
            source_index: 9,
            status: "queued",
            phase: null,
            attempt_number: 3,
            attempt_count: 3,
            created_at: "2026-08-16T09:00:00Z",
            eligible_at: "2026-08-16T09:40:00Z",
            started_at: null,
            last_failure: {
              category: "report_generation_failure",
              code: "report_generation",
              phase: "generating_report",
              summary: "Report assembly failed safely",
              finished_at: "2026-08-16T09:30:00Z",
            },
          },
        ],
      }),
    });

    render(<BatchRuntime api={api} batchId="batch-live" navigate={navigate} />);

    expect(await screen.findByRole("heading", { name: "当前运行任务" })).toBeVisible();
    expect(screen.getByText("source7")).toBeVisible();
    expect(screen.getByText("ON 执行")).toBeVisible();
    expect(screen.getByText("source9")).toBeVisible();
    expect(screen.getAllByText("等待重试").length).toBeGreaterThan(0);
    expect(screen.getByText("Report assembly failed safely")).toBeVisible();
    expect(screen.getByText("普通排队").nextSibling).toHaveTextContent("2");

    const back = screen.getByRole("link", { name: "← 返回总体报告" });
    expect(back).toHaveAttribute("href", "/report/batch-live");
    fireEvent.click(back);
    expect(navigate).toHaveBeenCalledWith("/report/batch-live");
    navigate.mockClear();

    fireEvent.click(screen.getAllByRole("link", { name: "查看详情" })[0]!);
    expect(navigate).toHaveBeenCalledWith("/report/batch-live/tasks/task-running");
  });

  it("shows a stable empty state when no task is running or waiting to retry", async () => {
    render(<BatchRuntime api={apiStub()} batchId="batch-001" navigate={() => undefined} />);

    expect(await screen.findByText("当前没有运行中或等待重试的任务。")).toBeVisible();
  });
});
