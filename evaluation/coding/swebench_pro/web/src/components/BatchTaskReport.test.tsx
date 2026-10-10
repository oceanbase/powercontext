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

import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { BatchTaskReport } from "./BatchTaskReport";
import { apiStub, batchTask, batchTaskPage } from "../test/fixtures";

describe("BatchTaskReport", () => {
  it("loads the URL-selected category and renders objective OFF/ON rows", async () => {
    const listBatchTasks = vi.fn().mockResolvedValue({
      ...batchTaskPage,
      items: [
        batchTask(),
        batchTask({
          task_id: "task-positive",
          instance_id: "instance_owner__repo-positive",
          pair_category: "off_fail_on_pass",
          off: { resolved: false, input_tokens: 80, output_tokens: 8, total_tokens: 88 },
          on: { resolved: true, input_tokens: 70, output_tokens: 7, total_tokens: 77 },
          tokens: { off: 88, on: 77, delta: -11 },
        }),
      ],
      total: 2,
    });
    render(
      <BatchTaskReport
        api={apiStub({ listBatchTasks })}
        batchId="batch-001"
        search="?category=off_pass_on_fail&sort=token_delta_desc"
        navigate={() => undefined}
      />,
    );

    expect(await screen.findByRole("heading", { name: "任务详细报告" })).toBeVisible();
    expect(listBatchTasks).toHaveBeenCalledWith(
      "batch-001",
      {
        category: "off_pass_on_fail",
        sort: "token_delta_desc",
        limit: 100,
        offset: 0,
      },
      expect.any(AbortSignal),
    );
    const negativeRow = screen.getByText("task-001").closest("tr");
    expect(negativeRow).not.toBeNull();
    expect(within(negativeRow!).getByText("通过")).toBeVisible();
    expect(within(negativeRow!).getByText("未通过")).toBeVisible();
    expect(within(negativeRow!).getByText("+25")).toBeVisible();
    expect(within(negativeRow!).getByRole("link", { name: "查看 task-001" })).toHaveAttribute(
      "href",
      "/report/batch-001/tasks/task-001?category=off_pass_on_fail&sort=token_delta_desc",
    );
    expect(screen.queryByText(/N\/A|补丁大小|生命周期|处理有效性|验收/)).not.toBeInTheDocument();
  });

  it("updates category, search, and sort in the report URL", async () => {
    const navigate = vi.fn();
    render(
      <BatchTaskReport
        api={apiStub()}
        batchId="batch-001"
        search=""
        navigate={navigate}
      />,
    );
    await screen.findByText("task-001");

    fireEvent.click(screen.getByRole("button", { name: "OFF 通过 / ON 未通过" }));
    expect(navigate).toHaveBeenLastCalledWith("/report/batch-001/tasks?category=off_pass_on_fail");

    fireEvent.change(screen.getByLabelText("搜索仓库或任务 ID"), { target: { value: "owner/repo" } });
    await waitFor(() =>
      expect(navigate).toHaveBeenLastCalledWith("/report/batch-001/tasks?q=owner%2Frepo"),
    );

    fireEvent.change(screen.getByLabelText("Token 变化排序"), { target: { value: "token_delta_desc" } });
    expect(navigate).toHaveBeenLastCalledWith("/report/batch-001/tasks?sort=token_delta_desc");
  });

  it("labels execution failures separately from ordinary unresolved results", async () => {
    const failed = batchTask({
      task_id: "task-error",
      status: "failed",
      pair_category: "execution_failure",
      off: null,
      on: null,
      tokens: { off: null, on: null, delta: null },
      failure_category: "codex_execution_failure",
      failure_summary: "Codex 执行失败",
    });
    render(
      <BatchTaskReport
        api={apiStub({
          listBatchTasks: vi.fn().mockResolvedValue({ ...batchTaskPage, items: [failed] }),
        })}
        batchId="batch-001"
        search=""
        navigate={() => undefined}
      />,
    );

    const row = (await screen.findByText("task-error")).closest("tr");
    expect(row).not.toBeNull();
    expect(within(row!).getByText("评测执行失败")).toBeVisible();
    expect(within(row!).queryByText("未通过")).not.toBeInTheDocument();
  });
});
