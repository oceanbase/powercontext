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
import { createHash, randomUUID } from "node:crypto";
import { mkdir, readFile, rename, rm } from "node:fs/promises";
import { createRequire } from "node:module";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";

const root = new URL("../", import.meta.url);
const run = promisify(execFile);

export async function getGeneratorInfo() {
  const [config, checksum] = await Promise.all(
    ["openapitools.json", "openapi-generator-checksum.json"].map(async (name) =>
      JSON.parse(await readFile(new URL(name, root), "utf8")),
    ),
  );
  const version = config["generator-cli"]?.version;
  if (
    typeof version !== "string" ||
    checksum.version !== version ||
    !/^[a-f0-9]{64}$/i.test(checksum.sha256 ?? "")
  ) {
    throw new Error("Pin the OpenAPI Generator version and SHA-256 together");
  }
  const generatorCli = createRequire(import.meta.url).resolve(
    "@openapitools/openapi-generator-cli/main.js",
  );
  return {
    version,
    sha256: checksum.sha256.toLowerCase(),
    jar: join(dirname(generatorCli), "versions", `${version}.jar`),
    url: `https://repo.maven.apache.org/maven2/org/openapitools/openapi-generator-cli/${version}/openapi-generator-cli-${version}.jar`,
  };
}

async function matchesChecksum(path, expected) {
  try {
    const bytes = await readFile(path);
    return createHash("sha256").update(bytes).digest("hex") === expected;
  } catch (error) {
    if (error.code === "ENOENT") return false;
    throw error;
  }
}

export async function prepareGenerator(info) {
  info ??= await getGeneratorInfo();
  const { jar, sha256, url, version } = info;
  if (await matchesChecksum(jar, sha256)) return jar;

  await mkdir(dirname(jar), { recursive: true });
  await rm(jar, { force: true });
  const temporary = `${jar}.${randomUUID()}.tmp`;
  try {
    // curl honors proxy settings and Retry-After, and retries only download failures.
    await run(
      "curl",
      [
        "--fail",
        "--location",
        "--silent",
        "--show-error",
        "--retry",
        "3",
        "--retry-connrefused",
        "--retry-max-time",
        "120",
        "--connect-timeout",
        "10",
        "--max-time",
        "45",
        "--output",
        temporary,
        url,
      ],
      { timeout: 180_000, windowsHide: true },
    );
    if (!(await matchesChecksum(temporary, sha256))) {
      throw new Error(`SHA-256 mismatch for OpenAPI Generator ${version}`);
    }
    await rename(temporary, jar);
    return jar;
  } finally {
    await rm(temporary, { force: true });
  }
}

if (
  process.argv[1] &&
  fileURLToPath(import.meta.url) === resolve(process.argv[1])
) {
  try {
    const info = await getGeneratorInfo();
    if (process.argv.includes("--cache-info")) {
      console.log(`jar=${info.jar}`);
      console.log(`key=openapi-generator-v1-${info.version}-${info.sha256}`);
    } else {
      await prepareGenerator(info);
    }
  } catch (error) {
    console.error(error.message);
    process.exitCode = 1;
  }
}
