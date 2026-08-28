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
  MemoryProviderStatus,
  MemoryReadResult,
  MemorySearchManager,
  MemorySearchResult,
} from "openclaw/plugin-sdk/memory-core-host-engine-storage";
import type { PowerContextConfig } from "./config.js";
import { resolvePowerContextScope } from "./config.js";
import type { PowerContextClient } from "./http.js";
import {
  decodeCitation,
  encodeCitation,
  isMemoryCitation,
  type MemoryEntry,
  type SearchMemoryResponse,
} from "./types.js";

const DEFAULT_MEMORY_READ_LINES = 120;
const DEFAULT_MEMORY_READ_MAX_CHARS = 12_000;

export class PowerContextMemoryManager implements MemorySearchManager {
  private readonly citationScopes = new Map<string, string>();

  constructor(
    private readonly agentId: string,
    private readonly getConfig: () => PowerContextConfig,
    private readonly client: PowerContextClient,
    private readonly isPrivateSession: (agentId: string, sessionKey: string | undefined) => boolean,
    private readonly fallbackScopeId?: string,
  ) {}

  async search(
    query: string,
    opts?: {
      maxResults?: number;
      minScore?: number;
      sessionKey?: string;
      lexicalOnly?: boolean;
      activeProjectKeys?: string[];
      sources?: Array<"memory" | "sessions">;
      signal?: AbortSignal;
    },
  ): Promise<MemorySearchResult[]> {
    if (
      !this.isPrivateSession(this.agentId, opts?.sessionKey) ||
      (opts?.sources && !opts.sources.includes("memory"))
    ) {
      return [];
    }
    const config = this.getConfig();
    const scopeId = resolvePowerContextScope(this.agentId, config, opts?.activeProjectKeys);
    const result = await this.client.post<SearchMemoryResponse>(
      "/v1/memory/search",
      {
        scope_id: scopeId,
        query: query.slice(0, 8192),
        limit: Math.min(50, Math.max(1, opts?.maxResults ?? 10)),
        mode: opts?.lexicalOnly ? "fts" : "auto",
      },
      opts?.signal,
    );
    if (!Array.isArray(result.hits)) {
      throw new Error("PowerContext memory search returned an invalid hits payload");
    }
    const minScore = opts?.minScore ?? 0;
    return result.hits
      .filter(
        (hit) =>
          hit &&
          typeof hit.text === "string" &&
          typeof hit.score === "number" &&
          Number.isFinite(hit.score) &&
          hit.score >= 0 &&
          hit.score <= 1 &&
          isMemoryCitation(hit.citation),
      )
      .filter((hit) => hit.score >= minScore)
      .map((hit) => {
        const citation = encodeCitation(hit.citation);
        this.citationScopes.delete(citation);
        this.citationScopes.set(citation, scopeId);
        if (this.citationScopes.size > 1000) {
          const oldest = this.citationScopes.keys().next().value;
          if (oldest) {
            this.citationScopes.delete(oldest);
          }
        }
        return {
          path: citation,
          startLine: 1,
          endLine: Math.max(1, hit.text.split("\n").length),
          score: hit.score,
          snippet: hit.text,
          source: "memory" as const,
          citation,
          originClass: "untrusted",
        };
      });
  }

  async readFile(params: { relPath: string; from?: number; lines?: number; scopeId?: string }): Promise<MemoryReadResult> {
    const citation = decodeCitation(params.relPath);
    const config = this.getConfig();
    const scopeId =
      params.scopeId ??
      this.citationScopes.get(params.relPath) ??
      this.fallbackScopeId ??
      (config.scopeMode === "agent"
        ? resolvePowerContextScope(this.agentId, config)
        : undefined);
    if (!scopeId) {
      throw new Error(
        "PowerContext project citation is not bound to this manager; run memory_search again",
      );
    }
    const entry = await this.client.post<MemoryEntry>("/v1/memory/entries/get", {
      scope_id: scopeId,
      citation,
    });
    const allLines = entry.text.split("\n");
    if (allLines.at(-1) === "") {
      allLines.pop();
    }
    const from = Math.max(1, params.from ?? 1);
    const count = Math.max(1, params.lines ?? DEFAULT_MEMORY_READ_LINES);
    const selected = allLines.slice(from - 1, from - 1 + count);
    let includedLines = selected.length;
    let text = selected.join("\n");
    while (includedLines > 1 && text.length > DEFAULT_MEMORY_READ_MAX_CHARS) {
      includedLines -= 1;
      text = selected.slice(0, includedLines).join("\n");
    }
    const hardTruncated = text.length > DEFAULT_MEMORY_READ_MAX_CHARS;
    if (hardTruncated) {
      text = text.slice(0, DEFAULT_MEMORY_READ_MAX_CHARS);
    }
    const moreSourceLinesRemain = from - 1 + selected.length < allLines.length;
    const truncated = hardTruncated || includedLines < selected.length || moreSourceLinesRemain;
    const nextFrom =
      !hardTruncated && truncated ? from + includedLines : undefined;
    if (truncated && (text || hardTruncated)) {
      text += nextFrom
        ? `\n\n[More content available. Use from=${nextFrom} to continue.]`
        : "\n\n[More content available. Requested excerpt exceeded the default maxChars budget.]";
    }
    return {
      text,
      path: params.relPath,
      from,
      lines: includedLines,
      ...(truncated ? { truncated: true } : {}),
      ...(nextFrom !== undefined ? { nextFrom } : {}),
    };
  }

  status(): MemoryProviderStatus {
    const config = this.getConfig();
    return {
      backend: "builtin",
      provider: "powercontext",
      dirty: false,
      sources: ["memory"],
      custom: {
        configured: Boolean(config.endpoint),
        scopeMode: config.scopeMode,
      },
    };
  }

  async probeEmbeddingAvailability() {
    try {
      await this.client.get("/health/ready");
      return { ok: true, checked: true, cached: false };
    } catch (error) {
      return {
        ok: false,
        checked: true,
        cached: false,
        error: error instanceof Error ? error.message : String(error),
      };
    }
  }

  async probeVectorAvailability() {
    return (await this.probeEmbeddingAvailability()).ok;
  }
}
