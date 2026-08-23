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


import { describe, expect, it } from "vitest";
import { resolvePowerContextConfig, resolvePowerContextScope } from "./config.js";
import type { PowerContextClient } from "./http.js";
import { PowerContextMemoryManager } from "./manager.js";
import { encodeCitation, type MemoryCitation } from "./types.js";

const citation: MemoryCitation = {
  memory_ref: { family: "memory", artifact_id: "artifact-1", revision: 1 },
  entry_id: "entry-1",
  entry_version_id: "version-1",
};

describe("PowerContext memory manager", () => {
  it("keeps memory_get excerpts within the OpenClaw default budget", async () => {
    const client = {
      async post() {
        return {
          citation,
          version: 1,
          kind: "fact",
          text: Array.from({ length: 130 }, (_, index) => `line-${index + 1}`).join("\n"),
          state: "active",
        };
      },
    } as unknown as PowerContextClient;
    const manager = new PowerContextMemoryManager(
      "main",
      () => resolvePowerContextConfig(undefined, { endpoint: "http://powercontext.test" }),
      client,
      () => true,
    );

    const result = await manager.readFile({ relPath: encodeCitation(citation) });

    expect(result.lines).toBe(120);
    expect(result.truncated).toBe(true);
    expect(result.nextFrom).toBe(121);
    expect(result.text).toContain("line-120");
    expect(result.text).not.toContain("line-121");
  });

  it("reads citations in the current project scope rather than a cached search scope", async () => {
    const postBodies: Array<Record<string, unknown>> = [];
    const client = {
      async post<T>(path: string, body: Record<string, unknown>): Promise<T> {
        postBodies.push(body);
        if (path === "/v1/memory/search") {
          return {
            memory: citation.memory_ref,
            mode: "fts",
            hits: [{ citation, text: "remembered fact", score: 1, matched_by: ["fts"] }],
          } as T;
        }
        return { citation, version: 1, kind: "fact", text: "remembered fact", state: "active" } as T;
      },
    } as unknown as PowerContextClient;
    const config = resolvePowerContextConfig(undefined, {
      endpoint: "http://powercontext.test",
      scopeMode: "project",
    });
    const manager = new PowerContextMemoryManager("main", () => config, client, () => true);
    const projectA = ["/workspace/project-a"];
    const projectB = ["/workspace/project-b"];

    const [result] = await manager.search("fact", {
      sessionKey: "agent:main:telegram:direct:user-1",
      activeProjectKeys: projectA,
    });
    await manager.readFile({
      relPath: result.path,
      scopeId: resolvePowerContextScope("main", config, projectB),
    });

    expect(postBodies.at(-1)).toMatchObject({
      scope_id: resolvePowerContextScope("main", config, projectB),
    });
  });
});
