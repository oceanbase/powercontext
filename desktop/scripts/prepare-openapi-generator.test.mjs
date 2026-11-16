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

import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { once } from "node:events";
import { mkdtemp, readFile, readdir, rm, writeFile } from "node:fs/promises";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { prepareGenerator } from "./prepare-openapi-generator.mjs";

const artifact = Buffer.from("a pinned generator artifact\n");
const sha256 = createHash("sha256").update(artifact).digest("hex");
process.env.NO_PROXY = [process.env.NO_PROXY, "127.0.0.1"]
  .filter(Boolean)
  .join(",");

async function fixture(t, respond) {
  const directory = await mkdtemp(join(tmpdir(), "openapi-generator-test-"));
  const server = createServer(respond);
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  t.after(async () => {
    await new Promise((resolve, reject) =>
      server.close((error) => (error ? reject(error) : resolve())),
    );
    await rm(directory, { recursive: true, force: true });
  });
  return {
    directory,
    info: {
      version: "7.26.0",
      sha256,
      jar: join(directory, "7.26.0.jar"),
      url: `http://127.0.0.1:${server.address().port}/generator.jar`,
    },
  };
}

test("a verified cached generator needs no network request", async (t) => {
  let requests = 0;
  const { info } = await fixture(t, (_request, response) => {
    requests += 1;
    response.writeHead(503).end();
  });
  await writeFile(info.jar, artifact);

  assert.equal(await prepareGenerator(info), info.jar);
  assert.deepEqual(await readFile(info.jar), artifact);
  assert.equal(requests, 0);
});

test("a download recovers from rate limiting and a temporary server failure", async (t) => {
  const statuses = [429, 503, 200];
  const served = [];
  const { info } = await fixture(t, (_request, response) => {
    const status = statuses.shift() ?? 200;
    served.push(status);
    response.writeHead(status).end(status === 200 ? artifact : "try later");
  });

  assert.equal(await prepareGenerator(info), info.jar);
  assert.deepEqual(await readFile(info.jar), artifact);
  assert.deepEqual(served, [429, 503, 200]);
});

test("a bad cache and mismatched download cannot be used, and a later download recovers", async (t) => {
  let body = Buffer.from("wrong artifact");
  const { directory, info } = await fixture(t, (_request, response) => {
    response.end(body);
  });
  await writeFile(info.jar, "broken cache");

  await assert.rejects(prepareGenerator(info), /SHA-256 mismatch/);
  assert.deepEqual(await readdir(directory), []);

  body = artifact;
  assert.equal(await prepareGenerator(info), info.jar);
  assert.deepEqual(await readFile(info.jar), artifact);
});

test("a missing artifact fails without retrying or leaving a cached file", async (t) => {
  let requests = 0;
  const { directory, info } = await fixture(t, (_request, response) => {
    requests += 1;
    response.writeHead(404).end("not found");
  });

  await assert.rejects(prepareGenerator(info), /404/);
  assert.equal(requests, 1);
  assert.deepEqual(await readdir(directory), []);
});
