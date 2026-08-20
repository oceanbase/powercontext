import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { EvaluationApi } from "../api";
import type { BatchStatus } from "../types";
import { ReportIndex } from "./ReportIndex";

function batch(
  batchId: string,
  model: string,
  pauseReason: string,
  createdAt = "2026-08-02T00:00:00Z",
  status: BatchStatus = "paused",
) {
  return {
    batch_id: batchId,
    request: {
      powercontext_ref: "latest",
      benchmark: "swebench-pro",
      task_set: "swebench-pro-public-v2",
      model,
      reasoning_effort: "medium",
      treatment_mode: "off_on",
      idempotency_key: `${batchId}-request`,
      usage_pause_percent: 80,
      initial_control_intent: "run",
    },
    total_tasks: 731,
    status,
    control: {
      intent: "pause",
      usage_pause_percent: 80,
      pause_reason: pauseReason,
      updated_at: "2026-08-03T00:00:00Z",
      version: 1,
    },
    created_at: createdAt,
    started_at: "2026-08-02T00:01:00Z",
    finished_at: null,
    resolved_powercontext_sha: "0123456789abcdef0123456789abcdef01234567",
  };
}

describe("ReportIndex", () => {
  it("shows the newest created batch first and only marks that batch as latest", async () => {
    const older = batch("batch-older", "gpt-5.6-sol", "user", "2026-08-01T00:00:00Z");
    const newer = batch("batch-newer", "gpt-5.6-luna", "user", "2026-08-03T00:00:00Z");
    const fetch = vi.fn<typeof globalThis.fetch>().mockResolvedValue(
      new Response(JSON.stringify([older, newer]), { headers: { "Content-Type": "application/json" } }),
    );

    render(<ReportIndex api={new EvaluationApi({ fetch })} navigate={vi.fn()} />);

    await screen.findByRole("link", { name: "查看 batch-newer 的总体报告" });
    const items = screen.getAllByRole("listitem");
    expect(within(items[0]!).getByRole("link", { name: "查看 batch-newer 的总体报告" })).toBeVisible();
    expect(within(items[0]!).getByText("最新批次")).toBeVisible();
    expect(within(items[1]!).getByRole("link", { name: "查看 batch-older 的总体报告" })).toBeVisible();
    expect(within(items[1]!).queryByText("最新批次")).not.toBeInTheDocument();
    expect(screen.getAllByText("最新批次")).toHaveLength(1);
  });

  it("uses descending batch ID to break equal creation-time ties", async () => {
    const lowerId = batch("batch-a", "gpt-5.6-sol", "user", "2026-08-03T00:00:00Z");
    const higherId = batch("batch-z", "gpt-5.6-luna", "user", "2026-08-03T00:00:00Z");
    const fetch = vi.fn<typeof globalThis.fetch>().mockResolvedValue(
      new Response(JSON.stringify([lowerId, higherId]), { headers: { "Content-Type": "application/json" } }),
    );

    render(<ReportIndex api={new EvaluationApi({ fetch })} navigate={vi.fn()} />);

    await screen.findByRole("link", { name: "查看 batch-z 的总体报告" });
    const items = screen.getAllByRole("listitem");
    expect(within(items[0]!).getByRole("link", { name: "查看 batch-z 的总体报告" })).toBeVisible();
    expect(within(items[0]!).getByText("最新批次")).toBeVisible();
    expect(within(items[1]!).getByRole("link", { name: "查看 batch-a 的总体报告" })).toBeVisible();
    expect(within(items[1]!).queryByText("最新批次")).not.toBeInTheDocument();
  });

  it("shows distinct paused and pausing status labels", async () => {
    const paused = batch("batch-paused", "gpt-5.6-sol", "user", "2026-08-03T00:00:00Z", "paused");
    const pausing = batch("batch-pausing", "gpt-5.6-luna", "user", "2026-08-02T00:00:00Z", "pausing");
    const fetch = vi.fn<typeof globalThis.fetch>().mockResolvedValue(
      new Response(JSON.stringify([paused, pausing]), { headers: { "Content-Type": "application/json" } }),
    );

    render(<ReportIndex api={new EvaluationApi({ fetch })} navigate={vi.fn()} />);

    const pausedLink = await screen.findByRole("link", { name: "查看 batch-paused 的总体报告" });
    const pausingLink = screen.getByRole("link", { name: "查看 batch-pausing 的总体报告" });
    expect(pausedLink.closest("li")).toHaveTextContent("已暂停");
    expect(pausingLink.closest("li")).toHaveTextContent("暂停中");
  });

  it("keeps all batches visible when Luna pauses for an infrastructure failure", async () => {
    const sol = batch("batch-sol", "gpt-5.6-sol", "user");
    const luna = batch("batch-luna", "gpt-5.6-luna", "infrastructure_failure");
    const fetch = vi.fn<typeof globalThis.fetch>().mockResolvedValue(
      new Response(JSON.stringify([sol, luna]), { headers: { "Content-Type": "application/json" } }),
    );

    render(<ReportIndex api={new EvaluationApi({ fetch })} navigate={vi.fn()} />);

    expect(await screen.findByText("batch-sol")).toBeVisible();
    expect(screen.getByText("batch-luna")).toBeVisible();
    expect(screen.queryByText("评测批次暂时无法加载。")).not.toBeInTheDocument();
  });
});
