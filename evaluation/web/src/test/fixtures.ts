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

import { vi } from "vitest";

import type { EvaluationApi } from "../api";
import type {
  BatchCreate,
  BatchRecord,
  BatchReport,
  BatchTaskDetail,
  BatchTaskItem,
  BatchTaskPage,
  Capabilities,
  ContextEventPage,
  HealthResponse,
  ReportResponse,
  TaskCreate,
  TaskRecord,
  TaskSummary,
} from "../types";

export function deferred<T>(): {
  promise: Promise<T>;
  resolve(value: T): void;
  reject(reason?: unknown): void;
} {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((onResolve, onReject) => {
    resolve = onResolve;
    reject = onReject;
  });
  return { promise, resolve, reject };
}

export const instanceId =
  "instance_flipt-io__flipt-518ec324b66a07fdd95464a5e9ca5fe7681ad8f9" as const;

export const capabilities: Capabilities = {
  benchmarks: ["swebench-pro"],
  instances: [instanceId],
  models: ["gpt-5.6-sol"],
  reasoning_efforts: ["medium"],
  treatment_modes: ["off_on"],
};

export const health: HealthResponse = {
  service: "ok",
  worker_lease_active: true,
  queued_tasks: 1,
  running_tasks: 1,
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

export const report: ReportResponse = {
  task_id: "task-report",
  acceptance_valid: true,
  off: {
    arm: "off",
    state: "treatment_validated",
    resolution: "resolved",
    passed: true,
    treatment_valid: true,
    input_tokens: 1_963_221,
    output_tokens: null,
    elapsed_seconds: 125.55,
    patch_bytes: 1_024,
  },
  on: {
    arm: "on",
    state: "treatment_validated",
    resolution: "resolved",
    passed: true,
    treatment_valid: true,
    input_tokens: 1_122_207,
    output_tokens: 12_345,
    elapsed_seconds: 100,
    patch_bytes: 2_048,
  },
  comparison: {
    input_tokens: { off: 1_963_221, on: 1_122_207, delta: -841_014, percent: -42.839 },
    output_tokens: null,
    elapsed_seconds: { off: 125.55, on: 100, delta: -25.55, percent: -20.3505 },
    patch_bytes: { off: 1_024, on: 2_048, delta: 1_024, percent: 100 },
  },
  evidence: {
    off: {
      mcp_requests: 0,
      prompt_sources: 0,
      plugin_checkout_sha: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      plugin_id: "powercontext",
      plugin_installed: true,
      plugin_version: "0.1.0",
      scope_id: "eval:run-123:off",
      server_ready: true,
    },
    on: {
      mcp_requests: 10,
      prompt_sources: 2,
      plugin_checkout_sha: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      plugin_id: "powercontext",
      plugin_installed: true,
      plugin_version: "0.1.0",
      scope_id: "eval:run-123:on",
      server_ready: true,
    },
  },
  revisions: {
    powercontext: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    codex: "gpt-5.6-sol",
  },
  configuration: {
    model: "gpt-5.6-sol",
    reasoning_effort: "medium",
    run_id: "run-123",
  },
  generated_at: "2026-07-29T01:02:03Z",
};

export const request: TaskCreate = {
  powercontext_ref: "latest",
  benchmark: "swebench-pro",
  instance_id: instanceId,
  model: "gpt-5.6-sol",
  reasoning_effort: "medium",
  treatment_mode: "off_on",
  idempotency_key: "fixture-key",
};

export const batchRequest: BatchCreate = {
  powercontext_ref: "latest",
  benchmark: "swebench-pro",
  task_set: "swebench-pro-public-v2",
  model: "gpt-5.6-sol",
  reasoning_effort: "medium",
  treatment_mode: "off_on",
  idempotency_key: "fixture-batch-key",
  usage_pause_percent: 80,
  initial_control_intent: "run",
};

export const usageSnapshot = {
  limit_id: "codex" as const,
  used_percent: 32,
  remaining_percent: 68,
  window_duration_minutes: 10_080,
  resets_at: "2026-08-05T01:00:00Z",
  observed_at: "2026-07-29T01:00:00Z",
  rate_limit_reached_type: null,
  plan_type: "pro",
  account_tokens: 1234,
  probe_version: 1 as const,
};

export const batchControl = {
  intent: "run" as const,
  usage_pause_percent: 80,
  pause_reason: null,
  updated_at: "2026-07-29T01:00:00Z",
  version: 0,
};

export const batchEstimate = {
  quality: "measured" as const,
  basis: "current_batch" as const,
  sample_size: 100,
  remaining_tasks: 0,
  remaining_tokens: 0,
  remaining_duration_seconds: 0,
  low_tokens: 0,
  high_tokens: 0,
  low_duration_seconds: 0,
  high_duration_seconds: 0,
};

export function batchRecord(overrides: Partial<BatchRecord> = {}): BatchRecord {
  return {
    batch_id: "batch-001",
    request: batchRequest,
    total_tasks: 731,
    status: "queued",
    control: batchControl,
    created_at: "2026-07-29T01:00:00Z",
    started_at: null,
    finished_at: null,
    resolved_powercontext_sha: null,
    ...overrides,
  };
}

export const batchReport: BatchReport = {
  batch_id: "batch-001",
  report_revision: 10_100,
  total_tasks: 100,
  terminal_tasks: 100,
  comparable_pairs: 100,
  execution_failures: 0,
  cancelled_tasks: 0,
  off: { resolved: 41, total: 100, rate_percent: 41 },
  on: { resolved: 48, total: 100, rate_percent: 48 },
  resolution_rate_delta_points: 7,
  pair_categories: {
    off_fail_on_pass: 14,
    off_pass_on_fail: 7,
    both_pass: 34,
    both_fail: 45,
    execution_failure: 0,
  },
  task_statuses: {
    queued: 0,
    running: 0,
    succeeded: 100,
    failed: 0,
    interrupted: 0,
    cancelled: 0,
  },
  tokens: {
    input: { off: 72_400_000, on: 79_800_000, delta: 7_400_000, off_measured_tasks: 100, on_measured_tasks: 100 },
    output: { off: 612_000, on: 668_000, delta: 56_000, off_measured_tasks: 100, on_measured_tasks: 100 },
    total: { off: 73_012_000, on: 80_468_000, delta: 7_456_000, off_measured_tasks: 100, on_measured_tasks: 100 },
  },
  control: batchControl,
  latest_usage: usageSnapshot,
  estimate: batchEstimate,
  revisions: { powercontext: "a".repeat(40), dataset: "public-v2", harness: "harness-sha" },
  configuration: { model: "gpt-5.6-sol", reasoning_effort: "medium", task_set: "swebench-pro-public-v2" },
};

export function batchTask(overrides: Partial<BatchTaskItem> = {}): BatchTaskItem {
  return {
    task_id: "task-001",
    attempt_id: "task-001.attempt-0001",
    attempt_number: 1,
    attempt_count: 1,
    retryable: false,
    model: "gpt-5.6-sol",
    reasoning_effort: "medium",
    instance_id: "instance_owner__repo-001",
    repository: "owner/repo",
    source_index: 0,
    status: "succeeded",
    pair_category: "off_pass_on_fail",
    off: { resolved: true, input_tokens: 100, output_tokens: 10, total_tokens: 110 },
    on: { resolved: false, input_tokens: 120, output_tokens: 15, total_tokens: 135 },
    tokens: { off: 110, on: 135, delta: 25 },
    failure_category: null,
    failure_summary: null,
    ...overrides,
  };
}

export const batchTaskPage: BatchTaskPage = {
  items: [batchTask()],
  total: 1,
  limit: 100,
  offset: 0,
};

export const batchTaskDetail: BatchTaskDetail = {
  task: batchTask(),
  problem_statement: "完整的问题描述",
  required_tests: {
    fail_to_pass: ["test_issue"],
    pass_to_pass: ["test_regression"],
    selected_test_files_to_run: "tests/test_feature.py",
    test_patch: "diff --git a/tests/test_feature.py b/tests/test_feature.py",
  },
  off: {
    resolved: true,
    patch_applied: true,
    fail_to_pass: { passed: 1, total: 1, failed: [] },
    pass_to_pass: { passed: 1, total: 1, failed: [] },
    log_excerpt: null,
    input_tokens: 100,
    output_tokens: 10,
    total_tokens: 110,
  },
  on: {
    resolved: false,
    patch_applied: true,
    fail_to_pass: { passed: 0, total: 1, failed: ["test_issue"] },
    pass_to_pass: { passed: 1, total: 1, failed: [] },
    log_excerpt: "test_issue failed",
    input_tokens: 120,
    output_tokens: 15,
    total_tokens: 135,
  },
  tokensflow_finalization: { off: null, on: null },
};

export const contextEventPage: ContextEventPage = {
  items: [],
  total: 0,
  limit: 100,
  offset: 0,
};

export function summary(
  status: TaskSummary["status"],
  taskId = `task-${status}`,
  overrides: Partial<TaskSummary> = {},
): TaskSummary {
  return {
    task_id: taskId,
    attempt_id: `${taskId}.attempt-0001`,
    attempt_number: 1,
    attempt_count: 1,
    retryable: status === "failed" || status === "interrupted",
    powercontext_ref: "latest",
    instance_id: instanceId,
    model: "gpt-5.6-sol",
    status,
    phase: status === "running" ? "running_off" : null,
    created_at: "2026-07-29T01:00:00Z",
    started_at: status === "queued" || status === "cancelled" ? null : "2026-07-29T01:01:00Z",
    finished_at: ["succeeded", "failed", "interrupted", "cancelled"].includes(status)
      ? "2026-07-29T01:02:00Z"
      : null,
    version: 1,
    off_resolved: status === "succeeded" ? false : null,
    on_resolved: status === "succeeded" ? true : null,
    queue_position: status === "queued" ? 2 : null,
    ...overrides,
  };
}

export function record(status: TaskRecord["status"], taskId = `task-${status}`): TaskRecord {
  const base = {
    task_id: taskId,
    attempt_id: `${taskId}.attempt-0001`,
    attempt_number: 1,
    attempt_count: 1,
    retryable: status === "failed" || status === "interrupted",
    request,
    created_at: "2026-07-29T01:00:00Z",
    version: 1,
  };
  if (status === "queued") {
    return {
      ...base,
      status,
      phase: null,
      started_at: null,
      finished_at: null,
      failure_category: null,
      failure_phase: null,
      failure_summary: null,
      result: null,
      queue_position: 2,
    };
  }
  if (status === "running") {
    return {
      ...base,
      status,
      phase: "running_off",
      started_at: "2026-07-29T01:01:00Z",
      finished_at: null,
      failure_category: null,
      failure_phase: null,
      failure_summary: null,
      result: null,
      queue_position: null,
    };
  }
  if (status === "succeeded") {
    return {
      ...base,
      status,
      phase: "generating_report",
      started_at: "2026-07-29T01:01:00Z",
      finished_at: "2026-07-29T01:02:00Z",
      failure_category: null,
      failure_phase: null,
      failure_summary: null,
      result: {
        artifact_dir: "/safe/artifacts",
        report_path: "/safe/report.md",
        off_resolved: false,
        on_resolved: true,
      },
      queue_position: null,
    };
  }
  if (status === "cancelled") {
    return {
      ...base,
      status,
      phase: null,
      started_at: null,
      finished_at: "2026-07-29T01:02:00Z",
      failure_category: null,
      failure_phase: null,
      failure_summary: null,
      result: null,
      queue_position: null,
    };
  }
  return {
    ...base,
    status,
    phase: "running_on",
    started_at: "2026-07-29T01:01:00Z",
    finished_at: "2026-07-29T01:02:00Z",
    failure_category: status === "failed" ? "codex_execution_failure" : "worker_interruption",
    failure_phase: "running_on",
    failure_summary: "安全的失败摘要",
    result: null,
    queue_position: null,
  };
}

export function apiStub(overrides: Partial<Record<keyof EvaluationApi, unknown>> = {}): EvaluationApi {
  return {
    listBatches: vi.fn().mockResolvedValue([]),
    getBatch: vi.fn().mockResolvedValue(batchRecord()),
    previewBatch: vi.fn().mockResolvedValue({
      powercontext_ref: "latest",
      benchmark: "swebench-pro",
      task_set: "swebench-pro-public-v2",
      model: "gpt-5.6-sol",
      reasoning_effort: "medium",
      treatment_mode: "off_on",
      total_tasks: 731,
      usage_pause_percent: 80,
      usage: usageSnapshot,
      estimate: { ...batchEstimate, basis: "historical_compatible" },
      can_start: true,
      block_reason: null,
    }),
    createBatch: vi.fn().mockResolvedValue(batchRecord()),
    pauseBatch: vi.fn().mockResolvedValue(batchRecord({ status: "paused" })),
    resumeBatch: vi.fn().mockResolvedValue(batchRecord()),
    cancelBatch: vi.fn().mockResolvedValue(batchRecord({ status: "cancelled" })),
    updateBatchThreshold: vi.fn().mockResolvedValue(batchRecord()),
    getAccountUsage: vi.fn().mockResolvedValue({ mode: "subscription", sufficient: true, usage: usageSnapshot }),
    getBatchRuntime: vi.fn().mockResolvedValue({
      batch_id: "batch-001",
      generated_at: "2026-08-16T09:30:00Z",
      status_counts: {
        queued: 1,
        running: 1,
        succeeded: 22,
        failed: 0,
        interrupted: 0,
        cancelled: 0,
      },
      tasks: [],
    }),
    listBatchControlEvents: vi.fn().mockResolvedValue([]),
    listTaskAttempts: vi.fn().mockResolvedValue([]),
    retryTask: vi.fn(),
    getBatchReport: vi.fn().mockResolvedValue(batchReport),
    listBatchTasks: vi.fn().mockResolvedValue(batchTaskPage),
    getBatchTask: vi.fn().mockResolvedValue(batchTaskDetail),
    listContextEvents: vi.fn().mockResolvedValue(contextEventPage),
    getContextEvent: vi.fn(),
    subscribeBatchEvents: vi.fn().mockReturnValue({ close: vi.fn() }),
    getCapabilities: vi.fn().mockResolvedValue(capabilities),
    getHealth: vi.fn().mockResolvedValue(health),
    listTasks: vi.fn().mockResolvedValue([]),
    getTask: vi.fn().mockResolvedValue(record("queued")),
    createTask: vi.fn().mockResolvedValue(record("queued")),
    cancelTask: vi.fn().mockResolvedValue(record("cancelled")),
    getReport: vi.fn().mockResolvedValue(report),
    getRawReport: vi.fn().mockResolvedValue("# report"),
    subscribeTaskEvents: vi.fn().mockReturnValue({ close: vi.fn() }),
    ...overrides,
  } as unknown as EvaluationApi;
}
