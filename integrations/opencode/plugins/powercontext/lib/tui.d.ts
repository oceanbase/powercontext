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
import { TuiPlugin } from "@opencode-ai/plugin/tui";

//#region src/management.d.ts
interface ManagementReport extends Record<string, unknown> {
  ok: boolean;
  status: string;
  checks: Record<string, unknown>;
}
//#endregion
//#region src/client.d.ts
type JsonObject = Record<string, unknown>;
type ClientSuccess = {
  kind: 'json';
  value: unknown;
  status: number;
  requestId: string | undefined;
  etag?: string;
} | {
  kind: 'text';
  value: string;
  status: number;
  requestId: string | undefined;
  etag?: string;
} | {
  kind: 'bytes';
  value: Uint8Array;
  status: number;
  requestId: string | undefined;
  etag?: string;
};
interface ClientOptions {
  baseUrl: string;
  allowInsecureHttp?: boolean;
  authorization?: string;
  requestTimeoutMs: number;
  startupTimeoutMs?: number;
  worker?: {
    command: string;
    args: string[];
  };
}
declare class PowerContextClient {
  protected readonly options: ClientOptions;
  private readonly worker;
  constructor(options: ClientOptions);
  request(id: string, payload?: JsonObject, signal?: AbortSignal, options?: {
    readinessResponse?: boolean;
  } | number): Promise<ClientSuccess>;
  doctor(signal?: AbortSignal): Promise<ManagementReport>;
  close(): void;
}
//#endregion
//#region src/config.d.ts
interface ResolvedConfig {
  contextAssembly?: Record<string, unknown>;
  baseUrl: string;
  allowInsecureHttp: boolean;
  scopeId: string | undefined;
  authorization: string | undefined;
  capturePrompts: boolean;
  requestTimeoutMs: number;
  httpBudgetMs: number;
  maxBytes: number;
  flushOnCapture: boolean;
  flushMaxCalls: number;
}
//#endregion
//#region src/commands.d.ts
interface PcCommandRuntime {
  client: PowerContextClient;
  config: ResolvedConfig;
}
//#endregion
//#region src/tui.d.ts
type StatuslineState = {
  connected: boolean;
  label: string;
  todayReduction?: number;
  monthReduction?: number;
};
declare function formatPowerContextStatus(today: unknown, month: unknown): string;
type FailureOutcome = 'authentication_failed' | 'version_mismatch' | 'server_unavailable' | 'invalid_response';
declare function failureLabel(outcome: FailureOutcome): string;
declare function withTimeout<Value>(promise: Promise<Value>, timeoutMs: number): Promise<Value>;
declare function loadStatuslineStatus(runtime: PcCommandRuntime, sessionID: string, cwd: string | undefined, signal?: AbortSignal): Promise<StatuslineState>;
declare const PowerContextTuiPlugin: TuiPlugin;
declare const plugin: {
  id: string;
  tui: TuiPlugin;
};
//#endregion
export { FailureOutcome, PowerContextTuiPlugin, plugin as default, failureLabel, formatPowerContextStatus, loadStatuslineStatus, withTimeout };
