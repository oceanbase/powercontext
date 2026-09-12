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
import { Value } from "typebox/value";
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
    const contract = {
      schema: "powercontext.work-contract.v1",
      trust: "untrusted_input",
      objective: "ship it",
      facts: [],
      in_scope: ["Implement the requested change."],
      exclusions: [],
      completion_criteria: ["The change is tested."],
      authorization_notes: [],
      open_questions: [],
    };

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
    const currentWork = {
      schema: "powercontext.current-work-handoff.v1",
      trust: "untrusted_input",
      objective: "ship it",
      state: [{ text: "The implementation is ready.", basis: "declared", evidence: [] }],
      disposition: "continuable",
      next_action: null,
      omissions: [],
    };
    const handoff = {
      schema: "powercontext.prepared-handoff.v1",
      scope_id: "scope-1",
      base: null,
      content: {
        schema: "powercontext.handoff.v1",
        objective: "ship it",
        state: [{
          text: "The implementation is ready.",
          citations: [{ kind: "source", source_ref: { name: "handoff-boundary", source_id: "boundary" } }],
        }],
        disposition: "continuable",
        next_action: null,
        omissions: [],
      },
    };
    const revision = { family: "handoff", artifact_id: "h-1", revision: 1 };
    const checks = { live_state: "confirmed", capability: "confirmed", authorization: "confirmed" };
    const outcome = {
      schema: "powercontext.task-outcome.v1",
      trust: "untrusted_observation",
      objective: "ship it",
      status: "succeeded",
      summary: "The implementation is ready.",
      handoff_receipt_ref: null,
      observations: [{ text: "The implementation is ready.", basis: "declared", evidence: [] }],
      checks: [],
      produced_artifacts: [],
      remaining_work: [],
    };

    await createHandoffCurrentWorkTool(context, deps)!.execute("call-1", { handoff: currentWork, source_id: "boundary" });
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

  it("exposes server-compatible nested schemas", () => {
    const { deps } = fixture();
    const contractTool = createWorkContractTool(context, deps)!;
    const handoffTool = createHandoffCurrentWorkTool(context, deps)!;
    const commitTool = createHandoffCommitTool(context, deps)!;
    const continueTool = createHandoffContinueTool(context, deps)!;
    const acknowledgeTool = createHandoffAcknowledgeTool(context, deps)!;
    const outcomeTool = createTaskOutcomeTool(context, deps)!;
    const prepared = {
      schema: "powercontext.prepared-handoff.v1",
      scope_id: "scope-1",
      base: null,
      content: {
        schema: "powercontext.handoff.v1",
        objective: "ship it",
        state: [{
          text: "The implementation is ready.",
          citations: [{ kind: "source", source_ref: { name: "handoff-boundary", source_id: "boundary" } }],
        }],
        disposition: "continuable",
        next_action: null,
        omissions: [],
      },
    };

    expect(Value.Check(contractTool.parameters, {
      contract: {
        schema: "powercontext.work-contract.v1",
        trust: "untrusted_input",
        objective: "ship it",
        facts: [],
        in_scope: ["Implement the requested change."],
        exclusions: [],
        completion_criteria: ["The change is tested."],
        authorization_notes: [],
        open_questions: [],
      },
    })).toBe(true);
    expect(Value.Check(contractTool.parameters, {
      contract: { schema: "powercontext.work-contract.v1", objective: "ship it" },
    })).toBe(false);
    expect(Value.Check(handoffTool.parameters, {
      handoff: {
        schema: "powercontext.current-work-handoff.v1",
        trust: "untrusted_input",
        objective: "ship it",
        state: [{ text: "The implementation is ready.", basis: "declared", evidence: [] }],
        disposition: "continuable",
        next_action: null,
        omissions: [],
      },
    })).toBe(true);
    expect(Value.Check(handoffTool.parameters, {
      handoff: { schema: "powercontext.prepared-handoff.v1", content: {} },
    })).toBe(false);
    expect(Value.Check(commitTool.parameters, { handoff: prepared })).toBe(true);
    expect(Value.Check(continueTool.parameters, { selection: "prepared", prepared })).toBe(true);
    expect(Value.Check(acknowledgeTool.parameters, {
      receiver: "agent-2",
      status: "accepted",
      selection: "prepared",
      prepared,
      receiver_checks: { live_state: "confirmed", capability: "confirmed", authorization: "confirmed" },
    })).toBe(true);
    expect(Value.Check(outcomeTool.parameters, {
      outcome: {
        schema: "powercontext.task-outcome.v1",
        trust: "untrusted_observation",
        objective: "ship it",
        status: "succeeded",
        summary: "The implementation is ready.",
        handoff_receipt_ref: null,
        observations: [{ text: "The implementation is ready.", basis: "declared", evidence: [] }],
        checks: [],
        produced_artifacts: [],
        remaining_work: [],
      },
    })).toBe(true);
  });
});
