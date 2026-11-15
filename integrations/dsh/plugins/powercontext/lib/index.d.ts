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

import { Context } from "@deepseek-ai/cordis";

//#region src/config.d.ts
interface PluginConfig {
  contextAssembly?: Record<string, unknown>;
  baseUrl?: string;
  allowInsecureHttp?: boolean;
  authorization?: string;
  scopeId?: string;
  timeoutMs?: number;
  requestTimeoutMs?: number;
  maxBytes?: number;
  capturePrompts?: boolean;
  flushOnCapture?: boolean;
  flushMaxCalls?: number;
  /** Explicit owner assertion for an otherwise unverified local Web host. */
  ownerLocal?: boolean;
  updateChannel?: 'stable' | 'preview';
  updateHold?: boolean;
}
//#endregion
//#region src/companion-memory.d.ts
declare const COMPANION_MEMORY_SERVICE = "powercontextCompanionMemory";
declare const COMPANION_MEMORY_PROTOCOL = "powercontext.companion-memory.v1";
interface CompanionMemoryRequest {
  sessionId: string;
  /** Omit to inspect the binding without retrieving memory. */
  query?: string;
  maxBytes?: number;
  signal?: AbortSignal;
}
type CompanionMemoryResult = {
  status: 'connected' | 'ready' | 'empty';
  /** Opaque connection + workspace + Scope identity; safe for local history partitioning. */
  bindingId: string;
  scopeId: string;
  workspace: string;
  content?: string;
} | {
  status: 'unavailable';
  reason: 'session-required' | 'workspace-unavailable' | 'scope-unavailable' | 'connection-unavailable';
};
interface CompanionMemoryService {
  readonly protocol: typeof COMPANION_MEMORY_PROTOCOL;
  prepare(request: CompanionMemoryRequest): Promise<CompanionMemoryResult>;
}
//#endregion
//#region src/index.d.ts
declare const name = "powercontext-dsh";
declare const inject: string[];
interface Config extends PluginConfig {}
declare const Config: {
  '~standard': {
    version: 1;
    vendor: string;
    validate(value: unknown): {
      value: PluginConfig;
      issues?: undefined;
    } | {
      issues: {
        message: string;
      }[];
      value?: undefined;
    };
  };
};
declare function apply(ctx: Context, config: Config): Promise<void>;
//#endregion
export { COMPANION_MEMORY_PROTOCOL, COMPANION_MEMORY_SERVICE, type CompanionMemoryRequest, type CompanionMemoryResult, type CompanionMemoryService, Config, apply, inject, name };
