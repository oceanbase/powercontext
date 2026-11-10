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

import * as react0 from "react";
import { Context } from "@deepseek-ai/cordis";

//#region src/status-contract.d.ts

/**
 * Wire contract between the plugin Host status bridge and its browser entry.
 *
 * The Host owns every field below. The browser parser reconstructs exactly the
 * documented fields instead of spreading the response, so an accidentally added
 * credential, path, or provider payload cannot reach the diagnostics panel.
 */
declare const STATUS_SCHEMA = "powercontext.dsh-status.v1";
type BridgeConnectionState = 'unconfigured' | 'connecting' | 'ready' | 'failed';
type DeploymentMode = 'desktop' | 'owner_local_web';
type OwnerLocalSource = 'automatic' | 'explicit';
type DisplayState = 'unconfigured' | 'connecting' | 'stale' | 'ready' | 'failed';
type ProbeState = 'ok' | 'failed' | 'unknown';
type StageState = 'not_yet_observed' | 'running' | 'resolved' | 'ready' | 'empty' | 'accepted' | 'completed' | 'incomplete' | 'appended' | 'skipped' | 'unavailable';
interface StatusProbePayload {
  state: ProbeState;
  code?: string;
  message?: string;
  recovery?: string;
  http_status?: number;
  request_id?: string;
  observed_at: string | null;
  age_ms: number | null;
  dependencies?: Record<string, string>;
}
interface StatusStagePayload {
  operation: string;
  state: StageState;
  code?: string;
  message?: string;
  recovery?: string;
  http_status?: number;
  request_id?: string;
  protocol_issue?: string;
  content_bytes?: number;
  confirmation?: 'rejected' | 'unconfirmed';
  observed_at: string | null;
  age_ms: number | null;
}
interface StatusRuntimePayload {
  observation: string;
  freshness: 'not_yet_observed' | 'current' | 'stale';
  stale_reason?: string;
  reason?: string;
  attempt: number | null;
  turn: string | null;
  started_at: string | null;
  age_ms: number | null;
  stale_after_ms: number;
  observed_scope: string | null;
  stages: Record<string, StatusStagePayload>;
  coverage: string;
}
interface StatusSessionPayload {
  id: string;
  scope_id: string | null;
  scope_state: 'resolved' | 'unresolved' | 'not_attempted';
  scope_code?: string;
  scope_message?: string;
  cwd?: string;
}
interface StatusConfigurationPayload {
  endpoint_origin: string;
  endpoint_source: string;
  path_prefix: boolean;
  authorization_configured: boolean;
  authorization_source: string;
  scope_source: string;
  request_timeout_ms: number;
}
interface StatusCapabilitiesPayload {
  prepared_context: boolean;
  context_versions: string[];
  memory_extraction: boolean | null;
  handoff_generation: boolean | null;
}
interface StatusPayload {
  schema: typeof STATUS_SCHEMA;
  observed_at: string;
  plugin_version: string;
  deployment: {
    kind: 'local';
    mode: DeploymentMode;
    owner_local: OwnerLocalSource;
    ssh: false;
    bridge: 'available';
    client: 'packaged';
  };
  configuration: StatusConfigurationPayload;
  connection: {
    state: BridgeConnectionState;
    observed_at: string | null;
    age_ms: number | null;
    stale: boolean;
    stale_after_ms: number;
    generation: number;
    in_flight: boolean;
    code?: string;
    message?: string;
    recovery?: string;
    http_status?: number;
  };
  session: StatusSessionPayload | null;
  runtime: StatusRuntimePayload;
  server: {
    package_version?: string | null;
    liveness: StatusProbePayload;
    readiness: StatusProbePayload;
    capabilities: StatusProbePayload;
  };
  capabilities: StatusCapabilitiesPayload;
  notices: string[];
}
//#endregion
//#region src/ui/status-controller.d.ts
type FetchLike = (input: string | URL, init?: RequestInit) => Promise<Response>;
interface StatusError {
  kind: 'authentication' | 'forbidden' | 'bridge' | 'network' | 'protocol' | 'unavailable';
  code: string;
  message: string;
  recovery?: string;
  http_status?: number;
}
interface StatusSnapshot {
  /** Display state derived at the last publication. */
  state: DisplayState;
  /** A request is in flight; existing data stays visible. */
  loading: boolean;
  data: StatusPayload | null;
  error: StatusError | null;
  last_success_at: number | null;
  session_id: string | null;
  generation: number;
}
interface StatusControllerOptions {
  fetch?: FetchLike;
  now?: () => number;
  document?: Pick<Document, 'visibilityState' | 'addEventListener' | 'removeEventListener'> | null;
  setTimeout?: typeof setTimeout;
  clearTimeout?: typeof clearTimeout;
  /** Prefix for the status route; tests use an absolute origin, the shipped UI uses ''. */
  baseUrl?: string;
}
/**
 * Visibility-aware status refresher. It owns one request at a time, invalidates
 * responses when the Session or a manual refresh supersedes them, pauses while
 * hidden, and backs off after failures. It never reads `/pc doctor`.
 */
declare class StatusController {
  private readonly fetcher;
  private readonly now;
  private readonly visibility;
  private readonly setTimer;
  private readonly clearTimer;
  private readonly baseUrl;
  private readonly listeners;
  private snapshot;
  private started;
  private visible;
  private inFlight;
  private retry;
  private generation;
  private timer;
  private abort;
  constructor(options?: StatusControllerOptions);
  readonly getSnapshot: () => StatusSnapshot;
  readonly subscribe: (listener: () => void) => (() => void);
  /** Attach visibility tracking and make the first bounded request. */
  start(): void;
  /** Detach timers and listeners; retained data stays available until the next mount. */
  stop(): void;
  /** Release everything; called from the plugin effect's disposer. */
  dispose(): void;
  /**
   * Select the active conversation. A changed identity invalidates old data and
   * supersedes any response already in flight.
   */
  setSession(sessionId: string | undefined): void;
  /** Manual refresh: abandon an in-flight read so the user's action is not queued behind it. */
  refreshNow(): void;
  private readonly onVisibility;
  private refresh;
  private fail;
  private schedule;
  private cancelScheduled;
  private publish;
}
//#endregion
//#region src/ui/client.d.ts
interface SessionRow {
  id?: string;
  retainedBy?: {
    mainView?: number;
  };
}
interface StatusFace {
  hooks: {
    status: StatusController;
  };
  setSession: (sessionId: string | undefined) => void;
  refresh: () => void;
}
interface FooterActionProps {
  wide: boolean;
  useSessions: <T>(selector: (state: {
    byId: Record<string, SessionRow>;
  }) => T) => T;
}
type StatusProps = FooterActionProps & StatusFace & {
  useStatus: <T>(selector: (snapshot: StatusSnapshot) => T) => T;
};
interface SlotsService {
  inject(key: 'sidebar.footer.action', callback: () => () => void): void;
  register(options: {
    name: 'sidebar.footer.action';
    id: string;
    order: number;
    inject?: () => StatusFace;
  }, component: (props: StatusProps) => ReturnType<typeof PowerContextStatus>): () => void;
}
declare module '@deepseek-ai/cordis' {
  interface Context {
    slots: SlotsService;
  }
}
declare const inject: string[];
/**
 * The sidebar footer action and its non-modal diagnostics panel. All status
 * values come from the authenticated Host bridge; the component never reads a
 * credential, calls a model, or invokes `/pc doctor`.
 */
declare function PowerContextStatus(props: StatusProps): react0.JSX.Element;
/**
 * Register the optional sidebar entry. The sidebar slot owner controls when the
 * component mounts; unloading this plugin disposes the slot cell, styles, and
 * the controller's timers.
 */
declare function apply(ctx: Context): void;
//#endregion
export { PowerContextStatus, apply, inject };
