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

import { writeFileSync } from "node:fs";
import { join } from "node:path";
import { expect, it } from "vitest";
import { buildMemoryGuidance } from "./guidance.js";
import { resolvePowerContextConfig } from "./config.js";
import {
  createMemorySearchTool, createMemoryGetTool, createMemoryStoreTool,
  createMemoryReviseTool, createMemoryRetireTool,
} from "./tools.js";

it("mentions only currently available tools, including a write-only catalog", () => {
  const deps = {
    client: {} as never, isPrivateSession: () => true,
    getConfig: () => resolvePowerContextConfig(undefined, { endpoint: "http://powercontext.test" }),
  };
  const context = { agentId: "main", sessionKey: "agent:main:telegram:direct:fixture" };
  const tools = [createMemorySearchTool, createMemoryGetTool, createMemoryStoreTool,
    createMemoryReviseTool, createMemoryRetireTool].map(create => create(context, deps)!);
  expect(buildMemoryGuidance(new Set(), "off")).toEqual([]);
  for (const visible of [tools, ...tools.map(tool => [tool])]) {
    const names = new Set(visible.map(tool => tool.name));
    const guidance = buildMemoryGuidance(names, "off").join("\n");
    const references = new Set(guidance.match(/\bpowercontext_memory_[a-z_]+\b/g));
    expect(references).toEqual(names);
  }
  const output = process.env.POWERCONTEXT_GUIDANCE_EXPORT;
  const readTools = tools.filter(tool => tool.name !== "powercontext_memory_store");
  if (output) writeFileSync(join(output, "openclaw.json"), JSON.stringify({
    host: "openclaw", guidance: buildMemoryGuidance(new Set(tools.map(tool => tool.name)), "on").join("\n"),
    skill: null, tools: tools.map(({ name, description, parameters }) => ({ name, description, parameters })),
    variants: { unavailable_save: {
      guidance: buildMemoryGuidance(new Set(readTools.map(tool => tool.name)), "on").join("\n"),
      tools: readTools.map(({ name, description, parameters }) => ({ name, description, parameters })),
    } },
  }, null, 2));
});
