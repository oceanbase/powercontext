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

import { createHash } from "node:crypto";
import { describe, expect, it } from "vitest";
import { resolvePowerContextConfig } from "./config.js";
import type { PowerContextClient } from "./http.js";
import { resolvePowerContextScope } from "./scope.js";

const digest = (value: string) => createHash("sha256").update(value).digest("hex");

describe("PowerContext Scope binding", () => {
  it("asks the Server to resolve explicit and ordered host bindings", async () => {
    const requests: Array<{ path: string; body: Record<string, unknown> }> = [];
    const client = {
      async post<T>(path: string, body: Record<string, unknown>): Promise<T> {
        requests.push({ path, body });
        return { scope_id: "scp_server_owned" } as T;
      },
    } as unknown as PowerContextClient;
    const config = resolvePowerContextConfig(undefined, { scopeId: "scp_explicit" });

    const scopeId = await resolvePowerContextScope(client, config, {
      agentId: "main",
      sessionKey: "agent:main:telegram:direct:user-1",
      activeProjectKeys: ["/workspace/primary", " /workspace/primary ", "/workspace/secondary"],
    });

    expect(scopeId).toBe("scp_server_owned");
    expect(requests).toEqual([
      {
        path: "/v1/scope-bindings/resolve",
        body: {
          explicit_scope_id: "scp_explicit",
          binding_keys: [
            {
              integration: "openclaw",
              kind: "session",
              external_id: digest("agent:main:telegram:direct:user-1"),
            },
            {
              integration: "openclaw",
              kind: "project",
              external_id: digest("/workspace/primary"),
            },
            {
              integration: "openclaw",
              kind: "project",
              external_id: digest("/workspace/secondary"),
            },
            { integration: "openclaw", kind: "agent", external_id: "main" },
          ],
        },
      },
    ]);
  });

  it("rejects an invalid Server binding response", async () => {
    const client = {
      async post<T>(): Promise<T> {
        return { scope_id: " " } as T;
      },
    } as unknown as PowerContextClient;

    await expect(
      resolvePowerContextScope(client, resolvePowerContextConfig(undefined), { agentId: "main" }),
    ).rejects.toThrow("invalid Scope binding");
  });
});
