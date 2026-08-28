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

import { describe, expect, it, vi } from "vitest";

import { ApiError, EvaluationApi } from "./api";
import { batchReport } from "./test/fixtures";

const validTask = {
  powercontext_ref: "latest",
  benchmark: "swebench-pro",
  instance_id: "instance_flipt-io__flipt-518ec324b66a07fdd95464a5e9ca5fe7681ad8f9",
  model: "gpt-5.6-sol",
  reasoning_effort: "medium",
  treatment_mode: "off_on",
  idempotency_key: "request-1",
} as const;

const queuedTask = {
  task_id: "task-1",
  attempt_id: "task-1.attempt-0001",
  attempt_number: 1,
  attempt_count: 1,
  retryable: false,
  request: validTask,
  status: "queued",
  phase: null,
  created_at: "2026-07-29T00:00:00Z",
  started_at: null,
  finished_at: null,
  version: 0,
  failure_category: null,
  failure_phase: null,
  failure_summary: null,
  result: null,
  queue_position: 1,
} as const;

function jsonResponse(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function apiWithResponse(response: Response): {
  api: EvaluationApi;
  fetch: ReturnType<typeof vi.fn>;
} {
  const fetch = vi.fn<typeof globalThis.fetch>().mockResolvedValue(response);
  return { api: new EvaluationApi({ fetch }), fetch };
}

describe("EvaluationApi HTTP", () => {
  it("accepts an API-key admission response without subscription usage", async () => {
    const response = { mode: "api_key", sufficient: true, usage: null } as const;
    const { api, fetch } = apiWithResponse(jsonResponse(response));

    await expect(api.getAccountUsage()).resolves.toEqual(response);
    expect(fetch).toHaveBeenCalledWith("/api/account-usage", expect.any(Object));
  });

  it("accepts the secret-free current batch runtime response", async () => {
    const response = {
      batch_id: "batch-live",
      generated_at: "2026-08-16T09:30:00Z",
      status_counts: {
        queued: 1,
        running: 1,
        succeeded: 22,
        failed: 0,
        interrupted: 0,
        cancelled: 0,
      },
      tasks: [
        {
          task_id: "task-live",
          attempt_id: "task-live.attempt-0002",
          instance_id: "instance_org__repo-live",
          source_index: 8,
          status: "running",
          phase: "running_off",
          attempt_number: 2,
          attempt_count: 2,
          created_at: "2026-08-16T09:20:00Z",
          eligible_at: "2026-08-16T09:25:00Z",
          started_at: "2026-08-16T09:26:00Z",
          last_failure: {
            category: "report_generation_failure",
            code: "report_generation",
            phase: "generating_report",
            summary: "Safe summary",
            finished_at: "2026-08-16T09:24:00Z",
          },
        },
      ],
    } as const;
    const { api, fetch } = apiWithResponse(jsonResponse(response));

    await expect(api.getBatchRuntime("batch/live")).resolves.toEqual(response);
    expect(fetch).toHaveBeenCalledWith("/api/batches/batch%2Flive/runtime", expect.any(Object));
  });

  it("accepts a running batch report before any task has reached a terminal state", async () => {
    const runningReport = {
      ...batchReport,
      terminal_tasks: 0,
      comparable_pairs: 0,
      off: { resolved: 0, total: 0, rate_percent: 0 },
      on: { resolved: 0, total: 0, rate_percent: 0 },
      resolution_rate_delta_points: 0,
      pair_categories: {
        off_fail_on_pass: 0,
        off_pass_on_fail: 0,
        both_pass: 0,
        both_fail: 0,
        execution_failure: 0,
      },
      task_statuses: {
        queued: 711,
        running: 20,
        succeeded: 0,
        failed: 0,
        interrupted: 0,
        cancelled: 0,
      },
    };
    const { api } = apiWithResponse(jsonResponse(runningReport));

    await expect(api.getBatchReport("batch-running")).resolves.toEqual(runningReport);
  });

  it("accepts batches paused by an infrastructure failure", async () => {
    const batch = {
      batch_id: "batch-luna",
      request: {
        powercontext_ref: "latest",
        benchmark: "swebench-pro",
        task_set: "swebench-pro-public-v2",
        model: "gpt-5.6-luna",
        reasoning_effort: "medium",
        treatment_mode: "off_on",
        idempotency_key: "batch-luna-request",
        usage_pause_percent: 80,
        initial_control_intent: "run",
      },
      total_tasks: 731,
      status: "paused",
      control: {
        intent: "pause",
        usage_pause_percent: 80,
        pause_reason: "infrastructure_failure",
        updated_at: "2026-08-03T00:00:00Z",
        version: 1,
      },
      created_at: "2026-08-02T00:00:00Z",
      started_at: "2026-08-02T00:01:00Z",
      finished_at: null,
      resolved_powercontext_sha: "0123456789abcdef0123456789abcdef01234567",
    };
    const { api } = apiWithResponse(jsonResponse([batch]));

    await expect(api.listBatches()).resolves.toEqual([batch]);
  });

  it("accepts the infrastructure failure event that accompanies a system pause", async () => {
    const event = {
      sequence: 1,
      batch_id: "batch-luna",
      event_type: "infrastructure_failure",
      actor: "system",
      details: {
        task_id: "task-1",
        attempt_id: "task-1.attempt-0001",
        failure_category: "worker_interruption",
      },
      occurred_at: "2026-08-03T00:00:00Z",
    } as const;
    const { api } = apiWithResponse(jsonResponse([event]));

    await expect(api.listBatchControlEvents("batch-luna")).resolves.toEqual([event]);
  });

  it("accepts batches paused by an exhausted upstream capacity retry budget", async () => {
    const batch = {
      batch_id: "batch-luna",
      request: {
        powercontext_ref: "latest",
        benchmark: "swebench-pro",
        task_set: "swebench-pro-public-v2",
        model: "gpt-5.6-luna",
        reasoning_effort: "medium",
        treatment_mode: "off_on",
        idempotency_key: "batch-luna-request",
        usage_pause_percent: 95,
        initial_control_intent: "run",
      },
      total_tasks: 731,
      status: "paused",
      control: {
        intent: "pause",
        usage_pause_percent: 95,
        pause_reason: "codex_capacity",
        updated_at: "2026-08-04T00:00:00Z",
        version: 31,
      },
      created_at: "2026-08-02T00:00:00Z",
      started_at: "2026-08-02T00:01:00Z",
      finished_at: null,
      resolved_powercontext_sha: "0123456789abcdef0123456789abcdef01234567",
    };
    const { api } = apiWithResponse(jsonResponse([batch]));

    await expect(api.listBatches()).resolves.toEqual([batch]);
  });

  it("creates and loads a complete batch through strict relative API routes", async () => {
    const request = {
      powercontext_ref: "latest",
      benchmark: "swebench-pro",
      task_set: "swebench-pro-public-v2",
      model: "gpt-5.6-luna",
      reasoning_effort: "medium",
      treatment_mode: "off_on",
      idempotency_key: "batch-request-1",
      usage_pause_percent: 80,
      initial_control_intent: "run",
    } as const;
    const batch = {
      batch_id: "batch/1",
      request,
      total_tasks: 731,
      status: "queued",
      control: {
        intent: "run",
        usage_pause_percent: 80,
        pause_reason: null,
        updated_at: "2026-07-29T00:00:00Z",
        version: 0,
      },
      created_at: "2026-07-29T00:00:00Z",
      started_at: null,
      finished_at: null,
      resolved_powercontext_sha: null,
    } as const;
    const fetch = vi
      .fn<typeof globalThis.fetch>()
      .mockResolvedValueOnce(jsonResponse(batch, 201))
      .mockResolvedValueOnce(jsonResponse([batch]))
      .mockResolvedValueOnce(jsonResponse(batch));
    const api = new EvaluationApi({ fetch });

    await expect(api.createBatch(request)).resolves.toEqual(batch);
    await expect(api.listBatches()).resolves.toEqual([batch]);
    await expect(api.getBatch("batch/1")).resolves.toEqual(batch);

    expect(fetch.mock.calls.map(([url]) => url)).toEqual([
      "/api/batches",
      "/api/batches",
      "/api/batches/batch%2F1",
    ]);
    expect(fetch.mock.calls[0]?.[1]).toEqual(
      expect.objectContaining({ method: "POST", body: JSON.stringify(request) }),
    );
  });

  it("loads capabilities, health, task summaries, and task detail from relative API URLs", async () => {
    const capabilities = {
      benchmarks: ["swebench-pro"],
      instances: [validTask.instance_id],
      models: ["gpt-5.6-sol"],
      reasoning_efforts: ["medium"],
      treatment_modes: ["off_on"],
    };
    const health = {
      service: "ok",
      worker_lease_active: false,
      queued_tasks: 1,
      running_tasks: 0,
      active_task_pairs: 3,
      task_parallelism: 4,
      resource_admission_open: true,
      filesystem_free_bytes: 200_000_000_000,
      filesystem_total_bytes: 400_000_000_000,
      filesystem_min_free_bytes: 20_000_000_000,
      filesystem_free_inodes: 20_000_000,
      filesystem_total_inodes: 40_000_000,
      filesystem_min_free_inodes: 1_000_000,
    };
    const summary = {
      task_id: "task-1",
      attempt_id: "task-1.attempt-0001",
      attempt_number: 1,
      attempt_count: 1,
      retryable: false,
      powercontext_ref: "latest",
      instance_id: validTask.instance_id,
      model: "gpt-5.6-sol",
      status: "queued",
      phase: null,
      created_at: "2026-07-29T00:00:00Z",
      started_at: null,
      finished_at: null,
      version: 0,
      off_resolved: null,
      on_resolved: null,
      queue_position: 1,
    };
    const fetch = vi
      .fn<typeof globalThis.fetch>()
      .mockResolvedValueOnce(jsonResponse(capabilities))
      .mockResolvedValueOnce(jsonResponse(health))
      .mockResolvedValueOnce(jsonResponse([summary]))
      .mockResolvedValueOnce(jsonResponse(queuedTask));
    const api = new EvaluationApi({ fetch });

    await expect(api.getCapabilities()).resolves.toEqual(capabilities);
    await expect(api.getHealth()).resolves.toEqual(health);
    await expect(api.listTasks({ status: "queued", order: "newest", limit: 25, offset: 0 })).resolves.toEqual([summary]);
    await expect(api.getTask("task-1")).resolves.toEqual(queuedTask);

    expect(fetch.mock.calls.map(([url]) => url)).toEqual([
      "/api/capabilities",
      "/api/health",
      "/api/tasks?status=queued&order=newest&limit=25&offset=0",
      "/api/tasks/task-1",
    ]);
  });

  it.each([201, 200])("accepts create status %i and returns the queued task", async (status) => {
    const { api, fetch } = apiWithResponse(jsonResponse(queuedTask, status));

    await expect(api.createTask(validTask)).resolves.toEqual(queuedTask);
    const [url, init] = fetch.mock.calls[0] ?? [];
    expect(url).toBe("/api/tasks");
    expect(init).toEqual(
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify(validTask),
      }),
    );
    const headers = new Headers(init?.headers);
    expect(headers.get("Accept")).toBe("application/json");
    expect(headers.get("Content-Type")).toBe("application/json");
  });

  it("cancels a task with a relative URL and forwards its abort signal", async () => {
    const cancelled = {
      ...queuedTask,
      status: "cancelled",
      finished_at: "2026-07-29T00:01:00Z",
      queue_position: null,
      version: 1,
    };
    const { api, fetch } = apiWithResponse(jsonResponse(cancelled));
    const controller = new AbortController();

    await expect(api.cancelTask("task-1", controller.signal)).resolves.toEqual(cancelled);
    expect(fetch).toHaveBeenCalledWith(
      "/api/tasks/task-1/cancel",
      expect.objectContaining({ method: "POST", signal: controller.signal }),
    );
  });

  it("loads the exact structured report contract", async () => {
    const evidence = {
      mcp_requests: 1,
      prompt_sources: 2,
      plugin_checkout_sha: "abc",
      plugin_id: "powercontext",
      plugin_installed: true,
      plugin_version: "0.1.0",
      scope_id: "scope",
      server_ready: true,
    };
    const report = {
      task_id: "task-1",
      acceptance_valid: true,
      treatment_mode: "off_on",
      off: {
        arm: "off",
        state: "treatment_validated",
        resolution: "unresolved",
        passed: false,
        treatment_valid: true,
        input_tokens: 10,
        output_tokens: 20,
        elapsed_seconds: 1.5,
        patch_bytes: 0,
      },
      on: {
        arm: "on",
        state: "treatment_validated",
        resolution: "resolved",
        passed: true,
        treatment_valid: true,
        input_tokens: 8,
        output_tokens: 12,
        elapsed_seconds: 1,
        patch_bytes: 100,
      },
      comparison: {
        input_tokens: { off: 10, on: 8, delta: -2, percent: -20 },
        output_tokens: { off: 20, on: 12, delta: -8, percent: -40 },
        elapsed_seconds: { off: 1.5, on: 1, delta: -0.5, percent: -33.333 },
        patch_bytes: { off: 0, on: 100, delta: 100, percent: null },
      },
      evidence: { off: evidence, on: evidence },
      revisions: { powercontext: "abc" },
      configuration: { model: "gpt-5.6-sol" },
      generated_at: "2026-07-29T00:02:00Z",
    };
    const { api } = apiWithResponse(jsonResponse(report));

    await expect(api.getReport("task-1")).resolves.toEqual(report);
  });

  it("loads raw report markdown only from a text response", async () => {
    const fetch = vi.fn<typeof globalThis.fetch>().mockResolvedValue(
      new Response("# Report", { headers: { "Content-Type": "text/plain; charset=utf-8" } }),
    );

    await expect(new EvaluationApi({ fetch }).getRawReport("task-1")).resolves.toBe("# Report");
    expect(fetch).toHaveBeenCalledWith(
      "/api/tasks/task-1/report.md",
      expect.objectContaining({ headers: { Accept: "text/plain" } }),
    );
  });

  it("turns the fixed JSON error envelope into ApiError", async () => {
    const { api } = apiWithResponse(
      jsonResponse({ error: { code: "task_not_found", message: "The requested task does not exist." } }, 404),
    );

    await expect(api.getTask("missing")).rejects.toEqual(
      expect.objectContaining<ApiError>({
        name: "ApiError",
        status: 404,
        code: "task_not_found",
        message: "The requested task does not exist.",
      }),
    );
  });

  it.each([
    new Response("<secret>upstream failed</secret>", {
      status: 502,
      headers: { "Content-Type": "text/html" },
    }),
    new Response("{broken", { status: 500, headers: { "Content-Type": "application/json" } }),
  ])("uses a safe generic message for non-JSON or malformed errors", async (response) => {
    const { api } = apiWithResponse(response);

    await expect(api.getHealth()).rejects.toMatchObject({
      name: "ApiError",
      code: "request_failed",
      message: "The evaluation service could not complete the request.",
    });
    await expect(api.getHealth()).rejects.not.toThrow(/secret|upstream|broken/i);
  });

  it("uses a safe generic abort error without leaking the thrown value", async () => {
    const fetch = vi.fn<typeof globalThis.fetch>().mockRejectedValue(new DOMException("private reason", "AbortError"));

    await expect(new EvaluationApi({ fetch }).getHealth()).rejects.toMatchObject({
      name: "ApiError",
      code: "request_aborted",
      message: "The evaluation request was cancelled.",
    });
  });

  it.each([
    {
      name: "nested task request",
      method: "getTask" as const,
      payload: { ...queuedTask, request: { ...validTask, model: "unsafe model" } },
    },
    {
      name: "task lifecycle mismatch",
      method: "getTask" as const,
      payload: { ...queuedTask, status: "succeeded", started_at: null, finished_at: null, result: null },
    },
    {
      name: "capability literal and extra field",
      method: "getCapabilities" as const,
      payload: {
        benchmarks: ["swebench-pro"],
        instances: [validTask.instance_id],
        models: ["unknown-model"],
        reasoning_efforts: ["medium"],
        treatment_modes: ["off_on"],
        secret: "must not be accepted",
      },
    },
    {
      name: "negative health count",
      method: "getHealth" as const,
      payload: {
        service: "ok",
        worker_lease_active: true,
        queued_tasks: -1,
        running_tasks: 0,
        active_task_pairs: 0,
        task_parallelism: 1,
      },
    },
    {
      name: "negative active task pairs",
      method: "getHealth" as const,
      payload: {
        service: "ok",
        worker_lease_active: true,
        queued_tasks: 0,
        running_tasks: 0,
        active_task_pairs: -1,
        task_parallelism: 1,
      },
    },
    {
      name: "zero task parallelism",
      method: "getHealth" as const,
      payload: {
        service: "ok",
        worker_lease_active: true,
        queued_tasks: 0,
        running_tasks: 0,
        active_task_pairs: 0,
        task_parallelism: 0,
      },
    },
    {
      name: "task parallelism above maximum",
      method: "getHealth" as const,
      payload: {
        service: "ok",
        worker_lease_active: true,
        queued_tasks: 0,
        running_tasks: 0,
        active_task_pairs: 0,
        task_parallelism: 11,
      },
    },
    {
      name: "unknown health field",
      method: "getHealth" as const,
      payload: {
        service: "ok",
        worker_lease_active: true,
        queued_tasks: 0,
        running_tasks: 0,
        active_task_pairs: 0,
        task_parallelism: 1,
        secret: "must not be accepted",
      },
    },
  ])("rejects malformed $name with a safe fixed error", async ({ method, payload }) => {
    const { api } = apiWithResponse(jsonResponse(payload));
    const operation =
      method === "getTask"
        ? api.getTask("task-1")
        : method === "getCapabilities"
          ? api.getCapabilities()
          : api.getHealth();

    await expect(operation).rejects.toMatchObject({
      name: "ApiError",
      code: "invalid_response",
      message: "The evaluation service could not complete the request.",
    });
    await expect(operation).rejects.not.toThrow(/unsafe model|secret/i);
  });

  it("rejects malformed nested report evidence", async () => {
    const malformedReport = {
      task_id: "task-1",
      acceptance_valid: true,
      off: {
        arm: "off",
        state: "treatment_validated",
        resolution: "unresolved",
        passed: false,
        treatment_valid: true,
        input_tokens: null,
        output_tokens: null,
        elapsed_seconds: null,
        patch_bytes: null,
      },
      on: {
        arm: "on",
        state: "treatment_validated",
        resolution: "resolved",
        passed: true,
        treatment_valid: true,
        input_tokens: 1,
        output_tokens: 1,
        elapsed_seconds: 1,
        patch_bytes: 1,
      },
      comparison: {
        input_tokens: null,
        output_tokens: null,
        elapsed_seconds: null,
        patch_bytes: null,
      },
      evidence: {
        off: {
          mcp_requests: 0,
          prompt_sources: 0,
          plugin_checkout_sha: "abc",
          plugin_id: "powercontext",
          plugin_installed: "yes",
          plugin_version: "0.1.0",
          scope_id: "scope",
          server_ready: true,
        },
        on: {
          mcp_requests: 0,
          prompt_sources: 0,
          plugin_checkout_sha: "abc",
          plugin_id: "powercontext",
          plugin_installed: true,
          plugin_version: "0.1.0",
          scope_id: "scope",
          server_ready: true,
        },
      },
      revisions: { powercontext: "abc" },
      configuration: { model: "gpt-5.6-sol" },
      generated_at: "2026-07-29T00:02:00Z",
    };
    const { api } = apiWithResponse(jsonResponse(malformedReport));

    await expect(api.getReport("task-1")).rejects.toMatchObject({
      code: "invalid_response",
      message: "The evaluation service could not complete the request.",
    });
  });
});

type Listener = (event: Event) => void;

class FakeEventSource {
  readonly url: string;
  readonly listeners = new Map<string, Set<Listener>>();
  close = vi.fn();

  constructor(url: string) {
    this.url = url;
  }

  addEventListener(type: string, listener: EventListenerOrEventListenerObject): void {
    const callback: Listener =
      typeof listener === "function" ? listener : (event) => listener.handleEvent(event);
    const listeners = this.listeners.get(type) ?? new Set<Listener>();
    listeners.add(callback);
    this.listeners.set(type, listeners);
  }

  removeEventListener(type: string, listener: EventListenerOrEventListenerObject): void {
    if (typeof listener === "function") {
      this.listeners.get(type)?.delete(listener);
    }
  }

  emit(type: string, data?: string): void {
    const event = data === undefined ? new Event(type) : new MessageEvent(type, { data });
    for (const listener of this.listeners.get(type) ?? []) listener(event);
  }
}

describe("EvaluationApi task events", () => {
  it("parses task events, ignores heartbeat, and closes on terminal state", () => {
    let source: FakeEventSource | undefined;
    const events = vi.fn();
    const errors = vi.fn();
    const api = new EvaluationApi({
      eventSourceFactory: (url) => {
        source = new FakeEventSource(url);
        return source;
      },
    });
    const subscription = api.subscribeTaskEvents("task-1", events, errors);

    expect(source?.url).toBe("/api/tasks/task-1/events");
    source?.emit("heartbeat");
    expect(events).not.toHaveBeenCalled();
    source?.emit(
      "task",
      JSON.stringify({
        task_id: "task-1",
        status: "running",
        phase: "running_off",
        version: 1,
        occurred_at: "2026-07-29T00:01:00Z",
      }),
    );
    source?.emit(
      "task",
      JSON.stringify({
        task_id: "task-1",
        status: "succeeded",
        phase: "generating_report",
        version: 2,
        occurred_at: "2026-07-29T00:02:00Z",
      }),
    );
    source?.emit("error");

    expect(events).toHaveBeenCalledTimes(2);
    expect(source?.close).toHaveBeenCalledTimes(1);
    expect(errors).not.toHaveBeenCalled();
    subscription.close();
    expect(source?.close).toHaveBeenCalledTimes(1);
  });

  it("reports native reconnect for nonterminal errors and registers no duplicate listeners", () => {
    let source: FakeEventSource | undefined;
    const errors = vi.fn();
    const api = new EvaluationApi({
      eventSourceFactory: (url) => {
        source = new FakeEventSource(url);
        return source;
      },
    });

    const subscription = api.subscribeTaskEvents("task-1", vi.fn(), errors);
    source?.emit("error");
    source?.emit("error");

    expect(errors).toHaveBeenCalledTimes(2);
    expect(errors).toHaveBeenLastCalledWith({
      code: "event_stream_disconnected",
      message: "Live updates were interrupted. Reconnecting automatically.",
      reconnecting: true,
    });
    expect(source?.listeners.get("task")?.size).toBe(1);
    expect(source?.listeners.get("error")?.size).toBe(1);
    expect(source?.close).not.toHaveBeenCalled();
    subscription.close();
    subscription.close();
    expect(source?.close).toHaveBeenCalledTimes(1);
  });

  it("rejects malformed nested events without exposing their payload", () => {
    let source: FakeEventSource | undefined;
    const events = vi.fn();
    const errors = vi.fn();
    const api = new EvaluationApi({
      eventSourceFactory: (url) => {
        source = new FakeEventSource(url);
        return source;
      },
    });

    api.subscribeTaskEvents("task-1", events, errors);
    source?.emit(
      "task",
      JSON.stringify({
        task_id: "task-1",
        status: "running",
        phase: "private-invalid-phase",
        version: 1,
        occurred_at: "not-a-timestamp",
      }),
    );

    expect(events).not.toHaveBeenCalled();
    expect(errors).toHaveBeenCalledWith({
      code: "invalid_event",
      message: "A live update could not be read safely.",
      reconnecting: true,
    });
    expect(JSON.stringify(errors.mock.calls)).not.toMatch(/private-invalid-phase/);
  });
});
