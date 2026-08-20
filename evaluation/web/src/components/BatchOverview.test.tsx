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

import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { BatchOverview } from "./BatchOverview";
import { apiStub, batchRecord, batchReport } from "../test/fixtures";

describe("BatchOverview", () => {
  it("labels a paused batch as paused", async () => {
    const api = apiStub({
      getBatch: vi.fn().mockResolvedValue(batchRecord({ status: "paused" })),
      getBatchReport: vi.fn().mockResolvedValue(batchReport),
    });

    render(<BatchOverview api={api} batchId="batch-001" navigate={() => undefined} />);

    expect(await screen.findByText("已暂停 · 100 / 100")).toBeVisible();
  });

  it("renders reconciled objective facts without authored conclusions", async () => {
    const api = apiStub({
      getBatch: vi.fn().mockResolvedValue(
        batchRecord({
          status: "completed",
          total_tasks: 100,
          started_at: "2026-07-29T01:01:00Z",
          finished_at: "2026-07-29T05:00:00Z",
          resolved_powercontext_sha: "a".repeat(40),
        }),
      ),
      getBatchReport: vi.fn().mockResolvedValue(batchReport),
    });
    render(<BatchOverview api={api} batchId="batch-001" navigate={() => undefined} />);

    expect(await screen.findByRole("heading", { name: "总体报告" })).toBeVisible();
    const summary = screen.getByLabelText("正确性汇总");
    expect(within(summary).getByText("100")).toBeVisible();
    expect(within(summary).getByText("41%")).toBeVisible();
    expect(within(summary).getByText("41 / 100 个任务")).toBeVisible();
    expect(within(summary).getByText("48%")).toBeVisible();
    expect(within(summary).getByText("48 / 100 个任务")).toBeVisible();
    expect(within(summary).getByText("+7 pp")).toBeVisible();

    expect(screen.getByRole("link", { name: /OFF 未通过.*ON 通过.*14/ })).toBeVisible();
    expect(screen.getByRole("link", { name: /OFF 通过.*ON 未通过.*7/ })).toBeVisible();
    expect(screen.getByText("可比较任务 100 / 100")).toBeVisible();
    expect(screen.getByText("72,400,000")).toBeVisible();
    expect(screen.getByText("79,800,000")).toBeVisible();
    expect(screen.getAllByText("100 / 100 个任务有记录")).toHaveLength(3);
    expect(screen.queryByText(/提升|退化|验收有效|验收无效|优先分析|建议/)).not.toBeInTheDocument();
    expect(screen.queryByText(/N\/A|补丁大小|生命周期|处理有效性/)).not.toBeInTheDocument();
  });

  it("opens the exact task filter from a pair category", async () => {
    const navigate = vi.fn();
    render(<BatchOverview api={apiStub()} batchId="batch-001" navigate={navigate} />);

    fireEvent.click(await screen.findByRole("link", { name: /OFF 通过.*ON 未通过.*7/ }));

    expect(navigate).toHaveBeenCalledWith(
      "/report/batch-001/tasks?category=off_pass_on_fail",
    );
  });
});
