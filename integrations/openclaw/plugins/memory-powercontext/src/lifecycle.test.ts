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


import type { OpenClawPluginApi } from "openclaw/plugin-sdk/plugin-entry";
import { describe, expect, it } from "vitest";
import { resolvePowerContextConfig, resolvePowerContextScope } from "./config.js";
import { PowerContextRequestError, type PowerContextClient } from "./http.js";
import { registerPowerContextLifecycle } from "./lifecycle.js";

type Hook = (event: unknown, context: unknown) => unknown;

function createLifecycleHarness() {
  const hooks = new Map<string, Hook>();
  const debugMessages: string[] = [];
  const warnings: string[] = [];
  const flushScopes: string[] = [];
  const capturedScopes: string[] = [];
  const contextQueries: string[] = [];
  let memoryExtraction = true;
  let contextPrepareError: unknown;
  let captureError: unknown;
  let flushError: unknown;
  const config = resolvePowerContextConfig(undefined, {
    endpoint: "http://powercontext.test",
    scopeMode: "project",
  });
  const client = {
    async get<T>(path: string): Promise<T> {
      if (path !== "/v1/capabilities") {
        throw new Error(`unexpected GET ${path}`);
      }
      return { memory_extraction: memoryExtraction } as T;
    },
    async post<T>(path: string, body: Record<string, unknown>): Promise<T> {
      if (path === "/v1/memory/flush") {
        flushScopes.push(String(body.scope_id));
        if (flushError !== undefined) {
          throw flushError;
        }
      }
      if (path === "/v1/sources/content") {
        capturedScopes.push(String(body.scope_id));
        if (captureError !== undefined) {
          throw captureError;
        }
      }
      if (path === "/v1/context/prepare") {
        contextQueries.push(String(body.query));
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
        warnings.push(message);
      },
      debug(message: string) {
        debugMessages.push(message);
      },
    },
  } as unknown as OpenClawPluginApi;
  registerPowerContextLifecycle(api, {
    client,
    getConfig: () => config,
    isPrivateSession: () => true,
  });
  return {
    capturedScopes,
    config,
    contextQueries,
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
    warnings,
  };
}

describe("PowerContext lifecycle", () => {
  it("flushes every project observed by a session", async () => {
    const harness = createLifecycleHarness();
    const sessionContext = {
      agentId: "main",
      sessionId: "session-1",
      sessionKey: "agent:main:telegram:direct:user-1",
    };
    const beforePromptBuild = harness.hooks.get("before_prompt_build");
    const sessionEnd = harness.hooks.get("session_end");
    expect(beforePromptBuild).toBeDefined();
    expect(sessionEnd).toBeDefined();

    await beforePromptBuild!(
      { messages: [{ role: "user", content: "remember this" }], prompt: "" },
      { ...sessionContext, activeProjectKeys: ["/workspace/project-a"] },
    );
    await beforePromptBuild!(
      { messages: [{ role: "user", content: "and this" }], prompt: "" },
      { ...sessionContext, activeProjectKeys: ["/workspace/project-b"] },
    );
    await sessionEnd!(
      { sessionId: sessionContext.sessionId, messageCount: 2 },
      sessionContext,
    );

    expect(harness.flushScopes).toEqual([
      resolvePowerContextScope("main", harness.config, ["/workspace/project-a"]),
      resolvePowerContextScope("main", harness.config, ["/workspace/project-b"]),
    ]);
    expect(harness.warnings).toEqual([]);
  });

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
    const harness = createLifecycleHarness();
    harness.setFlushError(
      new PowerContextRequestError(
        "/v1/memory/flush",
        "conflict",
        409,
        "conflict",
      ),
    );
    const beforePromptBuild = harness.hooks.get("before_prompt_build");
    const sessionEnd = harness.hooks.get("session_end");
    const context = {
      agentId: "main",
      sessionId: "session-flush-domain-error",
      sessionKey: "agent:main:telegram:direct:user-1",
      activeProjectKeys: ["/workspace/project"],
    };

    await beforePromptBuild!(
      { messages: [{ role: "user", content: "remember this" }], prompt: "" },
      context,
    );
    await sessionEnd!(
      { sessionId: context.sessionId, messageCount: 1 },
      context,
    );

    expect(harness.warnings).toEqual([
      '{"component":"powercontext.openclaw","event":"session_end_flush","outcome":"invalid_response","http_status":409,"error_code":"conflict","failed_scopes":1,"total_scopes":1}',
    ]);
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

    const scope = resolvePowerContextScope("main", harness.config, context.activeProjectKeys);
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
