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

import type {
  AccountUsage,
  BatchCreate,
  BatchControlEvent,
  BatchEventSubscription,
  BatchPreview,
  BatchRecord,
  BatchReport,
  BatchRuntime,
  BatchTaskDetail,
  BatchTaskListOptions,
  BatchTaskPage,
  BatchTaskSet,
  Capabilities,
  ContextEvent,
  ContextEventPage,
  ContextPageOptions,
  EventStreamError,
  HealthResponse,
  ReportResponse,
  TaskCreate,
  TaskEvent,
  TaskEventSubscription,
  TaskAttempt,
  TaskListOptions,
  TaskRecord,
  TaskStatus,
  TaskSummary,
} from "./types";
import { z } from "zod";

const TASK_STATUSES = [
  "queued",
  "running",
  "succeeded",
  "failed",
  "interrupted",
  "cancelled",
] as const;
const TERMINAL_STATUSES = new Set<TaskStatus>(["succeeded", "failed", "interrupted", "cancelled"]);
const TERMINAL_BATCH_STATUSES = new Set(["completed", "cancelled"]);
const GENERIC_ERROR_MESSAGE = "The evaluation service could not complete the request.";
const INSTANCE_ID = "instance_flipt-io__flipt-518ec324b66a07fdd95464a5e9ca5fe7681ad8f9" as const;

type Fetch = typeof globalThis.fetch;

interface EventSourceLike {
  addEventListener(type: string, listener: EventListenerOrEventListenerObject): void;
  removeEventListener(type: string, listener: EventListenerOrEventListenerObject): void;
  close(): void;
}

export type EventSourceFactory = (url: string) => EventSourceLike;

export interface EvaluationApiOptions {
  fetch?: Fetch;
  eventSourceFactory?: EventSourceFactory;
}

export class ApiError extends Error {
  readonly status: number | null;
  readonly code: string;

  constructor(status: number | null, code: string, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

const timestampSchema = z.iso
  .datetime({ offset: true })
  .refine((value) => value.endsWith("Z") || value.endsWith("+00:00"), "Timestamp must use UTC.");
const nonnegativeIntegerSchema = z.number().int().nonnegative();
const queuePositionSchema = z.number().int().positive().nullable();
const nonnegativeNumberSchema = z.number().nonnegative();
const percentageSchema = z.number().int().min(0).max(100);
const taskStatusSchema = z.enum(TASK_STATUSES);
const taskPhaseSchema = z.enum([
  "preparing",
  "validating_gold",
  "running_off",
  "running_on",
  "official_evaluation",
  "generating_report",
]);
const failureCategorySchema = z.enum([
  "invalid_request",
  "queue_unavailable",
  "source_resolution_failure",
  "environment_preparation_failure",
  "gold_validation_failure",
  "codex_execution_failure",
  "codex_capacity_failure",
  "treatment_validation_failure",
  "official_evaluator_failure",
  "report_generation_failure",
  "worker_interruption",
  "internal",
]);
const codexModelSchema = z.string().regex(/^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/);
const batchTaskSetSchema = z.enum(["swebench-pro-public-v2", "swebench-pro-stability-v1"]);

const taskCreateSchema = z.strictObject({
  powercontext_ref: z.union([z.literal("latest"), z.string().regex(/^commit:[0-9a-fA-F]{40}$/)]),
  benchmark: z.literal("swebench-pro"),
  instance_id: z.literal(INSTANCE_ID),
  model: codexModelSchema,
  reasoning_effort: z.literal("medium"),
  treatment_mode: z.literal("off_on"),
  idempotency_key: z.string().min(8).max(128).regex(/^[A-Za-z0-9._-]+$/),
});

const taskResultSchema = z.strictObject({
  artifact_dir: z.string(),
  report_path: z.string(),
  off_resolved: z.boolean(),
  on_resolved: z.boolean(),
});

const taskRecordBaseShape = {
  task_id: z.string(),
  attempt_id: z.string().nullable(),
  attempt_number: z.number().int().positive(),
  attempt_count: z.number().int().positive(),
  retryable: z.boolean(),
  request: taskCreateSchema,
  created_at: timestampSchema,
  version: nonnegativeIntegerSchema,
  queue_position: queuePositionSchema,
};
const noFailureShape = {
  failure_category: z.null(),
  failure_phase: z.null(),
  failure_summary: z.null(),
};
const taskRecordSchema = z
  .discriminatedUnion("status", [
    z.strictObject({
      ...taskRecordBaseShape,
      status: z.literal("queued"),
      phase: z.null(),
      started_at: z.null(),
      finished_at: z.null(),
      ...noFailureShape,
      result: z.null(),
    }),
    z.strictObject({
      ...taskRecordBaseShape,
      status: z.literal("running"),
      phase: taskPhaseSchema.nullable(),
      started_at: timestampSchema,
      finished_at: z.null(),
      ...noFailureShape,
      result: z.null(),
    }),
    z.strictObject({
      ...taskRecordBaseShape,
      status: z.literal("succeeded"),
      phase: taskPhaseSchema.nullable(),
      started_at: timestampSchema,
      finished_at: timestampSchema,
      ...noFailureShape,
      result: taskResultSchema,
    }),
    z.strictObject({
      ...taskRecordBaseShape,
      status: z.enum(["failed", "interrupted"]),
      phase: taskPhaseSchema.nullable(),
      started_at: timestampSchema,
      finished_at: timestampSchema,
      failure_category: failureCategorySchema,
      failure_phase: taskPhaseSchema.nullable(),
      failure_summary: z.string().min(1).max(500),
      result: z.null(),
    }),
    z.strictObject({
      ...taskRecordBaseShape,
      status: z.literal("cancelled"),
      phase: z.null(),
      started_at: z.null(),
      finished_at: timestampSchema,
      ...noFailureShape,
      result: z.null(),
    }),
  ])
  .superRefine((record, context) => {
    const created = Date.parse(record.created_at);
    const started = record.started_at === null ? null : Date.parse(record.started_at);
    const finished = record.finished_at === null ? null : Date.parse(record.finished_at);
    if (started !== null && started < created) {
      context.addIssue({ code: "custom", message: "Task start precedes creation." });
    }
    if (finished !== null && finished < (started ?? created)) {
      context.addIssue({ code: "custom", message: "Task finish precedes its prior lifecycle timestamp." });
    }
  });

const taskSummarySchema = z.strictObject({
  task_id: z.string(),
  attempt_id: z.string().nullable(),
  attempt_number: z.number().int().positive(),
  attempt_count: z.number().int().positive(),
  retryable: z.boolean(),
  powercontext_ref: z.string(),
  instance_id: z.string(),
  model: z.string(),
  status: taskStatusSchema,
  phase: taskPhaseSchema.nullable(),
  created_at: timestampSchema,
  started_at: timestampSchema.nullable(),
  finished_at: timestampSchema.nullable(),
  version: nonnegativeIntegerSchema,
  off_resolved: z.boolean().nullable(),
  on_resolved: z.boolean().nullable(),
  queue_position: queuePositionSchema,
});

const taskEventSchema = z.strictObject({
  task_id: z.string(),
  status: taskStatusSchema,
  phase: taskPhaseSchema.nullable(),
  version: nonnegativeIntegerSchema,
  occurred_at: timestampSchema,
});

const capabilitiesSchema = z.strictObject({
  benchmarks: z.array(z.literal("swebench-pro")),
  instances: z.array(z.literal(INSTANCE_ID)),
  models: z.array(codexModelSchema),
  reasoning_efforts: z.array(z.literal("medium")),
  treatment_modes: z.array(z.literal("off_on")),
});

const healthSchema = z.strictObject({
  service: z.literal("ok"),
  worker_lease_active: z.boolean(),
  queued_tasks: nonnegativeIntegerSchema,
  running_tasks: nonnegativeIntegerSchema,
  active_task_pairs: nonnegativeIntegerSchema,
  task_parallelism: z.number().int().min(1).max(20),
  resource_admission_open: z.boolean(),
  filesystem_free_bytes: nonnegativeIntegerSchema.nullable(),
  filesystem_total_bytes: nonnegativeIntegerSchema.nullable(),
  filesystem_min_free_bytes: z.number().int().positive(),
  filesystem_free_inodes: nonnegativeIntegerSchema.nullable(),
  filesystem_total_inodes: nonnegativeIntegerSchema.nullable(),
  filesystem_min_free_inodes: z.number().int().positive(),
});

function armSchema<Arm extends "off" | "on">(arm: Arm) {
  return z.strictObject({
    arm: z.literal(arm),
    state: z.enum([
      "created",
      "revisions_resolved",
      "configuration_error",
      "gold_verified",
      "gold_check_failed",
      "infrastructure_error",
      "environment_ready",
      "codex_running",
      "patch_captured",
      "codex_error",
      "codex_timeout",
      "evaluated",
      "evaluation_error",
      "treatment_validated",
      "invalid_treatment",
      "reported",
    ]),
    resolution: z.enum(["resolved", "unresolved"]),
    passed: z.boolean().nullable(),
    treatment_valid: z.boolean(),
    input_tokens: nonnegativeIntegerSchema.nullable(),
    output_tokens: nonnegativeIntegerSchema.nullable(),
    elapsed_seconds: nonnegativeNumberSchema.nullable(),
    patch_bytes: nonnegativeIntegerSchema.nullable(),
  });
}

const metricComparisonSchema = z.strictObject({
  off: nonnegativeNumberSchema,
  on: nonnegativeNumberSchema,
  delta: z.number(),
  percent: z.number().nullable(),
});
const comparisonSchema = z.strictObject({
  input_tokens: metricComparisonSchema.nullable(),
  output_tokens: metricComparisonSchema.nullable(),
  elapsed_seconds: metricComparisonSchema.nullable(),
  patch_bytes: metricComparisonSchema.nullable(),
});
const treatmentEvidenceSchema = z.strictObject({
  mcp_requests: nonnegativeIntegerSchema,
  prompt_sources: nonnegativeIntegerSchema,
  plugin_checkout_sha: z.string(),
  plugin_id: z.string(),
  plugin_installed: z.boolean(),
  plugin_version: z.string(),
  scope_id: z.string(),
  server_ready: z.boolean(),
});
const goldValidationAuditSchema = z.strictObject({
  instance_id: z.string(),
  mode: z.enum(["dataset_patch", "verified_override"]),
  dataset_patch_sha256: z.string().regex(/^[0-9a-f]{64}$/),
  validation_patch_sha256: z.string().regex(/^[0-9a-f]{64}$/),
  dataset_patch_status: z.enum(["unverified", "known_failed"]),
  reference_validation_status: z.enum(["not_applicable", "passed"]),
  attempt_gold_validation_status: z.enum(["pending", "passed", "failed"]),
  source_dataset: z.string().nullable(),
  source_revision: z.string().regex(/^[0-9a-f]{40}$/).nullable(),
  source_file_oid: z.string().regex(/^[0-9a-f]{40}$/).nullable(),
  source_kind: z.literal("verified_reference_submission").nullable(),
});
const reportSchema = z.strictObject({
  task_id: z.string(),
  acceptance_valid: z.boolean(),
  off: armSchema("off"),
  on: armSchema("on"),
  comparison: comparisonSchema,
  evidence: z.strictObject({
    off: treatmentEvidenceSchema,
    on: treatmentEvidenceSchema,
  }),
  gold_validation: goldValidationAuditSchema.nullable().optional(),
  revisions: z.record(z.string(), z.string()),
  configuration: z.record(z.string(), z.string()),
  generated_at: timestampSchema,
});
const batchCreateSchema = z.strictObject({
  powercontext_ref: z.union([z.literal("latest"), z.string().regex(/^commit:[0-9a-fA-F]{40}$/)]),
  benchmark: z.literal("swebench-pro"),
  task_set: batchTaskSetSchema,
  model: codexModelSchema,
  reasoning_effort: z.literal("medium"),
  treatment_mode: z.literal("off_on"),
  idempotency_key: z.string().min(8).max(128).regex(/^[A-Za-z0-9._-]+$/),
  usage_pause_percent: z.number().int().min(1).max(100),
  initial_control_intent: z.enum(["run", "pause"]),
  container_env: z.record(z.string(), z.string()).optional(),
});
const usageSnapshotSchema = z.strictObject({
  limit_id: z.literal("codex"),
  used_percent: percentageSchema,
  remaining_percent: percentageSchema,
  window_duration_minutes: z.number().int().positive(),
  resets_at: timestampSchema,
  observed_at: timestampSchema,
  rate_limit_reached_type: z.string().nullable(),
  plan_type: z.string().nullable(),
  account_tokens: nonnegativeIntegerSchema.nullable(),
  probe_version: z.literal(1),
});
const accountUsageSchema = z.discriminatedUnion("mode", [
  z.strictObject({
    mode: z.literal("api_key"),
    sufficient: z.literal(true),
    usage: z.null(),
  }),
  z.strictObject({
    mode: z.literal("subscription"),
    sufficient: z.boolean(),
    usage: usageSnapshotSchema,
  }),
]);
const batchControlSchema = z.strictObject({
  intent: z.enum(["run", "pause", "cancel"]),
  usage_pause_percent: z.number().int().min(1).max(100),
  pause_reason: z
    .enum([
      "user",
      "usage_threshold",
      "usage_unavailable",
      "quota_limit",
      "infrastructure_failure",
      "codex_capacity",
      "resource_pressure",
    ])
    .nullable(),
  updated_at: timestampSchema,
  version: nonnegativeIntegerSchema,
});
const batchEstimateSchema = z.strictObject({
  quality: z.enum(["unavailable", "preliminary", "measured"]),
  basis: z.enum(["none", "current_batch", "historical_compatible"]),
  sample_size: nonnegativeIntegerSchema,
  remaining_tasks: nonnegativeIntegerSchema,
  remaining_tokens: nonnegativeIntegerSchema.nullable(),
  remaining_duration_seconds: nonnegativeIntegerSchema.nullable(),
  low_tokens: nonnegativeIntegerSchema.nullable(),
  high_tokens: nonnegativeIntegerSchema.nullable(),
  low_duration_seconds: nonnegativeIntegerSchema.nullable(),
  high_duration_seconds: nonnegativeIntegerSchema.nullable(),
});
const batchRecordSchema = z.strictObject({
  batch_id: z.string(),
  request: batchCreateSchema,
  total_tasks: z.number().int().positive(),
  status: z.enum(["queued", "running", "pausing", "paused", "cancelling", "completed", "cancelled"]),
  control: batchControlSchema,
  created_at: timestampSchema,
  started_at: timestampSchema.nullable(),
  finished_at: timestampSchema.nullable(),
  resolved_powercontext_sha: z.string().regex(/^[0-9a-f]{40}$/).nullable(),
});
const batchPreviewSchema = z.strictObject({
  powercontext_ref: z.union([z.literal("latest"), z.string().regex(/^commit:[0-9a-fA-F]{40}$/)]),
  benchmark: z.literal("swebench-pro"),
  task_set: batchTaskSetSchema,
  model: codexModelSchema,
  reasoning_effort: z.literal("medium"),
  treatment_mode: z.literal("off_on"),
  total_tasks: z.number().int().positive(),
  usage_pause_percent: z.number().int().min(1).max(100),
  usage: usageSnapshotSchema.nullable(),
  estimate: batchEstimateSchema,
  can_start: z.boolean(),
  block_reason: z.literal("usage_threshold_reached").nullable(),
});
const pairCategorySchema = z.enum([
  "off_fail_on_pass",
  "off_pass_on_fail",
  "both_pass",
  "both_fail",
  "execution_failure",
]);
const resolutionAggregateSchema = z.strictObject({
  resolved: nonnegativeIntegerSchema,
  total: nonnegativeIntegerSchema,
  rate_percent: z.number().min(0).max(100),
});
const tokenMetricAggregateSchema = z.strictObject({
  off: nonnegativeIntegerSchema,
  on: nonnegativeIntegerSchema,
  delta: z.number().int(),
  off_measured_tasks: nonnegativeIntegerSchema,
  on_measured_tasks: nonnegativeIntegerSchema,
});
const batchReportSchema = z.strictObject({
  batch_id: z.string(),
  report_revision: nonnegativeIntegerSchema,
  total_tasks: z.number().int().positive(),
  terminal_tasks: nonnegativeIntegerSchema,
  comparable_pairs: nonnegativeIntegerSchema,
  execution_failures: nonnegativeIntegerSchema,
  cancelled_tasks: nonnegativeIntegerSchema,
  off: resolutionAggregateSchema,
  on: resolutionAggregateSchema,
  resolution_rate_delta_points: z.number(),
  pair_categories: z.record(pairCategorySchema, nonnegativeIntegerSchema),
  task_statuses: z.record(taskStatusSchema, nonnegativeIntegerSchema),
  tokens: z.strictObject({
    input: tokenMetricAggregateSchema,
    output: tokenMetricAggregateSchema,
    total: tokenMetricAggregateSchema,
  }),
  control: batchControlSchema,
  latest_usage: usageSnapshotSchema.nullable(),
  estimate: batchEstimateSchema,
  revisions: z.record(z.string(), z.string()),
  configuration: z.record(z.string(), z.string()),
});
const batchRuntimeFailureSchema = z.strictObject({
  category: failureCategorySchema,
  code: z.string(),
  phase: taskPhaseSchema.nullable(),
  summary: z.string().min(1).max(500),
  finished_at: timestampSchema,
});
const batchRuntimeTaskSchema = z.strictObject({
  task_id: z.string(),
  attempt_id: z.string(),
  instance_id: z.string(),
  source_index: nonnegativeIntegerSchema,
  status: z.enum(["queued", "running"]),
  phase: taskPhaseSchema.nullable(),
  attempt_number: z.number().int().positive(),
  attempt_count: z.number().int().positive(),
  created_at: timestampSchema,
  eligible_at: timestampSchema,
  started_at: timestampSchema.nullable(),
  last_failure: batchRuntimeFailureSchema.nullable(),
});
const batchRuntimeSchema = z.strictObject({
  batch_id: z.string(),
  generated_at: timestampSchema,
  status_counts: z.record(taskStatusSchema, nonnegativeIntegerSchema),
  tasks: z.array(batchRuntimeTaskSchema),
});
const taskArmSummarySchema = z.strictObject({
  resolved: z.boolean(),
  input_tokens: nonnegativeIntegerSchema.nullable(),
  output_tokens: nonnegativeIntegerSchema.nullable(),
  total_tokens: nonnegativeIntegerSchema.nullable(),
});
const taskTokenDeltaSchema = z.strictObject({
  off: nonnegativeIntegerSchema.nullable(),
  on: nonnegativeIntegerSchema.nullable(),
  delta: z.number().int().nullable(),
});
const batchTaskItemSchema = z.strictObject({
  task_id: z.string(),
  attempt_id: z.string().nullable(),
  attempt_number: z.number().int().positive(),
  attempt_count: z.number().int().positive(),
  retryable: z.boolean(),
  model: codexModelSchema,
  reasoning_effort: z.literal("medium"),
  instance_id: z.string(),
  repository: z.string(),
  source_index: nonnegativeIntegerSchema,
  status: taskStatusSchema,
  pair_category: pairCategorySchema.nullable(),
  off: taskArmSummarySchema.nullable(),
  on: taskArmSummarySchema.nullable(),
  tokens: taskTokenDeltaSchema,
  failure_category: z.string().nullable(),
  failure_summary: z.string().nullable(),
});
const taskAttemptSchema = z.strictObject({
  attempt_id: z.string(),
  task_id: z.string(),
  attempt_number: z.number().int().positive(),
  status: taskStatusSchema,
  phase: taskPhaseSchema.nullable(),
  created_at: timestampSchema,
  started_at: timestampSchema.nullable(),
  finished_at: timestampSchema.nullable(),
  version: nonnegativeIntegerSchema,
  failure_category: failureCategorySchema.nullable(),
  failure_phase: taskPhaseSchema.nullable(),
  failure_summary: z.string().max(500).nullable(),
  result: taskResultSchema.nullable(),
  retryable: z.boolean(),
});
const batchControlEventSchema = z.strictObject({
  sequence: z.number().int().positive(),
  batch_id: z.string(),
  event_type: z.enum([
    "batch_created",
    "threshold_changed",
    "pause_requested",
    "paused",
    "resume_requested",
    "resumed",
    "cancel_requested",
    "cancelled",
    "usage_threshold_reached",
    "usage_unavailable",
    "quota_limit_reached",
    "infrastructure_failure",
    "batch_completed",
    "resource_pressure",
    "task_retry_requested",
  ]),
  actor: z.enum(["user", "system"]),
  details: z.record(z.string(), z.union([z.number().int(), z.string(), z.null()])),
  occurred_at: timestampSchema,
});
const batchTaskPageSchema = z.strictObject({
  items: z.array(batchTaskItemSchema),
  total: nonnegativeIntegerSchema,
  limit: z.number().int().positive(),
  offset: nonnegativeIntegerSchema,
});
const officialTestGroupSchema = z.strictObject({
  passed: nonnegativeIntegerSchema,
  total: nonnegativeIntegerSchema,
  failed: z.array(z.string()),
});
const taskDetailArmSchema = z.strictObject({
  resolved: z.boolean(),
  patch_applied: z.boolean().nullable(),
  fail_to_pass: officialTestGroupSchema,
  pass_to_pass: officialTestGroupSchema,
  log_excerpt: z.string().max(4_000).nullable(),
  input_tokens: nonnegativeIntegerSchema.nullable(),
  output_tokens: nonnegativeIntegerSchema.nullable(),
  total_tokens: nonnegativeIntegerSchema.nullable(),
});
const tokensflowFinalizationSchema = z.strictObject({
  state: z.enum(["pending", "passed", "timed_out", "capacity_evicted", "cleanup_failed"]),
  registered_at: timestampSchema,
  deadline_at: timestampSchema,
  finished_at: timestampSchema.nullable(),
  attempts: nonnegativeIntegerSchema,
  queue_passed: z.boolean(),
  doctor_rc: z.number().int().nullable(),
  error_category: z.string().nullable(),
  reason: z.string().nullable(),
});
const batchTaskDetailSchema = z.strictObject({
  task: batchTaskItemSchema,
  problem_statement: z.string(),
  required_tests: z.strictObject({
    fail_to_pass: z.array(z.string()),
    pass_to_pass: z.array(z.string()),
    selected_test_files_to_run: z.string(),
    test_patch: z.string(),
  }),
  off: taskDetailArmSchema.nullable(),
  on: taskDetailArmSchema.nullable(),
  tokensflow_finalization: z.strictObject({
    off: tokensflowFinalizationSchema.nullable(),
    on: tokensflowFinalizationSchema.nullable(),
  }),
});
const contextEventSchema = z.strictObject({
  sequence: z.number().int().positive(),
  observed_at: timestampSchema,
  elapsed_ms: nonnegativeIntegerSchema,
  arm: z.enum(["off", "on"]),
  actor: z.string(),
  event_type: z.string(),
  input: z.record(z.string(), z.unknown()).nullable(),
  output: z.record(z.string(), z.unknown()).nullable(),
  source_artifact: z.string(),
  source_sequence: nonnegativeIntegerSchema,
});
const contextEventPageSchema = z.strictObject({
  items: z.array(contextEventSchema),
  total: nonnegativeIntegerSchema,
  limit: z.number().int().positive(),
  offset: nonnegativeIntegerSchema,
});
const errorEnvelopeSchema = z.strictObject({
  error: z.strictObject({
    code: z.string(),
    message: z.string(),
  }),
});

function validateWithSchema<T>(schema: z.ZodType<T>, value: unknown): T {
  return schema.parse(value);
}

function validateTaskRecord(value: unknown): TaskRecord {
  return validateWithSchema(taskRecordSchema, value);
}

function validateTaskSummary(value: unknown): TaskSummary {
  return validateWithSchema(taskSummarySchema, value);
}

function validateTaskEvent(value: unknown): TaskEvent {
  return validateWithSchema(taskEventSchema, value);
}

function validateCapabilities(value: unknown): Capabilities {
  return validateWithSchema(capabilitiesSchema, value);
}

function validateHealth(value: unknown): HealthResponse {
  return validateWithSchema(healthSchema, value);
}

function validateReport(value: unknown): ReportResponse {
  return validateWithSchema(reportSchema, value);
}

function validateBatch(value: unknown): BatchRecord {
  return validateWithSchema(batchRecordSchema, value) as BatchRecord;
}

function validateAuthUpdate(value: unknown): { updated_at: string } {
  return validateWithSchema(z.object({ updated_at: timestampSchema }), value);
}

function validateBatchPreview(value: unknown): BatchPreview {
  return validateWithSchema(batchPreviewSchema, value);
}

function validateBatchReport(value: unknown): BatchReport {
  return validateWithSchema(batchReportSchema, value);
}

function validateBatchTaskPage(value: unknown): BatchTaskPage {
  return validateWithSchema(batchTaskPageSchema, value);
}

function validateBatchTaskDetail(value: unknown): BatchTaskDetail {
  return validateWithSchema(batchTaskDetailSchema, value);
}

function validateContextEvent(value: unknown): ContextEvent {
  return validateWithSchema(contextEventSchema, value);
}

function validateContextEventPage(value: unknown): ContextEventPage {
  return validateWithSchema(contextEventPageSchema, value);
}

function validateAccountUsage(value: unknown): AccountUsage {
  return validateWithSchema(accountUsageSchema, value);
}

function validateTaskAttempt(value: unknown): TaskAttempt {
  return validateWithSchema(taskAttemptSchema, value);
}

function validateBatchControlEvent(value: unknown): BatchControlEvent {
  return validateWithSchema(batchControlEventSchema, value);
}

function mediaType(response: Response): string {
  return response.headers.get("Content-Type")?.split(";", 1)[0]?.trim().toLowerCase() ?? "";
}

function apiPath(path: string): string {
  return `/api${path}`;
}

function taskPath(taskId: string, suffix = ""): string {
  return apiPath(`/tasks/${encodeURIComponent(taskId)}${suffix}`);
}

function batchPath(batchId: string, suffix = ""): string {
  return apiPath(`/batches/${encodeURIComponent(batchId)}${suffix}`);
}

function withSignal(signal: AbortSignal | undefined): Pick<RequestInit, "signal"> {
  return signal === undefined ? {} : { signal };
}

export class EvaluationApi {
  readonly #fetch: Fetch;
  readonly #eventSourceFactory: EventSourceFactory;

  constructor(options: EvaluationApiOptions = {}) {
    this.#fetch = options.fetch ?? globalThis.fetch.bind(globalThis);
    this.#eventSourceFactory =
      options.eventSourceFactory ?? ((url) => new globalThis.EventSource(url));
  }

  getCapabilities(signal?: AbortSignal): Promise<Capabilities> {
    return this.#json(apiPath("/capabilities"), validateCapabilities, withSignal(signal));
  }

  listBatches(signal?: AbortSignal): Promise<BatchRecord[]> {
    return this.#json(
      apiPath("/batches"),
      (value) => {
        if (!Array.isArray(value)) throw new ApiError(null, "invalid_response", GENERIC_ERROR_MESSAGE);
        return value.map(validateBatch);
      },
      withSignal(signal),
    );
  }

  getBatch(batchId: string, signal?: AbortSignal): Promise<BatchRecord> {
    return this.#json(batchPath(batchId), validateBatch, withSignal(signal));
  }

  previewBatch(
    request: { powercontext_ref: string; task_set: BatchTaskSet; model: string; usage_pause_percent: number },
    signal?: AbortSignal,
  ): Promise<BatchPreview> {
    return this.#json(apiPath("/batches/preview"), validateBatchPreview, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(request),
      ...withSignal(signal),
    });
  }

  createBatch(batch: BatchCreate, signal?: AbortSignal): Promise<BatchRecord> {
    return this.#json(apiPath("/batches"), validateBatch, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(batch),
      ...withSignal(signal),
    });
  }

  updateAuth(authJson: string, signal?: AbortSignal): Promise<{ updated_at: string }> {
    return this.#json(apiPath("/auth"), validateAuthUpdate, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ auth_json: authJson }),
      ...withSignal(signal),
    });
  }

  pauseBatch(batchId: string, signal?: AbortSignal): Promise<BatchRecord> {
    return this.#json(batchPath(batchId, "/pause"), validateBatch, {
      method: "POST",
      ...withSignal(signal),
    });
  }

  resumeBatch(batchId: string, signal?: AbortSignal): Promise<BatchRecord> {
    return this.#json(batchPath(batchId, "/resume"), validateBatch, {
      method: "POST",
      ...withSignal(signal),
    });
  }

  cancelBatch(batchId: string, signal?: AbortSignal): Promise<BatchRecord> {
    return this.#json(batchPath(batchId, "/cancel"), validateBatch, {
      method: "POST",
      ...withSignal(signal),
    });
  }

  updateBatchThreshold(
    batchId: string,
    usagePausePercent: number,
    expectedVersion: number,
    signal?: AbortSignal,
  ): Promise<BatchRecord> {
    return this.#json(batchPath(batchId, "/controls"), validateBatch, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        usage_pause_percent: usagePausePercent,
        expected_version: expectedVersion,
      }),
      ...withSignal(signal),
    });
  }

  getAccountUsage(signal?: AbortSignal): Promise<AccountUsage> {
    return this.#json(apiPath("/account-usage"), validateAccountUsage, withSignal(signal));
  }

  getBatchRuntime(batchId: string, signal?: AbortSignal): Promise<BatchRuntime> {
    return this.#json(batchPath(batchId, "/runtime"), (value) => validateWithSchema(batchRuntimeSchema, value), {
      ...withSignal(signal),
    });
  }

  listBatchControlEvents(batchId: string, signal?: AbortSignal): Promise<BatchControlEvent[]> {
    return this.#json(
      batchPath(batchId, "/control-events"),
      (value) => {
        if (!Array.isArray(value)) throw new ApiError(null, "invalid_response", GENERIC_ERROR_MESSAGE);
        return value.map(validateBatchControlEvent);
      },
      withSignal(signal),
    );
  }

  listTaskAttempts(batchId: string, taskId: string, signal?: AbortSignal): Promise<TaskAttempt[]> {
    return this.#json(
      batchPath(batchId, `/tasks/${encodeURIComponent(taskId)}/attempts`),
      (value) => {
        if (!Array.isArray(value)) throw new ApiError(null, "invalid_response", GENERIC_ERROR_MESSAGE);
        return value.map(validateTaskAttempt);
      },
      withSignal(signal),
    );
  }

  retryTask(
    batchId: string,
    taskId: string,
    idempotencyKey: string,
    signal?: AbortSignal,
  ): Promise<TaskAttempt> {
    return this.#json(
      batchPath(batchId, `/tasks/${encodeURIComponent(taskId)}/retry`),
      validateTaskAttempt,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ idempotency_key: idempotencyKey }),
        ...withSignal(signal),
      },
    );
  }

  getBatchReport(batchId: string, signal?: AbortSignal): Promise<BatchReport> {
    return this.#json(batchPath(batchId, "/report"), validateBatchReport, withSignal(signal));
  }

  listBatchTasks(
    batchId: string,
    options: BatchTaskListOptions = {},
    signal?: AbortSignal,
  ): Promise<BatchTaskPage> {
    const query = new URLSearchParams();
    if (options.category !== undefined) query.set("category", options.category);
    if (options.query !== undefined && options.query !== "") query.set("q", options.query);
    if (options.sort !== undefined) query.set("sort", options.sort);
    if (options.limit !== undefined) query.set("limit", String(options.limit));
    if (options.offset !== undefined) query.set("offset", String(options.offset));
    const suffix = query.size === 0 ? "/tasks" : `/tasks?${query.toString()}`;
    return this.#json(batchPath(batchId, suffix), validateBatchTaskPage, withSignal(signal));
  }

  getBatchTask(
    batchId: string,
    taskId: string,
    signal?: AbortSignal,
    attemptId?: string,
  ): Promise<BatchTaskDetail> {
    const query = attemptId === undefined ? "" : `?attempt_id=${encodeURIComponent(attemptId)}`;
    return this.#json(
      batchPath(batchId, `/tasks/${encodeURIComponent(taskId)}${query}`),
      validateBatchTaskDetail,
      withSignal(signal),
    );
  }

  listContextEvents(
    batchId: string,
    taskId: string,
    arm: "off" | "on",
    options: ContextPageOptions = {},
    signal?: AbortSignal,
  ): Promise<ContextEventPage> {
    const query = new URLSearchParams();
    if (options.limit !== undefined) query.set("limit", String(options.limit));
    if (options.offset !== undefined) query.set("offset", String(options.offset));
    if (options.attempt_id !== undefined) query.set("attempt_id", options.attempt_id);
    const querySuffix = query.size === 0 ? "" : `?${query.toString()}`;
    return this.#json(
      batchPath(batchId, `/tasks/${encodeURIComponent(taskId)}/context/${arm}${querySuffix}`),
      validateContextEventPage,
      withSignal(signal),
    );
  }

  getContextEvent(
    batchId: string,
    taskId: string,
    arm: "off" | "on",
    sequence: number,
    signal?: AbortSignal,
    attemptId?: string,
  ): Promise<ContextEvent> {
    const query = attemptId === undefined ? "" : `?attempt_id=${encodeURIComponent(attemptId)}`;
    return this.#json(
      batchPath(batchId, `/tasks/${encodeURIComponent(taskId)}/context/${arm}/${sequence}${query}`),
      validateContextEvent,
      withSignal(signal),
    );
  }

  subscribeBatchEvents(
    batchId: string,
    onEvent: (event: BatchRecord) => void,
    onError: (error: EventStreamError) => void = () => undefined,
  ): BatchEventSubscription {
    const source = this.#eventSourceFactory(batchPath(batchId, "/events"));
    let closed = false;
    const close = (): void => {
      if (closed) return;
      closed = true;
      source.removeEventListener("batch", batchListener);
      source.removeEventListener("error", errorListener);
      source.close();
    };
    const batchListener: EventListener = (nativeEvent) => {
      if (closed || !(nativeEvent instanceof MessageEvent) || typeof nativeEvent.data !== "string") return;
      try {
        const event = validateBatch(JSON.parse(nativeEvent.data) as unknown);
        onEvent(event);
        if (TERMINAL_BATCH_STATUSES.has(event.status)) close();
      } catch {
        onError({
          code: "invalid_event",
          message: "A live update could not be read safely.",
          reconnecting: true,
        });
      }
    };
    const errorListener: EventListener = () => {
      if (closed) {
        return;
      }
      onError({
        code: "event_stream_disconnected",
        message: "Live updates were interrupted. Reconnecting automatically.",
        reconnecting: true,
      });
    };
    source.addEventListener("batch", batchListener);
    source.addEventListener("error", errorListener);
    return { close };
  }

  getHealth(signal?: AbortSignal): Promise<HealthResponse> {
    return this.#json(apiPath("/health"), validateHealth, withSignal(signal));
  }

  listTasks(options: TaskListOptions = {}, signal?: AbortSignal): Promise<TaskSummary[]> {
    const query = new URLSearchParams();
    if (options.status !== undefined) query.set("status", options.status);
    if (options.order !== undefined) query.set("order", options.order);
    if (options.limit !== undefined) query.set("limit", String(options.limit));
    if (options.offset !== undefined) query.set("offset", String(options.offset));
    const suffix = query.size === 0 ? "" : `?${query.toString()}`;
    return this.#json(
      `${apiPath("/tasks")}${suffix}`,
      (value) => {
        if (!Array.isArray(value)) throw new ApiError(null, "invalid_response", GENERIC_ERROR_MESSAGE);
        return value.map(validateTaskSummary);
      },
      withSignal(signal),
    );
  }

  getTask(taskId: string, signal?: AbortSignal): Promise<TaskRecord> {
    return this.#json(taskPath(taskId), validateTaskRecord, withSignal(signal));
  }

  createTask(task: TaskCreate, signal?: AbortSignal): Promise<TaskRecord> {
    return this.#json(apiPath("/tasks"), validateTaskRecord, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(task),
      ...withSignal(signal),
    });
  }

  cancelTask(taskId: string, signal?: AbortSignal): Promise<TaskRecord> {
    return this.#json(taskPath(taskId, "/cancel"), validateTaskRecord, {
      method: "POST",
      ...withSignal(signal),
    });
  }

  getReport(taskId: string, signal?: AbortSignal): Promise<ReportResponse> {
    return this.#json(taskPath(taskId, "/report"), validateReport, withSignal(signal));
  }

  async getRawReport(taskId: string, signal?: AbortSignal): Promise<string> {
    const response = await this.#request(taskPath(taskId, "/report.md"), {
      headers: { Accept: "text/plain" },
      ...withSignal(signal),
    });
    if (mediaType(response) !== "text/plain") {
      throw new ApiError(response.status, "invalid_response", GENERIC_ERROR_MESSAGE);
    }
    return response.text();
  }

  subscribeTaskEvents(
    taskId: string,
    onEvent: (event: TaskEvent) => void,
    onError: (error: EventStreamError) => void = () => undefined,
  ): TaskEventSubscription {
    const source = this.#eventSourceFactory(taskPath(taskId, "/events"));
    let closed = false;

    const close = (): void => {
      if (closed) return;
      closed = true;
      source.removeEventListener("task", taskListener);
      source.removeEventListener("error", errorListener);
      source.close();
    };
    const taskListener: EventListener = (nativeEvent) => {
      if (closed || !(nativeEvent instanceof MessageEvent) || typeof nativeEvent.data !== "string") return;
      try {
        const event = validateTaskEvent(JSON.parse(nativeEvent.data) as unknown);
        onEvent(event);
        if (TERMINAL_STATUSES.has(event.status)) close();
      } catch {
        onError({
          code: "invalid_event",
          message: "A live update could not be read safely.",
          reconnecting: true,
        });
      }
    };
    const errorListener: EventListener = () => {
      if (closed) return;
      onError({
        code: "event_stream_disconnected",
        message: "Live updates were interrupted. Reconnecting automatically.",
        reconnecting: true,
      });
    };

    source.addEventListener("task", taskListener);
    source.addEventListener("error", errorListener);
    return { close };
  }

  async #json<T>(
    url: string,
    validate: (value: unknown) => T,
    init: RequestInit,
  ): Promise<T> {
    const headers = new Headers(init.headers);
    headers.set("Accept", "application/json");
    const response = await this.#request(url, { ...init, headers });
    if (mediaType(response) !== "application/json") {
      throw new ApiError(response.status, "invalid_response", GENERIC_ERROR_MESSAGE);
    }
    try {
      return validate((await response.json()) as unknown);
    } catch (error) {
      if (error instanceof ApiError) throw error;
      throw new ApiError(response.status, "invalid_response", GENERIC_ERROR_MESSAGE);
    }
  }

  async #request(url: string, init: RequestInit): Promise<Response> {
    let response: Response;
    try {
      response = await this.#fetch(url, init);
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") {
        throw new ApiError(null, "request_aborted", "The evaluation request was cancelled.");
      }
      throw new ApiError(null, "request_failed", GENERIC_ERROR_MESSAGE);
    }
    if (response.ok) return response;

    if (mediaType(response) === "application/json") {
      try {
        const parsed = errorEnvelopeSchema.safeParse((await response.json()) as unknown);
        if (parsed.success) {
          throw new ApiError(response.status, parsed.data.error.code, parsed.data.error.message);
        }
      } catch (error) {
        if (error instanceof ApiError) throw error;
      }
    }
    throw new ApiError(response.status, "request_failed", GENERIC_ERROR_MESSAGE);
  }
}
