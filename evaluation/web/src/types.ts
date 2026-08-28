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

export type TaskStatus = "queued" | "running" | "succeeded" | "failed" | "interrupted" | "cancelled";
export type Arm = "off" | "on";
export type TreatmentMode = "off_on" | "on_only" | "off_only";

export type TaskPhase =
  | "preparing"
  | "validating_gold"
  | "running_off"
  | "running_on"
  | "official_evaluation"
  | "generating_report";

export type FailureCategory =
  | "invalid_request"
  | "queue_unavailable"
  | "source_resolution_failure"
  | "environment_preparation_failure"
  | "gold_validation_failure"
  | "codex_execution_failure"
  | "codex_capacity_failure"
  | "treatment_validation_failure"
  | "official_evaluator_failure"
  | "report_generation_failure"
  | "worker_interruption"
  | "internal";

export interface TaskCreate {
  powercontext_ref: string;
  benchmark: "swebench-pro";
  instance_id: "instance_flipt-io__flipt-518ec324b66a07fdd95464a5e9ca5fe7681ad8f9";
  model: string;
  reasoning_effort: "medium";
  treatment_mode: TreatmentMode;
  idempotency_key: string;
}

export interface TaskResult {
  artifact_dir: string;
  report_path: string;
  off_resolved: boolean | null;
  on_resolved: boolean | null;
}

interface TaskRecordBase {
  task_id: string;
  attempt_id: string | null;
  attempt_number: number;
  attempt_count: number;
  retryable: boolean;
  request: TaskCreate;
  created_at: string;
  version: number;
  queue_position: number | null;
}

export interface QueuedTaskRecord extends TaskRecordBase {
  status: "queued";
  phase: null;
  started_at: null;
  finished_at: null;
  failure_category: null;
  failure_phase: null;
  failure_summary: null;
  result: null;
}

export interface RunningTaskRecord extends TaskRecordBase {
  status: "running";
  phase: TaskPhase | null;
  started_at: string;
  finished_at: null;
  failure_category: null;
  failure_phase: null;
  failure_summary: null;
  result: null;
}

export interface SucceededTaskRecord extends TaskRecordBase {
  status: "succeeded";
  phase: TaskPhase | null;
  started_at: string;
  finished_at: string;
  failure_category: null;
  failure_phase: null;
  failure_summary: null;
  result: TaskResult;
}

export interface FailedTaskRecord extends TaskRecordBase {
  status: "failed" | "interrupted";
  phase: TaskPhase | null;
  started_at: string;
  finished_at: string;
  failure_category: FailureCategory;
  failure_phase: TaskPhase | null;
  failure_summary: string;
  result: null;
}

export interface CancelledTaskRecord extends TaskRecordBase {
  status: "cancelled";
  phase: null;
  started_at: null;
  finished_at: string;
  failure_category: null;
  failure_phase: null;
  failure_summary: null;
  result: null;
}

export type TaskRecord =
  | QueuedTaskRecord
  | RunningTaskRecord
  | SucceededTaskRecord
  | FailedTaskRecord
  | CancelledTaskRecord;

export interface TaskSummary {
  task_id: string;
  attempt_id: string | null;
  attempt_number: number;
  attempt_count: number;
  retryable: boolean;
  powercontext_ref: string;
  instance_id: string;
  model: string;
  status: TaskStatus;
  phase: TaskPhase | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  version: number;
  off_resolved: boolean | null;
  on_resolved: boolean | null;
  queue_position: number | null;
}

export interface TaskEvent {
  task_id: string;
  status: TaskStatus;
  phase: TaskPhase | null;
  version: number;
  occurred_at: string;
}

export interface Capabilities {
  benchmarks: "swebench-pro"[];
  instances: "instance_flipt-io__flipt-518ec324b66a07fdd95464a5e9ca5fe7681ad8f9"[];
  models: string[];
  reasoning_efforts: "medium"[];
  treatment_modes: TreatmentMode[];
}

export interface HealthResponse {
  service: "ok";
  worker_lease_active: boolean;
  queued_tasks: number;
  running_tasks: number;
  active_task_pairs: number;
  task_parallelism: number;
  resource_admission_open: boolean;
  filesystem_free_bytes: number | null;
  filesystem_total_bytes: number | null;
  filesystem_min_free_bytes: number;
  filesystem_free_inodes: number | null;
  filesystem_total_inodes: number | null;
  filesystem_min_free_inodes: number;
}

export interface ArmResponse {
  arm: "off" | "on";
  state:
    | "created"
    | "revisions_resolved"
    | "configuration_error"
    | "gold_verified"
    | "gold_check_failed"
    | "infrastructure_error"
    | "environment_ready"
    | "codex_running"
    | "patch_captured"
    | "codex_error"
    | "codex_timeout"
    | "evaluated"
    | "evaluation_error"
    | "treatment_validated"
    | "invalid_treatment"
    | "reported";
  resolution: "resolved" | "unresolved";
  passed: boolean | null;
  treatment_valid: boolean;
  input_tokens: number | null;
  output_tokens: number | null;
  elapsed_seconds: number | null;
  patch_bytes: number | null;
}

export interface MetricComparison {
  off: number;
  on: number;
  delta: number;
  percent: number | null;
}

export interface ComparisonResponse {
  input_tokens: MetricComparison | null;
  output_tokens: MetricComparison | null;
  elapsed_seconds: MetricComparison | null;
  patch_bytes: MetricComparison | null;
}

export interface TreatmentEvidence {
  mcp_requests: number;
  prompt_sources: number;
  plugin_checkout_sha: string;
  plugin_id: string;
  plugin_installed: boolean;
  plugin_version: string;
  scope_id: string;
  server_ready: boolean;
}

export interface EvidenceResponse {
  off: TreatmentEvidence | null;
  on: TreatmentEvidence | null;
}

export interface GoldValidationAudit {
  instance_id: string;
  mode: "dataset_patch" | "verified_override";
  dataset_patch_sha256: string;
  validation_patch_sha256: string;
  dataset_patch_status: "unverified" | "known_failed";
  reference_validation_status: "not_applicable" | "passed";
  attempt_gold_validation_status: "pending" | "passed" | "failed";
  source_dataset: string | null;
  source_revision: string | null;
  source_file_oid: string | null;
  source_kind: "verified_reference_submission" | null;
}

export interface ReportResponse {
  task_id: string;
  acceptance_valid: boolean;
  treatment_mode: TreatmentMode;
  off: (ArmResponse & { arm: "off" }) | null;
  on: (ArmResponse & { arm: "on" }) | null;
  comparison: ComparisonResponse | null;
  evidence: EvidenceResponse;
  gold_validation?: GoldValidationAudit | null | undefined;
  revisions: Record<string, string>;
  configuration: Record<string, string>;
  generated_at: string;
}

export interface TaskListOptions {
  status?: TaskStatus;
  order?: "oldest" | "newest";
  limit?: number;
  offset?: number;
}

export interface EventStreamError {
  code: "event_stream_disconnected" | "invalid_event";
  message: string;
  reconnecting: boolean;
}

export interface TaskEventSubscription {
  close(): void;
}

export type BatchStatus =
  | "queued"
  | "running"
  | "pausing"
  | "paused"
  | "cancelling"
  | "completed"
  | "cancelled";

export type BatchControlIntent = "run" | "pause" | "cancel";
export type BatchPauseReason =
  | "user"
  | "usage_threshold"
  | "usage_unavailable"
  | "quota_limit"
  | "infrastructure_failure"
  | "codex_capacity"
  | "resource_pressure";

export interface UsageSnapshot {
  limit_id: "codex";
  used_percent: number;
  remaining_percent: number;
  window_duration_minutes: number;
  resets_at: string;
  observed_at: string;
  rate_limit_reached_type: string | null;
  plan_type: string | null;
  account_tokens: number | null;
  probe_version: 1;
}

export type AccountUsage =
  | { mode: "api_key"; sufficient: true; usage: null }
  | { mode: "subscription"; sufficient: boolean; usage: UsageSnapshot };

export interface BatchControlState {
  intent: BatchControlIntent;
  usage_pause_percent: number;
  pause_reason: BatchPauseReason | null;
  updated_at: string;
  version: number;
}

export type EstimateQuality = "unavailable" | "preliminary" | "measured";
export type EstimateBasis = "none" | "current_batch" | "historical_compatible";

export interface BatchEstimate {
  quality: EstimateQuality;
  basis: EstimateBasis;
  sample_size: number;
  remaining_tasks: number;
  remaining_tokens: number | null;
  remaining_duration_seconds: number | null;
  low_tokens: number | null;
  high_tokens: number | null;
  low_duration_seconds: number | null;
  high_duration_seconds: number | null;
}

export type PairCategory =
  | "off_fail_on_pass"
  | "off_pass_on_fail"
  | "both_pass"
  | "both_fail"
  | "execution_failure";

export type BatchTaskSet = "swebench-pro-public-v2" | "swebench-pro-stability-v1";

export interface BatchCreate {
  powercontext_ref: string;
  benchmark: "swebench-pro";
  task_set: BatchTaskSet;
  model: string;
  reasoning_effort: "medium";
  treatment_mode: TreatmentMode;
  idempotency_key: string;
  usage_pause_percent: number;
  initial_control_intent: "run" | "pause";
  container_env?: Record<string, string>;
}

export interface BatchRecord {
  batch_id: string;
  request: BatchCreate;
  total_tasks: number;
  status: BatchStatus;
  control: BatchControlState;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  resolved_powercontext_sha: string | null;
}

export interface BatchPreview {
  powercontext_ref: string;
  benchmark: "swebench-pro";
  task_set: BatchTaskSet;
  model: string;
  reasoning_effort: "medium";
  treatment_mode: TreatmentMode;
  total_tasks: number;
  usage_pause_percent: number;
  usage: UsageSnapshot | null;
  estimate: BatchEstimate;
  can_start: boolean;
  block_reason: "usage_threshold_reached" | null;
}

export interface ResolutionAggregate {
  resolved: number;
  total: number;
  rate_percent: number;
}

export interface TokenMetricAggregate {
  off: number | null;
  on: number | null;
  delta: number | null;
  off_measured_tasks: number | null;
  on_measured_tasks: number | null;
}

export interface TokenAggregate {
  input: TokenMetricAggregate;
  output: TokenMetricAggregate;
  total: TokenMetricAggregate;
}

export interface BatchReport {
  batch_id: string;
  treatment_mode: TreatmentMode;
  report_revision: number;
  total_tasks: number;
  terminal_tasks: number;
  comparable_pairs: number | null;
  execution_failures: number;
  cancelled_tasks: number;
  off: ResolutionAggregate | null;
  on: ResolutionAggregate | null;
  resolution_rate_delta_points: number | null;
  pair_categories: Record<PairCategory, number> | null;
  task_statuses: Record<TaskStatus, number>;
  tokens: TokenAggregate;
  control: BatchControlState;
  latest_usage: UsageSnapshot | null;
  estimate: BatchEstimate;
  revisions: Record<string, string>;
  configuration: Record<string, string>;
}

export interface TaskArmSummary {
  resolved: boolean;
  input_tokens: number | null;
  output_tokens: number | null;
  total_tokens: number | null;
}

export interface TaskTokenDelta {
  off: number | null;
  on: number | null;
  delta: number | null;
}

export interface BatchTaskItem {
  task_id: string;
  attempt_id: string | null;
  attempt_number: number;
  attempt_count: number;
  retryable: boolean;
  model: string;
  reasoning_effort: "medium";
  instance_id: string;
  repository: string;
  source_index: number;
  status: TaskStatus;
  pair_category: PairCategory | null;
  off: TaskArmSummary | null;
  on: TaskArmSummary | null;
  tokens: TaskTokenDelta;
  failure_category: string | null;
  failure_summary: string | null;
}

export interface TaskAttempt {
  attempt_id: string;
  task_id: string;
  attempt_number: number;
  status: TaskStatus;
  phase: TaskPhase | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  version: number;
  failure_category: FailureCategory | null;
  failure_phase: TaskPhase | null;
  failure_summary: string | null;
  result: TaskResult | null;
  retryable: boolean;
}

export interface BatchControlEvent {
  sequence: number;
  batch_id: string;
  event_type:
    | "batch_created"
    | "threshold_changed"
    | "pause_requested"
    | "paused"
    | "resume_requested"
    | "resumed"
    | "cancel_requested"
    | "cancelled"
    | "usage_threshold_reached"
    | "usage_unavailable"
    | "quota_limit_reached"
    | "infrastructure_failure"
    | "batch_completed"
    | "resource_pressure"
    | "task_retry_requested";
  actor: "user" | "system";
  details: Record<string, number | string | null>;
  occurred_at: string;
}

export interface BatchRuntimeFailure {
  category: FailureCategory;
  code: string;
  phase: TaskPhase | null;
  summary: string;
  finished_at: string;
}

export interface BatchRuntimeTask {
  task_id: string;
  attempt_id: string;
  instance_id: string;
  source_index: number;
  status: "queued" | "running";
  phase: TaskPhase | null;
  attempt_number: number;
  attempt_count: number;
  created_at: string;
  eligible_at: string;
  started_at: string | null;
  last_failure: BatchRuntimeFailure | null;
}

export interface BatchRuntime {
  batch_id: string;
  generated_at: string;
  status_counts: Record<TaskStatus, number>;
  tasks: BatchRuntimeTask[];
}

export interface BatchTaskPage {
  items: BatchTaskItem[];
  total: number;
  limit: number;
  offset: number;
}

export interface OfficialTestGroup {
  passed: number;
  total: number;
  failed: string[];
}

export interface TaskDetailArm {
  resolved: boolean;
  patch_applied: boolean | null;
  fail_to_pass: OfficialTestGroup;
  pass_to_pass: OfficialTestGroup;
  log_excerpt: string | null;
  input_tokens: number | null;
  output_tokens: number | null;
  total_tokens: number | null;
}

export interface RequiredTests {
  fail_to_pass: string[];
  pass_to_pass: string[];
  selected_test_files_to_run: string;
  test_patch: string;
}

export interface TokensFlowFinalizationSummary {
  state: "pending" | "passed" | "timed_out" | "capacity_evicted" | "cleanup_failed";
  registered_at: string;
  deadline_at: string;
  finished_at: string | null;
  attempts: number;
  queue_passed: boolean;
  doctor_rc: number | null;
  error_category: string | null;
  reason: string | null;
}

export interface BatchTaskDetail {
  task: BatchTaskItem;
  problem_statement: string;
  required_tests: RequiredTests;
  off: TaskDetailArm | null;
  on: TaskDetailArm | null;
  tokensflow_finalization: {
    off: TokensFlowFinalizationSummary | null;
    on: TokensFlowFinalizationSummary | null;
  };
}

export interface ContextEvent {
  sequence: number;
  observed_at: string;
  elapsed_ms: number;
  arm: "off" | "on";
  actor: string;
  event_type: string;
  input: Record<string, unknown> | null;
  output: Record<string, unknown> | null;
  source_artifact: string;
  source_sequence: number;
}

export interface ContextEventPage {
  items: ContextEvent[];
  total: number;
  limit: number;
  offset: number;
}

export interface BatchTaskListOptions {
  category?: PairCategory;
  query?: string;
  sort?: "source" | "token_delta_asc" | "token_delta_desc";
  limit?: number;
  offset?: number;
}

export interface ContextPageOptions {
  limit?: number;
  offset?: number;
  attempt_id?: string;
}

export interface BatchEventSubscription {
  close(): void;
}

export interface BaselineRecord {
  baseline_id: string;
  name: string;
  source_batch_id: string;
  source_arm: Arm;
  source_report_revision: number;
  benchmark: "swebench-pro";
  task_set: BatchTaskSet;
  instance_set_digest: string;
  total_tasks: number;
  resolved_tasks: number;
  execution_failures: number;
  model: string;
  reasoning_effort: "medium";
  dataset_revision: string;
  harness_revision: string;
  powercontext_sha: string | null;
  codex_version: string | null;
  created_at: string;
}

export interface BaselineSelection {
  baseline_id: string;
  current_arm: Arm;
}

export interface BaselineCompatibility {
  status: "compatible" | "warning" | "incompatible";
  reasons: string[];
}

export interface BaselineCandidate {
  baseline: BaselineRecord;
  compatibility: BaselineCompatibility;
}

export interface BaselineComparison {
  baseline: BaselineRecord;
  current_arm: Arm;
  compatibility: BaselineCompatibility;
  coverage: {
    matched_tasks: number;
    comparable_tasks: number;
    current_execution_failures: number;
    baseline_execution_failures: number;
  };
  resolution: {
    baseline_resolved: number;
    current_resolved: number;
    total: number;
    baseline_rate_percent: number;
    current_rate_percent: number;
    delta_points: number;
  };
  outcome_categories: Record<
    "baseline_fail_current_pass" | "baseline_pass_current_fail" | "both_pass" | "both_fail",
    number
  >;
  input_tokens: HistoricalTokenComparison | null;
  output_tokens: HistoricalTokenComparison | null;
  total_tokens: HistoricalTokenComparison | null;
}

export interface HistoricalTokenComparison {
  baseline: number;
  current: number;
  delta: number;
  baseline_measured_tasks: number;
  current_measured_tasks: number;
}

export interface BaselineComparisonResponse {
  batch_id: string;
  report_revision: number;
  comparisons: BaselineComparison[];
}
