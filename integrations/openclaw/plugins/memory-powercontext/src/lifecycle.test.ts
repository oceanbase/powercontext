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


import { readFileSync } from "node:fs";
import type { OpenClawPluginApi } from "openclaw/plugin-sdk/plugin-entry";
import { initializeGlobalHookRunner, resetGlobalHookRunner } from "openclaw/plugin-sdk/hook-runtime";
import { runAgentHarnessBeforeCompactionHook } from "openclaw/plugin-sdk/agent-harness-runtime";
import { afterEach, describe, expect, it, vi } from "vitest";
import { resolvePowerContextConfig } from "./config.js";
import { PowerContextRequestError, type PowerContextClient } from "./http.js";
import { registerPowerContextLifecycle } from "./lifecycle.js";
import { scopeBindingKeys, type ScopeBindingKey } from "./scope.js";

type Hook = (event: unknown, context: unknown) => unknown;
type ConformanceCase = {
  id: string;
  modes: string[];
  input: Record<string, any>;
  expected: Record<string, any>;
};

const CONFORMANCE_CASES = (JSON.parse(readFileSync(
  new URL("../../../../../tests/integration_conformance/cases.json", import.meta.url),
  "utf8",
)) as { cases: ConformanceCase[] }).cases;

function conformanceCase(id: string): ConformanceCase {
  const value = CONFORMANCE_CASES.find((item) => item.id === id && item.modes.includes("best_effort"));
  if (!value) throw new Error(`missing best-effort conformance case: ${id}`);
  return value;
}

function flushProgress(currentCursor: number, remainingWork = false) {
  return {
    status: currentCursor ? "processed" : "idle",
    previous_cursor: 0,
    current_cursor: currentCursor,
    high_watermark: currentCursor,
    processed_source_count: currentCursor ? 1 : 0,
    remaining_work: remainingWork,
  };
}

afterEach(() => {
  resetGlobalHookRunner();
});

function createLifecycleHarness() {
  const hooks = new Map<string, Hook>();
  const debugMessages: string[] = [];
  const warnings: string[] = [];
  const flushScopes: string[] = [];
  const capturedScopes: string[] = [];
  const capturedSourceIds: string[] = [];
  const contextQueries: string[] = [];
  const scopeResolutionRequests: Array<Record<string, unknown>> = [];
  let memoryExtraction = true;
  let contextPrepareError: unknown;
  let preparedResponse: unknown;
  const prepareRequests: Array<Record<string, unknown>> = [];
  let captureError: unknown;
  let flushError: unknown;
  let flushResponses: unknown[] = [];
  let flushHandler: ((scopeId: string, signal?: AbortSignal) => unknown | Promise<unknown>) | undefined;
  let capabilitiesHandler: ((signal?: AbortSignal) => unknown | Promise<unknown>) | undefined;
  let closingSignal = new AbortController().signal;
  let closingSignalFactory = () => closingSignal;
  let loggerFailure = false;
  let nextPosition = 1;
  const positionsBySource = new Map<string, number>();
  const latestPositionByScope = new Map<string, number>();
  const config = resolvePowerContextConfig(undefined, {
    endpoint: "https://powercontext.test",
  });
  const projectScopes = new Map(
    [
      ["/workspace/project-a", "scp_project_a"],
      ["/workspace/project-b", "scp_project_b"],
    ].map(([projectKey, scopeId]) => {
      const binding = scopeBindingKeys({ agentId: "main", activeProjectKeys: [projectKey] }).find(
        (key) => key.kind === "project",
      );
      return [binding!.external_id, scopeId] as const;
    }),
  );
  const client = {
    async get<T>(path: string, signal?: AbortSignal): Promise<T> {
      if (path !== "/v1/capabilities") {
        throw new Error(`unexpected GET ${path}`);
      }
      if (capabilitiesHandler) return await capabilitiesHandler(signal) as T;
      if (signal?.aborted) throw signal.reason ?? new DOMException("aborted", "AbortError");
      return { memory_extraction: memoryExtraction } as T;
    },
    async post<T>(path: string, body: Record<string, unknown>, signal?: AbortSignal): Promise<T> {
      if (path === "/v1/scope-bindings/resolve") {
        scopeResolutionRequests.push(body);
        const bindingKeys = body.binding_keys as ScopeBindingKey[];
        const projectBinding = bindingKeys.find(
          (key) => key.kind === "project" && projectScopes.has(key.external_id),
        );
        return {
          scope_id: projectBinding ? projectScopes.get(projectBinding.external_id) : "scp_default",
        } as T;
      }
      if (path === "/v1/memory/flush") {
        flushScopes.push(String(body.scope_id));
        if (flushHandler) return await flushHandler(String(body.scope_id), signal) as T;
        if (signal?.aborted) throw signal.reason ?? new DOMException("aborted", "AbortError");
        if (flushError !== undefined) {
          throw flushError;
        }
        if (flushResponses.length) {
          return flushResponses.shift() as T;
        }
        const current = latestPositionByScope.get(String(body.scope_id)) ?? 0;
        return {
          status: current ? "processed" : "idle",
          previous_cursor: 0,
          current_cursor: current,
          high_watermark: current,
          processed_source_count: current ? 1 : 0,
          remaining_work: false,
        } as T;
      }
      if (path === "/v1/sources/content") {
        capturedScopes.push(String(body.scope_id));
        capturedSourceIds.push(String(body.source_id));
        if (captureError !== undefined) {
          throw captureError;
        }
        const sourceId = String(body.source_id);
        let position = positionsBySource.get(sourceId);
        if (position === undefined) {
          position = nextPosition;
          nextPosition += 1;
          positionsBySource.set(sourceId, position);
        }
        latestPositionByScope.set(
          String(body.scope_id),
          Math.max(latestPositionByScope.get(String(body.scope_id)) ?? 0, position),
        );
        return { status: "accepted", source: { source_id: sourceId }, position } as T;
      }
      if (path === "/v1/context/prepare") {
        contextQueries.push(String(body.query));
        prepareRequests.push(body);
        if (preparedResponse !== undefined) return preparedResponse as T;
        if (contextPrepareError) {
          throw contextPrepareError;
        }
      }
      return {
        schema: "powercontext.prepared-context.v1",
        status: "empty",
        content: null,
        content_bytes: 0,
      } as T;
    },
  } as unknown as PowerContextClient;
  const api = {
    on(name: string, handler: unknown) {
      hooks.set(name, handler as Hook);
    },
    logger: {
      warn(message: string) {
        if (loggerFailure) throw new Error("logger unavailable");
        warnings.push(message);
      },
      debug(message: string) {
        if (loggerFailure) throw new Error("logger unavailable");
        debugMessages.push(message);
      },
    },
  } as unknown as OpenClawPluginApi;
  registerPowerContextLifecycle(api, {
    client,
    getConfig: () => config,
    isPrivateSession: () => true,
    createClosingSignal: () => closingSignalFactory(),
  });
  return {
    capturedScopes,
    capturedSourceIds,
    config,
    contextQueries,
    prepareRequests,
    setPreparedResponse(value: unknown) { preparedResponse = value; },
    debugMessages,
    flushScopes,
    hooks,
    setMemoryExtraction(value: boolean) {
      memoryExtraction = value;
    },
    setContextPrepareError(error: unknown) {
      contextPrepareError = error;
    },
    setCaptureError(error: unknown) {
      captureError = error;
    },
    setFlushError(error: unknown) {
      flushError = error;
    },
    setFlushResponses(values: unknown[]) {
      flushResponses = [...values];
    },
    setFlushHandler(handler: typeof flushHandler) {
      flushHandler = handler;
    },
    setCapabilitiesHandler(handler: typeof capabilitiesHandler) {
      capabilitiesHandler = handler;
    },
    setClosingSignal(signal: AbortSignal) {
      closingSignal = signal;
    },
    setClosingSignalFactory(factory: () => AbortSignal) {
      closingSignalFactory = factory;
    },
    setLoggerFailure(value: boolean) {
      loggerFailure = value;
    },
    scopeResolutionRequests,
    warnings,
  };
}

describe("PowerContext lifecycle", () => {
  it("flushes every project with accepted pending Sources at session end", async () => {
    const harness = createLifecycleHarness();
    const sessionContext = {
      agentId: "main",
      sessionId: "session-1",
      sessionKey: "agent:main:telegram:direct:user-1",
    };
    const agentEnd = harness.hooks.get("agent_end");
    const sessionEnd = harness.hooks.get("session_end");
    expect(agentEnd).toBeDefined();
    expect(sessionEnd).toBeDefined();

    await agentEnd!(
      { success: true, messages: [{ role: "user", content: "remember this" }] },
      { ...sessionContext, activeProjectKeys: ["/workspace/project-a"] },
    );
    await agentEnd!(
      { success: true, messages: [{ role: "user", content: "and this" }] },
      { ...sessionContext, activeProjectKeys: ["/workspace/project-b"] },
    );
    await sessionEnd!(
      { sessionId: sessionContext.sessionId, messageCount: 2 },
      sessionContext,
    );

    expect(harness.flushScopes).toEqual(["scp_project_a", "scp_project_b"]);
    expect(harness.warnings).toEqual([]);
  });

  it("does not leave a replayed acknowledged Source pending at closing", async () => {
    const harness = createLifecycleHarness();
    const context = {
      agentId: "main",
      sessionId: "session-replayed-ack",
      sessionKey: "agent:main:telegram:direct:user-1",
    };
    const event = {
      success: true,
      runId: "replayed-run",
      messages: [{ role: "user", content: "same source" }],
    };

    await harness.hooks.get("agent_end")!(event, context);
    await harness.hooks.get("gateway_stop")!({}, {});
    expect(harness.flushScopes).toEqual(["scp_default"]);

    await harness.hooks.get("agent_end")!(event, context);
    await harness.hooks.get("gateway_stop")!({}, {});

    expect(harness.flushScopes).toEqual(["scp_default"]);
    expect(harness.warnings).toEqual([]);
  });

  it("does not flush a replayed before-compaction target twice", async () => {
    const harness = createLifecycleHarness();
    const beforeCompaction = harness.hooks.get("before_compaction")!;
    const context = {
      agentId: "main",
      sessionId: "session-replayed-checkpoint",
      sessionKey: "agent:main:telegram:direct:user-1",
    };
    const event = { messages: [{ role: "user", content: "same source" }] };

    await beforeCompaction(event, context);
    await beforeCompaction(event, context);

    expect(harness.flushScopes).toEqual(["scp_default"]);
    expect(harness.warnings).toEqual([]);
  });

  it.each(["session_end", "gateway_stop"] as const)(
    "does not reflush a confirmed %s target when a newer position is pending",
    async (boundary) => {
      const harness = createLifecycleHarness();
      const context = {
        agentId: "main",
        sessionId: `session-target-pending-${boundary}`,
        sessionKey: "agent:main:telegram:direct:user-1",
      };
      let releaseCapabilities: (() => void) | undefined;
      let capabilitiesStarted: (() => void) | undefined;
      const started = new Promise<void>((resolve) => { capabilitiesStarted = resolve; });
      const blocked = new Promise<void>((resolve) => { releaseCapabilities = resolve; });
      let capabilityCalls = 0;
      harness.setCapabilitiesHandler(async () => {
        capabilityCalls += 1;
        if (capabilityCalls === 1) {
          capabilitiesStarted?.();
          await blocked;
        }
        return { memory_extraction: true };
      });
      let flushCalls = 0;
      harness.setFlushHandler(() => {
        flushCalls += 1;
        return flushProgress(flushCalls === 1 ? 1 : 2);
      });

      const beforeCompaction = harness.hooks.get("before_compaction")!(
        { messages: [{ role: "user", content: "first source" }] },
        context,
      ) as Promise<void>;
      await started;
      const closing = boundary === "session_end"
        ? harness.hooks.get("session_end")!(
          { sessionId: context.sessionId, messageCount: 1 },
          context,
        ) as Promise<void>
        : harness.hooks.get("gateway_stop")!({}, {}) as Promise<void>;
      await harness.hooks.get("agent_end")!(
        { success: true, runId: "later-source", messages: [{ role: "user", content: "later source" }] },
        context,
      );
      releaseCapabilities?.();
      await Promise.all([beforeCompaction, closing]);

      expect(harness.flushScopes).toEqual(["scp_default"]);
      expect(harness.warnings).toEqual([]);

      await harness.hooks.get("gateway_stop")!({}, {});
      expect(harness.flushScopes).toEqual(["scp_default", "scp_default"]);
      expect(harness.warnings).toEqual([]);
    },
  );

  it.each(["session_end", "gateway_stop"])(
    "shares the flush call budget across all pending Scopes at %s",
    async (lifecycleEvent) => {
      const harness = createLifecycleHarness();
      harness.config.flushMaxCalls = 1;
      const context = {
        agentId: "main",
        sessionId: `session-budget-${lifecycleEvent}`,
        sessionKey: "agent:main:telegram:direct:user-1",
      };
      await harness.hooks.get("agent_end")!(
        { success: true, messages: [{ role: "user", content: "scope a" }] },
        { ...context, activeProjectKeys: ["/workspace/project-a"] },
      );
      await harness.hooks.get("agent_end")!(
        { success: true, messages: [{ role: "user", content: "scope b" }] },
        { ...context, activeProjectKeys: ["/workspace/project-b"] },
      );

      if (lifecycleEvent === "session_end") {
        await harness.hooks.get("session_end")!(
          { sessionId: context.sessionId, messageCount: 2 },
          context,
        );
      } else {
        await harness.hooks.get("gateway_stop")!({}, {});
      }
      expect(harness.flushScopes).toEqual(["scp_project_a"]);

      await harness.hooks.get("gateway_stop")!({}, {});
      expect(harness.flushScopes).toEqual(["scp_project_a", "scp_project_b"]);
      expect(harness.warnings).toEqual([]);
    },
  );

  it("surfaces a bounded, content-free unavailable diagnostic", async () => {
    const harness = createLifecycleHarness();
    harness.setContextPrepareError(
      new PowerContextRequestError("/v1/context/prepare", "do not expose this detail"),
    );
    const beforePromptBuild = harness.hooks.get("before_prompt_build");
    const context = {
      agentId: "main",
      sessionId: "session-diagnostic",
      sessionKey: "agent:main:telegram:direct:user-1",
    };

    await beforePromptBuild!(
      { messages: [{ role: "user", content: "first request" }], prompt: "" },
      context,
    );
    await beforePromptBuild!(
      { messages: [{ role: "user", content: "second request" }], prompt: "" },
      context,
    );

    expect(harness.warnings).toHaveLength(1);
    expect(harness.warnings[0]).toBe(
      '{"component":"powercontext.openclaw","event":"context_prepare","outcome":"server_unavailable","recovery":"powercontext doctor"}',
    );
    expect(harness.warnings[0]).not.toContain("do not expose this detail");
  });

  it("reports a prepare domain failure from the actual endpoint", async () => {
    const harness = createLifecycleHarness();
    harness.setContextPrepareError(
      new PowerContextRequestError(
        "/v1/context/prepare",
        "invalid request",
        422,
        "invalid_request",
      ),
    );
    const beforePromptBuild = harness.hooks.get("before_prompt_build");

    await beforePromptBuild!(
      { messages: [{ role: "user", content: "prepare this" }], prompt: "" },
      {
        agentId: "main",
        sessionId: "session-prepare-domain-error",
        sessionKey: "agent:main:telegram:direct:user-1",
      },
    );

    expect(harness.warnings).toEqual([
      '{"component":"powercontext.openclaw","event":"context_prepare","outcome":"invalid_response","http_status":422,"error_code":"invalid_request"}',
    ]);
  });

  it("reports a capture domain failure from the actual endpoint", async () => {
    const harness = createLifecycleHarness();
    harness.setCaptureError(
      new PowerContextRequestError(
        "/v1/sources/content",
        "invalid request",
        422,
        "invalid_request",
      ),
    );
    const agentEnd = harness.hooks.get("agent_end");

    await agentEnd!(
      {
        success: true,
        messages: [{ role: "user", content: "capture this" }],
      },
      {
        agentId: "main",
        sessionId: "session-capture-domain-error",
        sessionKey: "agent:main:telegram:direct:user-1",
      },
    );

    expect(harness.warnings).toEqual([
      '{"component":"powercontext.openclaw","event":"capture_source","outcome":"invalid_response","http_status":422,"error_code":"invalid_request"}',
    ]);
  });

  it("reports a flush domain failure from the actual endpoint", async () => {
    const vector = conformanceCase("flush_failure");
    const harness = createLifecycleHarness();
    harness.setFlushError(
      new PowerContextRequestError(
        "/v1/memory/flush",
        "conflict",
        409,
        "conflict",
      ),
    );
    const agentEnd = harness.hooks.get("agent_end");
    const sessionEnd = harness.hooks.get("session_end");
    const context = {
      agentId: "main",
      sessionId: "session-flush-domain-error",
      sessionKey: "agent:main:telegram:direct:user-1",
      activeProjectKeys: ["/workspace/project"],
    };

    await agentEnd!(
      { success: true, messages: [{ role: "user", content: "remember this" }] },
      context,
    );
    await sessionEnd!(
      { sessionId: context.sessionId, messageCount: 1 },
      context,
    );

    expect(harness.warnings).toHaveLength(1);
    expect(JSON.parse(harness.warnings[0])).toMatchObject({
      component: "powercontext.openclaw",
      event: "session_end_flush",
      outcome: "invalid_response",
      failure_class: "invalid_response",
      host: "openclaw",
      lifecycle_event: "session_end",
      target_position: vector.input.target_position,
    });

    harness.setFlushError(undefined);
    await harness.hooks.get("gateway_stop")!({}, {});
    expect(harness.flushScopes).toHaveLength(vector.expected.later_retry_acknowledged ? 2 : 1);
  });

  it("flushes only accepted positions at both closing boundaries", async () => {
    const vector = conformanceCase("closing_boundaries");
    const harness = createLifecycleHarness();
    const agentEnd = harness.hooks.get("agent_end")!;
    const sessionEnd = harness.hooks.get("session_end")!;
    const gatewayStop = harness.hooks.get("gateway_stop")!;
    const context = {
      agentId: "main",
      sessionId: "session-closing-one",
      sessionKey: "agent:main:telegram:direct:user-1",
    };

    await sessionEnd({ sessionId: context.sessionId, messageCount: 0 }, context);
    expect(harness.flushScopes).toHaveLength(vector.expected.uncaptured_flush_calls);

    await agentEnd({ success: true, messages: [{ role: "user", content: "first" }] }, context);
    await sessionEnd({ sessionId: context.sessionId, messageCount: 1 }, context);
    await agentEnd(
      { success: true, messages: [{ role: "user", content: "second" }] },
      { ...context, sessionId: "session-closing-two" },
    );
    await gatewayStop({}, {});

    expect(vector.input.events_by_host.openclaw).toEqual(["session_end", "gateway_stop"]);
    expect(harness.flushScopes).toHaveLength(vector.expected.captured_flush_calls);
  });

  it("bounds context queries by UTF-8 bytes", async () => {
    const harness = createLifecycleHarness();
    const beforePromptBuild = harness.hooks.get("before_prompt_build");
    expect(beforePromptBuild).toBeDefined();

    await beforePromptBuild!(
      { messages: [{ role: "user", content: "记忆".repeat(5000) }], prompt: "" },
      {
        agentId: "main",
        sessionId: "session-utf8",
        sessionKey: "agent:main:telegram:direct:user-1",
      },
    );

    expect(harness.contextQueries).toHaveLength(1);
    expect(Buffer.byteLength(harness.contextQueries[0], "utf8")).toBeLessThanOrEqual(8192);
    expect(harness.warnings).toEqual([]);
  });

  it("keeps capture and flush on one resolved Scope during compaction", async () => {
    const harness = createLifecycleHarness();
    const beforeCompaction = harness.hooks.get("before_compaction");
    expect(beforeCompaction).toBeDefined();

    await beforeCompaction!(
      { messages: [{ role: "user", content: "remember this" }] },
      {
        agentId: "main",
        sessionId: "session-1",
        sessionKey: "agent:main:telegram:direct:user-1",
        activeProjectKeys: ["/workspace/project-a"],
      },
    );

    expect(harness.scopeResolutionRequests).toHaveLength(1);
    expect(harness.capturedScopes).toEqual(["scp_project_a"]);
    expect(harness.flushScopes).toEqual(["scp_project_a"]);
    expect(harness.warnings).toEqual([]);
  });

  it("retries until the flush cursor acknowledges the exact captured position", async () => {
    const vector = conformanceCase("cursor_catches_up");
    const harness = createLifecycleHarness();
    harness.config.flushMaxCalls = vector.expected.flush_calls;
    harness.setFlushResponses(vector.input.flush_cursors.map((cursor: number, index: number) => (
      flushProgress(cursor, vector.input.remaining_work[index])
    )));
    const beforeCompaction = harness.hooks.get("before_compaction")!;

    await beforeCompaction(
      { messages: [{ role: "user", content: "remember this" }] },
      {
        agentId: "main",
        sessionId: "session-cursor",
        sessionKey: "agent:main:telegram:direct:user-1",
      },
    );

    expect(harness.flushScopes).toHaveLength(vector.expected.flush_calls);
    expect(harness.warnings).toEqual([]);
  });

  it("rejects every malformed 2xx acknowledgement vector", async () => {
    const vector = conformanceCase("malformed_acknowledgement");
    for (const variant of vector.input.invalid_cursor_variants as string[]) {
      const harness = createLifecycleHarness();
      harness.config.flushMaxCalls = 1;
      harness.setFlushResponses([
        variant === "missing"
          ? { ...flushProgress(1), current_cursor: undefined }
          : { ...flushProgress(1), current_cursor: "1" },
      ]);
      const beforeCompaction = harness.hooks.get("before_compaction")!;

      await beforeCompaction(
        { messages: [{ role: "user", content: "do not log this transcript" }] },
        {
          agentId: "main",
          sessionId: `session-malformed-${variant}`,
          sessionKey: "agent:main:telegram:direct:user-1",
        },
      );

      expect(harness.warnings).toHaveLength(1);
      const diagnostic = JSON.parse(harness.warnings[0]);
      expect(diagnostic).toMatchObject({
        component: "powercontext.openclaw",
        event: "pre_compaction_checkpoint",
        outcome: "invalid_response",
        failure_class: "invalid_response",
        host: "openclaw",
        lifecycle_event: "before_compaction",
        target_position: vector.input.target_position,
      });
      expect(diagnostic.scope_correlation).toMatch(/^[0-9a-f]{16}$/);
      expect(harness.warnings[0]).not.toContain("do not log this transcript");
      expect(harness.warnings[0]).not.toContain("scp_default");
    }
  });

  it("reuses Source identity for duplicate delivery and distinguishes a later event", async () => {
    const vector = conformanceCase("duplicate_delivery");
    const harness = createLifecycleHarness();
    const beforeCompaction = harness.hooks.get("before_compaction")!;
    const context = {
      agentId: "main",
      sessionId: "session-duplicate",
      sessionKey: "agent:main:telegram:direct:user-1",
    };
    const first = { messages: vector.input.duplicate_event };
    const reordered = {
      messages: vector.input.duplicate_event.map((message: { role: string; content: string }) => ({
        content: message.content,
        role: message.role,
      })),
    };

    await beforeCompaction(first, context);
    await beforeCompaction(reordered, context);
    await beforeCompaction({ messages: vector.input.later_event }, context);

    expect(first.messages).toHaveLength(vector.input.later_event.length);
    expect(harness.capturedSourceIds[0]).toBe(harness.capturedSourceIds[1]);
    expect(harness.capturedSourceIds[2]).not.toBe(harness.capturedSourceIds[1]);
  });

  it("distinguishes equal-marker events whose canonical payloads differ outside the tail", async () => {
    const harness = createLifecycleHarness();
    const beforeCompaction = harness.hooks.get("before_compaction")!;
    const context = {
      agentId: "main",
      sessionId: "session-middle-difference",
      sessionKey: "agent:main:telegram:direct:user-1",
    };
    const sharedTail = [
      { role: "assistant", content: "same one" },
      { role: "user", content: "continue" },
      { role: "assistant", content: "same two" },
      { role: "user", content: "finish" },
    ];

    await beforeCompaction({
      messageCount: 5,
      compactingCount: 2,
      messages: [{ role: "user", content: "middle a" }, ...sharedTail],
    }, context);
    await beforeCompaction({
      messageCount: 5,
      compactingCount: 2,
      messages: [{ content: "middle b", role: "user" }, ...sharedTail],
    }, context);

    expect(harness.capturedSourceIds).toHaveLength(2);
    expect(harness.capturedSourceIds[1]).not.toBe(harness.capturedSourceIds[0]);
  });

  it("clears an acknowledged position when a later bounded flush call is cancelled", async () => {
    const harness = createLifecycleHarness();
    harness.config.flushMaxCalls = 2;
    let calls = 0;
    harness.setFlushHandler(() => {
      calls += 1;
      if (calls === 1) return flushProgress(1, true);
      throw new DOMException("cancelled", "AbortError");
    });
    const context = {
      agentId: "main",
      sessionId: "session-ack-then-cancel",
      sessionKey: "agent:main:telegram:direct:user-1",
    };

    await harness.hooks.get("before_compaction")!(
      { messages: [{ role: "user", content: "remember this" }] },
      context,
    );
    expect(harness.flushScopes).toHaveLength(2);

    await harness.hooks.get("session_end")!(
      { sessionId: context.sessionId, messageCount: 1 },
      context,
    );

    expect(harness.flushScopes).toHaveLength(2);
    expect(harness.warnings).toEqual([]);
  });

  it("preserves an acknowledgement when a later flush call exceeds the boundary deadline", async () => {
    const harness = createLifecycleHarness();
    harness.config.flushMaxCalls = 2;
    const boundaries: AbortController[] = [];
    harness.setClosingSignalFactory(() => {
      const controller = new AbortController();
      boundaries.push(controller);
      return controller.signal;
    });
    let calls = 0;
    harness.setFlushHandler(async (_scopeId, signal) => {
      calls += 1;
      if (calls === 1) return flushProgress(1, true);
      await new Promise<void>((_resolve, reject) => {
        signal?.addEventListener("abort", () => reject(signal.reason), { once: true });
      });
      throw new Error("unreachable");
    });
    const context = {
      agentId: "main",
      sessionId: "session-ack-then-deadline",
      sessionKey: "agent:main:telegram:direct:user-1",
    };

    const beforeCompaction = harness.hooks.get("before_compaction")!(
      { messages: [{ role: "user", content: "remember this" }] },
      context,
    ) as Promise<void>;
    await vi.waitFor(() => expect(harness.flushScopes).toHaveLength(2));
    boundaries[0].abort(new DOMException("deadline exceeded", "TimeoutError"));
    await beforeCompaction;

    expect(harness.warnings).toEqual([]);
    await harness.hooks.get("session_end")!(
      { sessionId: context.sessionId, messageCount: 1 },
      context,
    );
    expect(harness.flushScopes).toHaveLength(2);
    expect(harness.warnings).toEqual([]);
  });

  it("keeps a compaction acknowledgement separate from a concurrent later capture", async () => {
    const harness = createLifecycleHarness();
    harness.config.flushMaxCalls = 2;
    const boundaries: AbortController[] = [];
    harness.setClosingSignalFactory(() => {
      const controller = new AbortController();
      boundaries.push(controller);
      return controller.signal;
    });
    let calls = 0;
    harness.setFlushHandler(async (_scopeId, signal) => {
      calls += 1;
      if (calls === 1) return flushProgress(1, true);
      if (calls === 2) {
        await new Promise<void>((_resolve, reject) => {
          signal?.addEventListener("abort", () => reject(signal.reason), { once: true });
        });
        throw new Error("unreachable");
      }
      return flushProgress(2);
    });
    const context = {
      agentId: "main",
      sessionId: "session-ack-then-capture",
      sessionKey: "agent:main:telegram:direct:user-1",
    };

    const beforeCompaction = harness.hooks.get("before_compaction")!(
      { messages: [{ role: "user", content: "first source" }] },
      context,
    ) as Promise<void>;
    await vi.waitFor(() => expect(harness.flushScopes).toHaveLength(2));
    await harness.hooks.get("agent_end")!(
      { success: true, runId: "later-run", messages: [{ role: "user", content: "later source" }] },
      context,
    );
    boundaries[0].abort(new DOMException("deadline exceeded", "TimeoutError"));
    await beforeCompaction;

    expect(harness.warnings).toEqual([]);
    expect(harness.flushScopes).toHaveLength(2);
    await harness.hooks.get("gateway_stop")!({}, {});
    expect(harness.flushScopes).toHaveLength(3);
    expect(harness.warnings).toEqual([]);
  });

  it("reports the compaction target rather than a concurrent later capture on deadline", async () => {
    const harness = createLifecycleHarness();
    const boundaries: AbortController[] = [];
    harness.setClosingSignalFactory(() => {
      const controller = new AbortController();
      boundaries.push(controller);
      return controller.signal;
    });
    let calls = 0;
    harness.setFlushHandler(async (_scopeId, signal) => {
      calls += 1;
      if (calls === 1) return flushProgress(0, true);
      if (calls === 2) {
        await new Promise<void>((_resolve, reject) => {
          signal?.addEventListener("abort", () => reject(signal.reason), { once: true });
        });
        throw new Error("unreachable");
      }
      return flushProgress(2);
    });
    const context = {
      agentId: "main",
      sessionId: "session-target-then-capture",
      sessionKey: "agent:main:telegram:direct:user-1",
    };

    const beforeCompaction = harness.hooks.get("before_compaction")!(
      { messages: [{ role: "user", content: "first source" }] },
      context,
    ) as Promise<void>;
    await vi.waitFor(() => expect(harness.flushScopes).toHaveLength(2));
    await harness.hooks.get("agent_end")!(
      { success: true, runId: "later-run", messages: [{ role: "user", content: "later source" }] },
      context,
    );
    boundaries[0].abort(new DOMException("deadline exceeded", "TimeoutError"));
    await beforeCompaction;

    const diagnostics = harness.warnings.map((line) => JSON.parse(line));
    expect(diagnostics).toHaveLength(1);
    expect(diagnostics[0]).toMatchObject({
      event: "pre_compaction_checkpoint",
      outcome: "deadline_exceeded",
      lifecycle_event: "before_compaction",
      target_position: 1,
    });
    expect(JSON.stringify(diagnostics)).not.toContain("first source");
    expect(JSON.stringify(diagnostics)).not.toContain("later source");
    await harness.hooks.get("gateway_stop")!({}, {});
    expect(harness.flushScopes).toHaveLength(3);
  });

  it("serializes overlapping closing boundaries without flushing a cleared position", async () => {
    const harness = createLifecycleHarness();
    const context = {
      agentId: "main",
      sessionId: "session-overlap",
      sessionKey: "agent:main:telegram:direct:user-1",
    };
    await harness.hooks.get("agent_end")!(
      { success: true, messages: [{ role: "user", content: "remember this" }] },
      context,
    );
    let releaseCapabilities: (() => void) | undefined;
    let capabilitiesStarted: (() => void) | undefined;
    const started = new Promise<void>((resolve) => { capabilitiesStarted = resolve; });
    const blocked = new Promise<void>((resolve) => { releaseCapabilities = resolve; });
    harness.setCapabilitiesHandler(async () => {
      capabilitiesStarted?.();
      await blocked;
      return { memory_extraction: true };
    });

    const sessionEnd = harness.hooks.get("session_end")!(
      { sessionId: context.sessionId, messageCount: 1 },
      context,
    ) as Promise<void>;
    await started;
    const gatewayStop = harness.hooks.get("gateway_stop")!({}, {}) as Promise<void>;
    expect(harness.flushScopes).toEqual([]);
    releaseCapabilities?.();
    await Promise.all([sessionEnd, gatewayStop]);

    expect(harness.flushScopes).toEqual(["scp_default"]);
  });

  it("includes queue waiting in closing deadlines and retains pending work", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(0);
    const harness = createLifecycleHarness();
    harness.config.flushMaxCalls = 1;
    const firstContext = {
      agentId: "main",
      sessionId: "session-queued-deadline-one",
      sessionKey: "agent:main:telegram:direct:user-1",
    };
    const secondContext = { ...firstContext, sessionId: "session-queued-deadline-two" };
    await harness.hooks.get("agent_end")!(
      { success: true, runId: "run-pending-one", messages: [{ role: "user", content: "remember first" }] },
      firstContext,
    );
    await harness.hooks.get("agent_end")!(
      { success: true, runId: "run-pending-two", messages: [{ role: "user", content: "remember second" }] },
      secondContext,
    );
    const boundaries: AbortController[] = [];
    setTimeout(() => {
      for (const controller of boundaries) {
        controller.abort(new DOMException("deadline exceeded", "TimeoutError"));
      }
    }, 1000);
    harness.setClosingSignalFactory(() => {
      const controller = new AbortController();
      boundaries.push(controller);
      return controller.signal;
    });
    let calls = 0;
    harness.setFlushHandler(async (_scopeId, signal) => {
      calls += 1;
      if (calls > 2) return flushProgress(2);
      await new Promise<void>((resolve, reject) => {
        const timeout = setTimeout(resolve, 900);
        signal?.addEventListener("abort", () => {
          clearTimeout(timeout);
          reject(signal.reason);
        }, { once: true });
      });
      if (signal?.aborted) throw signal.reason;
      return flushProgress(0, true);
    });

    try {
      const startedAt = Date.now();
      const firstSessionEnd = harness.hooks.get("session_end")!(
        { sessionId: firstContext.sessionId, messageCount: 1 },
        firstContext,
      ) as Promise<void>;
      const secondSessionEnd = harness.hooks.get("session_end")!(
        { sessionId: secondContext.sessionId, messageCount: 1 },
        secondContext,
      ) as Promise<void>;
      const gatewayStop = harness.hooks.get("gateway_stop")!({}, {}) as Promise<void>;
      await vi.advanceTimersByTimeAsync(1000);
      await Promise.all([firstSessionEnd, secondSessionEnd, gatewayStop]);

      expect(Date.now() - startedAt).toBe(1000);
      expect(harness.flushScopes).toEqual(["scp_default", "scp_default"]);
      const deadlineDiagnostic = harness.warnings.map((line) => JSON.parse(line)).find((event) => (
        event.event === "shutdown_flush" && event.outcome === "deadline_exceeded"
      ));
      expect(deadlineDiagnostic).toMatchObject({
        failure_class: "deadline_exceeded",
        host: "openclaw",
        lifecycle_event: "gateway_stop",
        target_position: 2,
      });
      expect(JSON.stringify(deadlineDiagnostic)).not.toContain("remember");

      await harness.hooks.get("gateway_stop")!({}, {});
      expect(harness.flushScopes).toEqual(["scp_default", "scp_default", "scp_default"]);
    } finally {
      vi.useRealTimers();
    }
  });

  it("does not let one Scope cursor acknowledge another Scope", async () => {
    const vector = conformanceCase("scope_isolation");
    const harness = createLifecycleHarness();
    harness.config.flushMaxCalls = 1;
    harness.setFlushResponses(vector.input.scopes.map((scope: { cursor: number }) => flushProgress(scope.cursor)));
    const beforeCompaction = harness.hooks.get("before_compaction")!;
    const base = {
      agentId: "main",
      sessionId: "session-scopes",
      sessionKey: "agent:main:telegram:direct:user-1",
    };

    await beforeCompaction(
      { messages: [{ role: "user", content: "scope a" }] },
      { ...base, activeProjectKeys: ["/workspace/project-a"] },
    );
    await beforeCompaction(
      { messages: [{ role: "user", content: "scope b" }] },
      { ...base, activeProjectKeys: ["/workspace/project-b"] },
    );

    expect(harness.flushScopes).toEqual(["scp_project_a", "scp_project_b"]);
    expect(harness.warnings).toHaveLength(vector.expected.pending_scopes);
    expect(JSON.parse(harness.warnings[0])).toMatchObject({ target_position: 2, observed_cursor: 1 });
  });

  it("uses real AbortSignals before and after acknowledgement", async () => {
    const vector = conformanceCase("cancellation_boundaries");
    const harness = createLifecycleHarness();
    harness.config.flushMaxCalls = 1;
    const agentEnd = harness.hooks.get("agent_end")!;
    const gatewayStop = harness.hooks.get("gateway_stop")!;

    await agentEnd(
      { success: true, messages: [{ role: "user", content: "remember this" }] },
      {
        agentId: "main",
        sessionId: "session-cancel",
        sessionKey: "agent:main:telegram:direct:user-1",
      },
    );

    const before = new AbortController();
    before.abort(new DOMException("cancelled", "AbortError"));
    harness.setClosingSignal(before.signal);
    await gatewayStop({}, {});
    expect(harness.flushScopes).toHaveLength(0);

    const after = new AbortController();
    harness.setClosingSignal(after.signal);
    harness.setFlushHandler(() => {
      after.abort(new DOMException("cancelled", "AbortError"));
      return flushProgress(vector.input.target_position);
    });
    await gatewayStop({}, {});
    const callsAfterAcknowledgement = harness.flushScopes.length;
    await gatewayStop({}, {});

    expect(callsAfterAcknowledgement).toBe(1);
    expect(vector.expected.pending_before).toBe(1);
    expect(vector.expected.pending_after).toBe(0);
    expect(harness.flushScopes).toHaveLength(callsAfterAcknowledgement);
  });

  it("continues through the real OpenClaw harness when a before-compaction hook fails", async () => {
    const vector = conformanceCase("best_effort_fail_open");
    let hookCalls = 0;
    initializeGlobalHookRunner({
      hooks: [],
      plugins: [{ id: "powercontext-test", status: "loaded" }],
      typedHooks: [{
        pluginId: "powercontext-test",
        hookName: "before_compaction",
        handler: async () => {
          hookCalls += 1;
          throw new Error("checkpoint failed");
        },
        source: "test",
      }],
    } as never);

    await expect(runAgentHarnessBeforeCompactionHook({
      sessionFile: "/tmp/session.jsonl",
      messages: [],
      ctx: {},
    } as never)).resolves.toBeUndefined();
    expect(hookCalls).toBe(1);
    expect(vector.expected.host_continues).toBe(true);
  });

  it("continues flushing pending Scopes when operational logging throws", async () => {
    const harness = createLifecycleHarness();
    harness.config.flushMaxCalls = 2;
    const agentEnd = harness.hooks.get("agent_end")!;
    const context = {
      agentId: "main",
      sessionId: "session-logging",
      sessionKey: "agent:main:telegram:direct:user-1",
    };
    await agentEnd(
      { success: true, messages: [{ role: "user", content: "scope a" }] },
      { ...context, activeProjectKeys: ["/workspace/project-a"] },
    );
    await agentEnd(
      { success: true, messages: [{ role: "user", content: "scope b" }] },
      { ...context, activeProjectKeys: ["/workspace/project-b"] },
    );
    harness.setFlushError(new Error("flush failed"));
    harness.setLoggerFailure(true);

    await expect(harness.hooks.get("session_end")!(
      { sessionId: context.sessionId, messageCount: 2 },
      context,
    )).resolves.toBeUndefined();

    expect(harness.flushScopes).toEqual(["scp_project_a", "scp_project_b"]);
  });

  it("keeps a boundary capture pending when extraction is unavailable", async () => {
    const vector = conformanceCase("extraction_unavailable");
    const harness = createLifecycleHarness();
    harness.setMemoryExtraction(vector.input.memory_extraction);
    const beforeCompaction = harness.hooks.get("before_compaction")!;

    await beforeCompaction(
      { messages: [{ role: "user", content: "remember this" }] },
      {
        agentId: "main",
        sessionId: "session-extraction",
        sessionKey: "agent:main:telegram:direct:user-1",
      },
    );

    expect(harness.capturedSourceIds).toHaveLength(vector.expected.capture_accepted ? 1 : 0);
    expect(harness.flushScopes).toHaveLength(vector.expected.flush_calls_by_host.openclaw);
    expect(JSON.parse(harness.warnings[0])).toMatchObject({
      event: "pre_compaction_checkpoint",
      outcome: "extraction_unavailable",
      target_position: vector.input.target_position,
    });
  });

  it("defers flush without dropping captured sources when extraction is unavailable", async () => {
    const harness = createLifecycleHarness();
    const agentEnd = harness.hooks.get("agent_end");
    const beforePromptBuild = harness.hooks.get("before_prompt_build");
    const sessionEnd = harness.hooks.get("session_end");
    const context = {
      agentId: "main",
      sessionId: "session-1",
      sessionKey: "agent:main:telegram:direct:user-1",
      activeProjectKeys: ["/workspace/project-a"],
    };
    expect(agentEnd).toBeDefined();
    expect(beforePromptBuild).toBeDefined();
    expect(sessionEnd).toBeDefined();

    harness.setMemoryExtraction(false);
    await agentEnd!(
      {
        success: true,
        messages: [
          { role: "user", content: "remember this" },
          { role: "assistant", content: "I will remember it" },
        ],
      },
      context,
    );
    await sessionEnd!({ sessionId: context.sessionId, messageCount: 2 }, context);

    const scope = "scp_project_a";
    expect(harness.capturedScopes).toEqual([scope]);
    expect(harness.flushScopes).toEqual([]);
    expect(harness.debugMessages).toContain(
      "memory-powercontext: memory flush deferred because extraction is unavailable; captured sources remain pending",
    );
    expect(harness.warnings).toEqual([]);

    harness.setMemoryExtraction(true);
    const nextContext = { ...context, sessionId: "session-2" };
    await beforePromptBuild!(
      { messages: [{ role: "user", content: "what did I ask you to remember?" }], prompt: "" },
      nextContext,
    );
    await sessionEnd!({ sessionId: nextContext.sessionId, messageCount: 1 }, nextContext);

    expect(harness.flushScopes).toEqual([scope]);
    expect(harness.warnings).toEqual([]);
  });
});


it("forwards assembly and preserves every byte of standard text", async () => {
  const harness = createLifecycleHarness();
  const content = "\n# PowerContext historical context\n>     原始文本 </powercontext_memory>\n";
  harness.config.contextAssembly = { sections: [{ family: "memory", limit: 3 }] };
  const response = {
    schema: "powercontext.prepared-context.v1", status: "ready", content,
    content_bytes: Buffer.byteLength(content, "utf8"),
  };
  harness.setPreparedResponse(response);
  const hook = harness.hooks.get("before_prompt_build")!;
  const ctx = { agentId: "main", sessionId: "one", sessionKey: "agent:main:telegram:direct:user-1" };
  const output = await hook({ prompt: "context", messages: [] }, ctx) as { prependContext: string };
  expect(harness.prepareRequests[0].assembly).toEqual(harness.config.contextAssembly);
  expect(output.prependContext.endsWith(content)).toBe(true);
  for (const invalid of [
    { ...response, content_bytes: 1 },
    { ...response, content: "x".repeat(8001), content_bytes: 8001 },
    { ...response, unexpected: true },
  ]) {
    harness.setPreparedResponse(invalid);
    expect(await hook({ prompt: "context", messages: [] }, ctx)).toBeUndefined();
  }
});
