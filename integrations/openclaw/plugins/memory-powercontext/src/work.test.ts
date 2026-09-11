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
  createHandoffAcknowledgeTool,
  createHandoffCommitTool,
  createHandoffContinueTool,
  createHandoffCurrentWorkTool,
  createTaskOutcomeTool,
  createWorkContractTool,
} from "./work.js";

describe("PowerContext work tools", () => {
  const context = {
    agentId: "main",
    sessionKey: "agent:main:webchat:direct:user-1",
    sessionId: "session-1",
  } as OpenClawPluginToolContext;

  function fixture() {
    const requests: Array<{ path: string; body: Record<string, unknown> }> = [];
    const client = {
      async post<T>(path: string, body: Record<string, unknown>): Promise<T> {
        requests.push({ path, body });
        if (path === "/v1/scope-bindings/resolve") return { scope_id: "scope-1" } as T;
        return { accepted: true } as T;
      },
    } as unknown as PowerContextClient;
    const deps = {
      client,
      getConfig: () => resolvePowerContextConfig(undefined, {
        endpoint: "https://powercontext.test",
        scopeId: "scope-1",
      }),
      isPrivateSession: () => true,
    };
    return { deps, requests };
  }

  it("sends a Work Contract to the dedicated endpoint", async () => {
    const { deps, requests } = fixture();
    const contract = { schema: "powercontext.work-contract.v1", objective: "ship it" };

    await createWorkContractTool(context, deps)!.execute("call-1", {
      contract,
      source_id: "contract-source",
    });

    expect(requests[1]).toEqual({
      path: "/v1/work/contracts/create",
      body: { scope_id: "scope-1", source_id: "contract-source", contract },
    });
  });

  it("covers prepare, commit, continue, acknowledge, and outcome", async () => {
    const { deps, requests } = fixture();
    const handoff = { schema: "powercontext.prepared-handoff.v1", content: {} };
    const revision = { family: "handoff", artifact_id: "h-1", revision: 1 };
    const checks = { live_state: "confirmed", capability: "confirmed", authorization: "confirmed" };
    const outcome = { schema: "powercontext.task-outcome.v1", status: "succeeded" };

    await createHandoffCurrentWorkTool(context, deps)!.execute("call-1", { handoff, source_id: "boundary" });
    await createHandoffCommitTool(context, deps)!.execute("call-2", { handoff });
    await createHandoffContinueTool(context, deps)!.execute("call-3", { selection: "exact", revision });
    await createHandoffAcknowledgeTool(context, deps)!.execute("call-4", {
      receiver: "agent-2",
      status: "accepted",
      selection: "exact",
      revision,
      receiver_checks: checks,
      source_id: "receipt",
    });
    await createTaskOutcomeTool(context, deps)!.execute("call-5", { outcome, source_id: "outcome" });

    expect(requests.filter(({ path }) => path !== "/v1/scope-bindings/resolve").map(({ path }) => path)).toEqual([
      "/v1/work/handoffs/prepare-current",
      "/v1/handoff/commit",
      "/v1/handoff/continue",
      "/v1/work/handoffs/acknowledge",
      "/v1/work/outcomes/record",
    ]);
    const acknowledgeRequest = requests.filter(({ path }) => path === "/v1/work/handoffs/acknowledge")[0];
    expect(acknowledgeRequest?.body).toMatchObject({
      scope_id: "scope-1",
      source_id: "receipt",
      receiver: "agent-2",
      status: "accepted",
      selection: "exact",
      revision,
      receiver_checks: checks,
    });
  });

  it("does not expose work tools outside private sessions", () => {
    const { deps } = fixture();
    const publicDeps = { ...deps, isPrivateSession: () => false };

    expect(createWorkContractTool(context, publicDeps)).toBeNull();
    expect(createHandoffCurrentWorkTool(context, publicDeps)).toBeNull();
    expect(createHandoffCommitTool(context, publicDeps)).toBeNull();
    expect(createHandoffContinueTool(context, publicDeps)).toBeNull();
    expect(createHandoffAcknowledgeTool(context, publicDeps)).toBeNull();
    expect(createTaskOutcomeTool(context, publicDeps)).toBeNull();
  });
});
