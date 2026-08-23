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

describe("PowerContext scope resolution", () => {
  it("isolates agents without using a raw session key", () => {
    const config = resolvePowerContextConfig(undefined, { scopeMode: "agent" });
    expect(resolvePowerContextScope("Research Agent", config)).toBe("openclaw:agent:Research%20Agent");
  });

  it("uses a stable opaque project identity only for one trusted project", () => {
    const config = resolvePowerContextConfig(undefined, { scopeMode: "project" });
    const first = resolvePowerContextScope("main", config, ["/workspace/project"]);
    const second = resolvePowerContextScope("main", config, ["/workspace/project"]);
    expect(first).toBe(second);
    expect(first).toMatch(/^openclaw:agent:main:project:[0-9a-f]{32}$/u);
    expect(resolvePowerContextScope("main", config, ["/a", "/b"])).toBe("openclaw:agent:main");
  });
});
