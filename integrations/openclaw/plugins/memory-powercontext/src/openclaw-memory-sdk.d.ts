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


// OpenClaw 2026.8.1-beta.2 exports these runtime modules but omits their
// declarations from its npm package. Keep this narrow compatibility surface
// until the host publishes the corresponding SDK declarations.
declare module "openclaw/plugin-sdk/memory-core-host-engine-storage" {
  export type MemorySource = "memory" | "sessions";

  export type MemorySearchResult = {
    path: string;
    startLine: number;
    endLine: number;
    score: number;
    snippet: string;
    source: MemorySource;
    citation?: string;
    originClass?: string;
  };

  export type MemoryReadResult = {
    text: string;
    path: string;
    truncated?: boolean;
    from?: number;
    lines?: number;
    nextFrom?: number;
  };

  export type MemoryProviderStatus = {
    backend: "builtin";
    provider: string;
    dirty?: boolean;
    sources?: MemorySource[];
    custom?: Record<string, unknown>;
  };

  export interface MemorySearchManager {
    search(
      query: string,
      opts?: {
        maxResults?: number;
        minScore?: number;
        sessionKey?: string;
        lexicalOnly?: boolean;
        activeProjectKeys?: string[];
        sources?: MemorySource[];
        signal?: AbortSignal;
      },
    ): Promise<MemorySearchResult[]>;
    readFile(params: { relPath: string; from?: number; lines?: number }): Promise<MemoryReadResult>;
    status(): MemoryProviderStatus;
    probeEmbeddingAvailability(): Promise<{ ok: boolean; checked?: boolean; cached?: boolean; error?: string }>;
    probeVectorAvailability(): Promise<boolean>;
  }
}

declare module "openclaw/plugin-sdk/memory-core-host-runtime-core" {
  import type { OpenClawConfig } from "openclaw/plugin-sdk/plugin-entry";
  import type {
    MemorySearchManager,
    MemorySearchResult,
  } from "openclaw/plugin-sdk/memory-core-host-engine-storage";

  export type MemoryPluginRuntime = {
    getMemorySearchManager(params: {
      cfg: OpenClawConfig;
      agentId: string;
      purpose?: "default" | "status" | "cli";
    }): Promise<{
      manager: MemorySearchManager | null;
      debug?: { backend?: "builtin"; purpose?: "default" | "status" | "cli"; managerMs?: number };
      error?: string;
    }>;
    resolveMemoryBackendConfig(params: { cfg: OpenClawConfig; agentId: string }): { backend: "builtin" };
    authorizeSearchHits?(params: {
      cfg: OpenClawConfig;
      agentId: string;
      requesterSessionKey: string | undefined;
      sandboxed: boolean;
      hits: MemorySearchResult[];
    }): Promise<MemorySearchResult[]>;
    closeMemorySearchManager?(params: { cfg: OpenClawConfig; agentId: string }): Promise<void>;
    closeAllMemorySearchManagers?(): Promise<void>;
  };

  export function asToolParamsRecord(value: unknown): Record<string, unknown>;
  export function jsonResult<T>(value: T): {
    content: Array<{ type: "text"; text: string }>;
    details: T;
  };
  export function readStringParam(
    params: Record<string, unknown>,
    key: string,
    options: { required: true },
  ): string;
  export function readStringParam(
    params: Record<string, unknown>,
    key: string,
    options?: { required?: false },
  ): string | undefined;
  export function readPositiveIntegerParam(params: Record<string, unknown>, key: string): number | undefined;
  export function readFiniteNumberParam(
    params: Record<string, unknown>,
    key: string,
    options?: { min?: number; max?: number },
  ): number | undefined;
}
