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

import { execFile } from "node:child_process";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";
import { expect, it } from "vitest";

it("OpenClaw discovers the packaged router and its workflow files in an isolated profile", async () => {
  const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
  const home = await mkdtemp(join(tmpdir(), "pc-openclaw-skills-"));
  try {
    // The host selects runtimeExtensions, so discovery needs the built entry.
    await readFile(join(root, "dist", "index.js"), "utf8");
    const config = join(home, "openclaw.json");
    await writeFile(config, JSON.stringify({
      agents: { defaults: { workspace: join(home, "workspace") } },
      plugins: {
        allow: ["memory-powercontext"], load: { paths: [root] },
        slots: { memory: "memory-powercontext" },
        entries: { "memory-powercontext": { enabled: true, config: { endpoint: "http://127.0.0.1:1" } } },
      },
    }));
    // The CLI suppresses its entrypoint in test-runner environments.
    const env: NodeJS.ProcessEnv = { ...process.env, OPENCLAW_HOME: home, OPENCLAW_STATE_DIR: join(home, "state"), OPENCLAW_CONFIG_PATH: config };
    delete env.VITEST;
    delete env.NODE_ENV;
    const { stdout } = await promisify(execFile)(process.execPath,
      [join(root, "node_modules/openclaw/openclaw.mjs"), "skills", "list", "--json"], {
        timeout: 90000, maxBuffer: 4 * 1024 * 1024,
        env,
      });
    const skill = JSON.parse(stdout).skills.find((item: { name: string }) => item.name === "powercontext-project-context");
    expect(skill, `OpenClaw did not discover powercontext-project-context from ${root}/skills`).toMatchObject({ eligible: true, modelVisible: true, disabled: false });
    const directory = join(root, "skills", skill.name);
    const entry = await readFile(join(directory, "SKILL.md"), "utf8");
    for (const match of entry.matchAll(/\[[^\]]*\]\((references\/[^)]+)\)/g)) {
      expect((await readFile(join(directory, match[1]), "utf8")).trim()).not.toBe("");
    }
  } finally { await rm(home, { recursive: true, force: true }); }
}, 100000);
