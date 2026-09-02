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
  it("preserves the structured error code from an actual endpoint response", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(
        JSON.stringify({ error: { code: "source_conflict", message: "source already exists" } }),
        { status: 409, headers: { "content-type": "application/json" } },
      )),
    );
    const config = resolvePowerContextConfig(undefined, { endpoint: "http://powercontext.test" });
    const client = createPowerContextClient(() => config);

    await expect(client.post("/v1/sources/content", {})).rejects.toMatchObject({
      path: "/v1/sources/content",
      status: 409,
      code: "source_conflict",
    });
  });
});
