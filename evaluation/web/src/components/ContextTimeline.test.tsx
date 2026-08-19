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

import { ContextTimeline } from "./ContextTimeline";
import { apiStub } from "../test/fixtures";
import type { ContextEvent } from "../types";

function event(overrides: Partial<ContextEvent> = {}): ContextEvent {
  return {
    sequence: 1,
    observed_at: "2026-07-29T08:10:11.100000Z",
    elapsed_ms: 0,
    arm: "on",
    actor: "benchmark",
    event_type: "benchmark_prompt",
    input: { prompt: "fix the complete task" },
    output: null,
    source_artifact: "instance.jsonl",
    source_sequence: 0,
    ...overrides,
  };
}

describe("ContextTimeline", () => {
  it("loads every page in sequence order and exposes exact injection content safely", async () => {
    const injection = event({
      sequence: 2,
      observed_at: "2026-07-29T08:10:11.200000Z",
      elapsed_ms: 100,
      actor: "powercontext",
      event_type: "powercontext_injection",
      input: {
        query: "fix context",
        scope_id: "eval:batch:task:on",
        session_id: "session-1",
        turn_id: "turn-2",
      },
      output: {
        hits: [{ citation: "memory://decision/7", text: "exact context", score: 0.91 }],
        injected_text: "PowerContext recalled <img src=x onerror=alert(1)> exact context.",
      },
      source_artifact: "context/powercontext-injections.jsonl",
      source_sequence: 1,
    });
    const official = event({
      sequence: 3,
      observed_at: "2026-07-29T08:12:00.000000Z",
      elapsed_ms: 108_900,
      actor: "official_evaluator",
      event_type: "official_evaluation",
      input: { instance_id: "instance_owner__repo" },
      output: { resolved: false },
      source_artifact: "official",
      source_sequence: 1,
    });
    const listContextEvents = vi
      .fn()
      .mockResolvedValueOnce({ items: [event(), injection], total: 3, limit: 200, offset: 0 })
      .mockResolvedValueOnce({ items: [official], total: 3, limit: 200, offset: 2 });
    render(
      <ContextTimeline
        api={apiStub({ listContextEvents })}
        batchId="batch-001"
        taskId="task-001"
      />,
    );

    expect(await screen.findByRole("button", { name: /#1.*benchmark_prompt/ })).toBeVisible();
    expect(screen.getByRole("button", { name: /#2.*PowerContext 注入/ })).toBeVisible();
    expect(screen.getByRole("button", { name: /#3.*official_evaluation/ })).toBeVisible();
    expect(listContextEvents).toHaveBeenNthCalledWith(
      1,
      "batch-001",
      "task-001",
      "on",
      { limit: 200, offset: 0 },
      expect.any(AbortSignal),
    );
    expect(listContextEvents).toHaveBeenNthCalledWith(
      2,
      "batch-001",
      "task-001",
      "on",
      { limit: 200, offset: 2 },
      expect.any(AbortSignal),
    );

    fireEvent.click(screen.getByRole("button", { name: /#2.*PowerContext 注入/ }));
    const detail = screen.getByLabelText("事件详情");
    expect(within(detail).getByText("2026-07-29T08:10:11.200000Z")).toBeVisible();
    expect(within(detail).getByText(/\+100 ms · powercontext/)).toBeVisible();
    expect(within(detail).getByText("fix context")).toBeVisible();
    expect(within(detail).getByText("turn-2")).toBeVisible();
    expect(within(detail).getByText(/memory:\/\/decision\/7/)).toBeVisible();
    expect(within(detail).getAllByText(/exact context/).length).toBeGreaterThan(0);
    expect(within(detail).getByText(/PowerContext recalled <img src=x onerror=alert\(1\)>/)).toBeVisible();
    expect(detail.querySelector("img")).toBeNull();
    expect(screen.queryByText(/N\/A/)).not.toBeInTheDocument();
  });

  it("switches between ON and OFF without changing the selected task", async () => {
    const listContextEvents = vi.fn().mockResolvedValue({ items: [event()], total: 1, limit: 200, offset: 0 });
    render(
      <ContextTimeline
        api={apiStub({ listContextEvents })}
        batchId="batch-001"
        taskId="task-001"
      />,
    );
    await screen.findByRole("button", { name: /#1/ });

    fireEvent.click(screen.getByRole("button", { name: "OFF 时间线" }));

    await waitFor(() =>
      expect(listContextEvents).toHaveBeenLastCalledWith(
        "batch-001",
        "task-001",
        "off",
        { limit: 200, offset: 0 },
        expect.any(AbortSignal),
      ),
    );
    expect(screen.getByText(/task-001 · 按实际观察时间排序/)).toBeVisible();
  });
});
