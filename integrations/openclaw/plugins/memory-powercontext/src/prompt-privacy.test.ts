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
import { describe, expect, it } from "vitest";
import { resolvePowerContextConfig } from "./config.js";
import type { PowerContextClient } from "./http.js";
import { registerPowerContextLifecycle } from "./lifecycle.js";
import { containsSecret, scrubSecrets } from "./secrets.js";

type PrivacyCase = { id: string; prompt: string; sensitive: boolean; authorization?: string };
type Hook = (event: unknown, context: unknown) => unknown;

const { cases } = JSON.parse(readFileSync(
  new URL("../../../../../tests/fixtures/automatic_prompt_privacy.json", import.meta.url),
  "utf8",
)) as { cases: PrivacyCase[] };

function expectNoSyntheticSecret(value: string): void {
  expect(value).not.toContain("synthetic-value-2084");
  expect(value).not.toContain("synthetic_value_2084");
}

describe("shared automatic prompt privacy policy", () => {
  it.each(cases)("$id", ({ prompt, sensitive, authorization }) => {
    expect(containsSecret(prompt, authorization)).toBe(sensitive);
    const query = scrubSecrets(prompt, authorization);
    if (sensitive) expectNoSyntheticSecret(query);
    else expect(query).toBe(prompt);
  });
});

const TOKEN_ENV = "POWERCONTEXT_PRIVACY_TEST_TOKEN";
const CONTEXT = { agentId: "main", sessionId: "session-privacy", sessionKey: "agent:main:direct:user-1" };

function createHarness(options: { captureMaxChars?: number } = {}) {
  const config = resolvePowerContextConfig(undefined, {
    endpoint: "https://powercontext.test",
    tokenEnv: TOKEN_ENV,
    ...options,
  });
  const requests: Array<{ path: string; body: Record<string, unknown> }> = [];
  const client = {
    async get() {
      return { memory_extraction: true };
    },
    async post(path: string, body: Record<string, unknown>) {
      requests.push({ path, body });
      if (path === "/v1/scope-bindings/resolve") return { scope_id: "scp_privacy" };
      if (path === "/v1/sources/content") return { status: "accepted", position: 1 };
      return { schema: "powercontext.prepared-context.v1", status: "empty", content: null, content_bytes: 0 };
    },
  } as unknown as PowerContextClient;
  const hooks = new Map<string, Hook>();
  registerPowerContextLifecycle({
    on: (name: string, handler: Hook) => hooks.set(name, handler),
    logger: { warn() {}, debug() {} },
  } as unknown as OpenClawPluginApi, {
    client,
    getConfig: () => config,
    isPrivateSession: () => true,
    createClosingSignal: () => new AbortController().signal,
  });
  return {
    requests,
    hook: (name: string) => hooks.get(name)!,
    prepare: () => requests.find(({ path }) => path === "/v1/context/prepare"),
    captures: () => requests.filter(({ path }) => path === "/v1/sources/content"),
  };
}

async function withServerToken(token: string | undefined, run: () => Promise<void>): Promise<void> {
  const previous = process.env[TOKEN_ENV];
  if (token === undefined) delete process.env[TOKEN_ENV];
  else process.env[TOKEN_ENV] = token;
  try {
    await run();
  } finally {
    if (previous === undefined) delete process.env[TOKEN_ENV];
    else process.env[TOKEN_ENV] = previous;
  }
}

describe("automatic recall and capture", () => {
  it.each(cases)("$id", async ({ prompt, sensitive, authorization }) => {
    const harness = createHarness();
    const messages = [{ role: "user", content: prompt }];
    await withServerToken(authorization?.replace(/^Bearer\s+/u, ""), async () => {
      await harness.hook("before_prompt_build")({ messages, prompt }, CONTEXT);
      await harness.hook("agent_end")({ success: true, runId: "run-privacy", messages }, CONTEXT);
    });

    expect(harness.prepare()).toBeDefined();
    if (sensitive) {
      expect(harness.captures()).toEqual([]);
      expectNoSyntheticSecret(JSON.stringify(harness.requests));
    } else {
      expect(harness.prepare()!.body.query).toBe(prompt.trim());
      expect(harness.captures()).toHaveLength(1);
      expect(harness.captures()[0]!.body.content).toBe(`User: ${prompt.trim()}`);
    }
  });

  it("recognizes the Server token the client sends after trimming the environment value", async () => {
    const harness = createHarness();
    const messages = [{ role: "user", content: "Use synthetic-value-2084 in the request." }];
    await withServerToken(" synthetic-value-2084\n", async () => {
      await harness.hook("before_prompt_build")({ messages }, CONTEXT);
      await harness.hook("agent_end")({ success: true, runId: "run-token", messages }, CONTEXT);
    });

    expect(harness.prepare()!.body.query).toBe("Use [REDACTED] in the request.");
    expect(harness.captures()).toEqual([]);
  });

  it("skips a pre-compaction capture whose credential lies beyond the capture truncation", async () => {
    const harness = createHarness({ captureMaxChars: 128 });
    const messages = [{ role: "user", content: `${"context ".repeat(40)}api_key=synthetic-value-2084` }];
    await withServerToken(undefined, async () => {
      await harness.hook("before_compaction")({ messages, messageCount: 1, compactingCount: 1 }, CONTEXT);
    });

    expect(harness.captures()).toEqual([]);
    expectNoSyntheticSecret(JSON.stringify(harness.requests));
  });
});
