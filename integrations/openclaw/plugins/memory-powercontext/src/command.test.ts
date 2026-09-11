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
import { resolvePowerContextConfig } from "./config.js";
import type { PowerContextClient } from "./http.js";
import { registerPowerContextCommand } from "./command.js";

describe("PowerContext /pc command", () => {
  it("registers a WebUI-visible command with local help", async () => {
    let definition: Parameters<OpenClawPluginApi["registerCommand"]>[0] | undefined;
    registerPowerContextCommand({
      registerCommand(value) {
        definition = value;
      },
    }, {
      client: {} as PowerContextClient,
      getConfig: () => resolvePowerContextConfig(undefined, { endpoint: "https://powercontext.test" }),
      isPrivateSession: () => true,
    });

    expect(definition?.name).toBe("pc");
    expect(definition?.acceptsArgs).toBe(true);
    expect(definition?.requireAuth).toBe(true);
    const reply = await definition!.handler({ args: "help" } as never);
    expect(reply.text).toContain("/pc status");
    expect(reply.text).toContain("/pc handoff");
  });

  it("continues workflow subcommands into the agent", async () => {
    let definition: Parameters<OpenClawPluginApi["registerCommand"]>[0] | undefined;
    registerPowerContextCommand({
      registerCommand(value) {
        definition = value;
      },
    }, {
      client: {} as PowerContextClient,
      getConfig: () => resolvePowerContextConfig(undefined, { endpoint: "https://powercontext.test" }),
      isPrivateSession: () => true,
    });

    const reply = await definition!.handler({
      args: "handoff",
      agentId: "main",
      sessionKey: "agent:main:webchat:direct:user-1",
    } as never);
    expect(reply.continueAgent).toBe(true);
    expect(reply.text).toContain("handoff workflow requested");
  });
});
