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


import type { OpenClawPluginToolContext } from "openclaw/plugin-sdk/plugin-entry";
import { describe, expect, it } from "vitest";
import { resolvePowerContextConfig } from "./config.js";
import type { PowerContextClient } from "./http.js";
import {
  createMemoryGetTool,
  createMemoryRetireTool,
  createMemoryReviseTool,
  createMemorySearchTool,
  createMemoryStoreTool,
  POWERCONTEXT_MEMORY_GET_TOOL,
  POWERCONTEXT_MEMORY_RETIRE_TOOL,
  POWERCONTEXT_MEMORY_REVISE_TOOL,
  POWERCONTEXT_MEMORY_SEARCH_TOOL,
  POWERCONTEXT_MEMORY_STORE_TOOL,
} from "./tools.js";

describe("PowerContext tools", () => {
  it("uses PowerContext-prefixed names for search and read tools", () => {
    const client = {} as unknown as PowerContextClient;
    const context = {
      agentId: "main",
      sessionKey: "agent:main:telegram:direct:user-1",
    } as OpenClawPluginToolContext;
    const deps = {
      client,
      getConfig: () => resolvePowerContextConfig(undefined, { endpoint: "http://powercontext.test" }),
      isPrivateSession: () => true,
    };

    expect([
      createMemorySearchTool(context, deps)?.name,
      createMemoryGetTool(context, deps)?.name,
    ]).toEqual([POWERCONTEXT_MEMORY_SEARCH_TOOL, POWERCONTEXT_MEMORY_GET_TOOL]);
  });

  it("uses PowerContext-prefixed names for mutation tools", () => {
    const client = {} as unknown as PowerContextClient;
    const context = {
      agentId: "main",
      sessionKey: "agent:main:telegram:direct:user-1",
    } as OpenClawPluginToolContext;
    const deps = {
      client,
      getConfig: () => resolvePowerContextConfig(undefined, { endpoint: "http://powercontext.test" }),
      isPrivateSession: () => true,
    };

    expect([
      createMemoryStoreTool(context, deps)?.name,
      createMemoryReviseTool(context, deps)?.name,
      createMemoryRetireTool(context, deps)?.name,
    ]).toEqual([
      POWERCONTEXT_MEMORY_STORE_TOOL,
      POWERCONTEXT_MEMORY_REVISE_TOOL,
      POWERCONTEXT_MEMORY_RETIRE_TOOL,
    ]);
  });

  it("shares the manager between PowerContext search and get tools", () => {
    const client = {} as unknown as PowerContextClient;
    const context = {
      agentId: "main",
      sessionKey: "agent:main:telegram:direct:user-1",
    } as OpenClawPluginToolContext;
    const manager = {} as never;
    const deps = {
      client,
      getConfig: () => resolvePowerContextConfig(undefined, { endpoint: "http://powercontext.test" }),
      isPrivateSession: () => true,
      managerFor: () => manager,
    };

    expect(createMemorySearchTool(context, deps)).toBeTruthy();
    expect(createMemoryGetTool(context, deps)).toBeTruthy();
  });

  it("fails closed for the unsupported wiki corpus", async () => {
    let requests = 0;
    const client = {
      async post() {
        requests += 1;
        throw new Error("unexpected request");
      },
    } as unknown as PowerContextClient;
    const context = {
      agentId: "main",
      sessionKey: "agent:main:telegram:direct:user-1",
    } as OpenClawPluginToolContext;
    const deps = {
      client,
      getConfig: () => resolvePowerContextConfig(undefined, { endpoint: "http://powercontext.test" }),
      isPrivateSession: () => true,
    };
    const tool = createMemorySearchTool(context, deps);

    const result = await tool!.execute("call-1", { query: "hello", corpus: "wiki" });

    expect(result.details).toMatchObject({
      disabled: true,
      unavailable: true,
      error: "PowerContext does not provide the wiki corpus",
    });
    expect(requests).toBe(0);
  });

  it("returns a structured error when memory_get parameters are invalid", async () => {
    const client = {} as unknown as PowerContextClient;
    const context = {
      agentId: "main",
      sessionKey: "agent:main:telegram:direct:user-1",
    } as OpenClawPluginToolContext;
    const deps = {
      client,
      getConfig: () => resolvePowerContextConfig(undefined, { endpoint: "http://powercontext.test" }),
      isPrivateSession: () => true,
    };
    const tool = createMemoryGetTool(context, deps);

    const result = await tool!.execute("call-1", {});

    expect(result.details).toMatchObject({ path: "", unavailable: true });
  });
});
