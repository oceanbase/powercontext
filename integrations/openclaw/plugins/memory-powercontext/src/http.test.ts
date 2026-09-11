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

import { afterEach, describe, expect, it, vi } from "vitest";
import { resolvePowerContextConfig } from "./config.js";
import { createPowerContextClient } from "./http.js";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("PowerContext HTTP errors", () => {
  it("rejects non-boolean consent from direct JavaScript callers", async () => {
    const config = resolvePowerContextConfig(undefined, { endpoint: "https://powercontext.test" });
    config.endpoint = "http://powercontext.test";
    config.allowInsecureHttp = "false" as unknown as boolean;
    const fetchMock = vi.fn(async () => new Response("{}"));
    vi.stubGlobal("fetch", fetchMock);
    await expect(createPowerContextClient(() => config).get("/v1/scopes")).rejects.toThrow(/boolean/);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("does not follow redirects even with explicit HTTP consent", async () => {
    const fetchMock = vi.fn(async (_url, init) => {
      expect(init.redirect).toBe("manual");
      return new Response("", { status: 302, headers: { location: "http://another.test" } });
    });
    vi.stubGlobal("fetch", fetchMock);
    const config = resolvePowerContextConfig(undefined, {
      endpoint: "http://powercontext.test", allowInsecureHttp: true,
    });
    await expect(createPowerContextClient(() => config).get("/v1/scopes")).rejects.toMatchObject({
      status: 302,
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("forwards the configured bearer token and preserves Access denial details", async () => {
    const tokenEnv = "POWERCONTEXT_OPENCLAW_TEST_TOKEN";
    process.env[tokenEnv] = "integration-token";
    try {
      vi.stubGlobal(
        "fetch",
        vi.fn(async (_url, init) => {
          expect(new Headers(init?.headers).get("authorization")).toBe("Bearer integration-token");
          return new Response(
            JSON.stringify({ error: { code: "access_denied", message: "scope access denied" } }),
            { status: 403, headers: { "content-type": "application/json" } },
          );
        }),
      );
      const config = resolvePowerContextConfig(undefined, {
        endpoint: "https://powercontext.test",
        tokenEnv,
      });
      const client = createPowerContextClient(() => config);

      await expect(client.get("/v1/scopes/scope%3Afeature")).rejects.toMatchObject({
        path: "/v1/scopes/scope%3Afeature",
        status: 403,
        code: "access_denied",
      });
    } finally {
      delete process.env[tokenEnv];
    }
  });

  it("preserves the structured error code from an actual endpoint response", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(
        JSON.stringify({ error: { code: "source_conflict", message: "source already exists" } }),
        { status: 409, headers: { "content-type": "application/json" } },
      )),
    );
    const config = resolvePowerContextConfig(undefined, { endpoint: "https://powercontext.test" });
    const client = createPowerContextClient(() => config);

    await expect(client.post("/v1/sources/content", {})).rejects.toMatchObject({
      path: "/v1/sources/content",
      status: 409,
      code: "source_conflict",
    });
  });
});
