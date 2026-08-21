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

import type { ReportResponse } from "../types";
import { apiStub, deferred, report } from "../test/fixtures";
import { ReportView } from "./ReportView";

describe("ReportView", () => {
  it("renders the valid report hierarchy, comparisons, evidence, and sorted reproducibility data", async () => {
    render(<ReportView api={apiStub()} taskId="task/report" />);

    expect(await screen.findByText("验收有效")).toBeVisible();
    expect(screen.getByRole("heading", { name: "task-report" })).toBeVisible();
    expect(screen.getByText("OFF · RESOLVED")).toBeVisible();
    expect(screen.getByText("ON · RESOLVED")).toBeVisible();
    expect(screen.getAllByText("TREATMENT VALIDATED")).toHaveLength(2);
    expect(screen.getAllByText("PASS")).toHaveLength(2);
    expect(screen.getAllByText("VALID")).toHaveLength(2);
    expect(screen.getByText("−841,014")).toBeVisible();
    expect(screen.getByText("−42.8%")).toBeVisible();
    expect(screen.getByText("+1,024")).toBeVisible();
    expect(screen.getByText("+100.0%")).toBeVisible();
    expect(screen.getAllByText("N/A").length).toBeGreaterThan(0);
    expect(screen.getByText("10")).toBeVisible();
    expect(screen.getByText("2")).toBeVisible();
    expect(screen.getAllByText("已安装").length).toBe(2);
    expect(screen.getAllByText("已就绪").length).toBe(2);
    expect(screen.getByText("eval:run-123:on")).toBeVisible();

    const metadata = screen.getByRole("region", { name: "复现信息" });
    const terms = within(metadata).getAllByRole("term").map((node) => node.textContent);
    expect(terms).toEqual(["codex", "powercontext", "model", "reasoning_effort", "run_id", "生成时间"]);
    expect(screen.getByRole("link", { name: "查看原始 Markdown" })).toHaveAttribute(
      "href",
      "/api/tasks/task%2Freport/report.md",
    );
    expect(screen.getByRole("link", { name: "查看原始 Markdown" })).toHaveAttribute("target", "_blank");
    expect(screen.getByRole("link", { name: "查看原始 Markdown" })).toHaveAttribute("rel", "noopener noreferrer");
  });

  it("does not claim success or show unavailable comparisons for an invalid report", async () => {
    const invalid: ReportResponse = {
      ...report,
      acceptance_valid: false,
      off: {
        ...report.off,
        state: "treatment_validated",
        resolution: "unresolved",
        passed: false,
        treatment_valid: true,
      },
      comparison: { input_tokens: null, output_tokens: null, elapsed_seconds: null, patch_bytes: null },
    };
    render(<ReportView api={apiStub({ getReport: vi.fn().mockResolvedValue(invalid) })} taskId="invalid" />);

    expect(await screen.findByText("验收无效")).toBeVisible();
    expect(screen.getByText("OFF · UNRESOLVED")).toBeVisible();
    expect(screen.getByText("ON · RESOLVED")).toBeVisible();
    expect(screen.getAllByText("TREATMENT VALIDATED")).toHaveLength(2);
    expect(screen.getByText("FAIL")).toBeVisible();
    expect(screen.getByText("PASS")).toBeVisible();
    expect(screen.getAllByText("VALID")).toHaveLength(2);
    expect(screen.getByText("当前报告不具备有效的 OFF / ON 对照数据。")).toBeVisible();
    expect(screen.queryByText("验收有效")).not.toBeInTheDocument();
    expect(screen.queryByText("通过")).not.toBeInTheDocument();
    expect(screen.queryByText("−841,014")).not.toBeInTheDocument();
    expect(screen.queryByText(/生命周期未确认|官方结果未确认|处理证据未确认/)).not.toBeInTheDocument();
  });

  it("renders the nullable Gold validation audit when present", async () => {
    const audited: ReportResponse = {
      ...report,
      gold_validation: {
        instance_id: "instance_gravitational__teleport-c335534e02de143508ebebc7341021d7f8656e8f",
        mode: "verified_override",
        dataset_patch_sha256: "a".repeat(64),
        validation_patch_sha256: "b".repeat(64),
        dataset_patch_status: "known_failed",
        reference_validation_status: "passed",
        attempt_gold_validation_status: "passed",
        source_dataset: "livesweagent/claude-sonnet-4-5_swebench_pro_traj",
        source_revision: "c".repeat(40),
        source_file_oid: "d".repeat(40),
        source_kind: "verified_reference_submission",
      },
    };
    render(<ReportView api={apiStub({ getReport: vi.fn().mockResolvedValue(audited) })} taskId="audited" />);
    expect(await screen.findByText("Gold 校验审计")).toBeVisible();
    expect(screen.getByText("verified_override")).toBeVisible();
    expect(screen.getAllByText("passed")).toHaveLength(2);
  });

  it("shows safe loading, API error, and retry states", async () => {
    const first = deferred<ReportResponse>();
    const getReport = vi.fn().mockReturnValueOnce(first.promise).mockResolvedValueOnce(report);
    render(<ReportView api={apiStub({ getReport })} taskId="retry" />);
    expect(screen.getByText("正在加载验收报告…")).toBeVisible();

    first.reject(new Error("unsafe backend detail"));
    expect(await screen.findByText("验收报告暂时无法加载。")).toBeVisible();
    expect(screen.queryByText("unsafe backend detail")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    expect(await screen.findByText("验收有效")).toBeVisible();
    expect(getReport).toHaveBeenCalledTimes(2);
  });

  it("ignores stale responses after a task change and aborts on unmount", async () => {
    const stale = deferred<ReportResponse>();
    const fresh = deferred<ReportResponse>();
    const getReport = vi.fn().mockReturnValueOnce(stale.promise).mockReturnValueOnce(fresh.promise);
    const { rerender, unmount } = render(<ReportView api={apiStub({ getReport })} taskId="A" />);
    rerender(<ReportView api={apiStub({ getReport })} taskId="B" />);
    fresh.resolve({ ...report, task_id: "task-B" });
    expect(await screen.findByRole("heading", { name: "task-B" })).toBeVisible();
    stale.resolve({ ...report, task_id: "task-A" });
    await waitFor(() => expect(screen.queryByRole("heading", { name: "task-A" })).not.toBeInTheDocument());
    expect(getReport.mock.calls[0]?.[1]).toBeInstanceOf(AbortSignal);
    expect(getReport.mock.calls[0]?.[1].aborted).toBe(true);
    const activeSignal = getReport.mock.calls[1]?.[1] as AbortSignal;
    unmount();
    expect(activeSignal.aborted).toBe(true);
  });

  it("renders hostile strings as text and never inserts HTML", async () => {
    const hostile = "<img src=x onerror=alert(1)><script>window.pwned=true</script>";
    const unsafe: ReportResponse = {
      ...report,
      task_id: hostile,
      revisions: { hostile },
      configuration: { hostile },
    };
    const { container } = render(
      <ReportView api={apiStub({ getReport: vi.fn().mockResolvedValue(unsafe) })} taskId="unsafe" />,
    );
    expect(await screen.findByRole("heading", { name: hostile })).toBeVisible();
    expect(screen.getAllByText(hostile)).toHaveLength(3);
    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("img")).toBeNull();
  });
});
