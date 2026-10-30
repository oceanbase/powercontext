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

import {
  access,
  readFile,
  writeFile,
  mkdir,
  mkdtemp,
  rm,
} from "node:fs/promises";
import { createHash } from "node:crypto";
import { spawn } from "node:child_process";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import YAML from "yaml";
import openapiTS, { astToString } from "openapi-typescript";

const root = new URL("../", import.meta.url);
const contractUrl = new URL("../openapi/powercontext.yaml", root);
const raw = await readFile(contractUrl, "utf8");
const contract = YAML.parse(raw);
const wanted = [
  "get_server_info",
  "get_liveness",
  "get_readiness",
  "get_capabilities",
  "get_access_principal",
  "list_scopes",
  "get_scope",
  "get_default_scope",
  "remember_memory",
  "search_memory",
  "get_artifact_revision",
];
const operations = {};
const operationFeatures = {};
for (const [path, item] of Object.entries(contract.paths)) {
  for (const [method, op] of Object.entries(item)) {
    if (wanted.includes(op?.operationId)) {
      if (operations[op.operationId]) throw new Error("Duplicate operation");
      operations[op.operationId] = { method: method.toUpperCase(), path };
      operationFeatures[op.operationId] =
        op["x-powercontext-feature-contracts"] ?? [];
    }
  }
}
if (Object.keys(operations).length !== wanted.length)
  throw new Error("Missing public operation");
const discovery = {
  apiMajor: Number(contract.info.version.split(".")[0]),
  features: contract["x-powercontext-feature-contracts"],
  operationFeatures,
};
const digest = createHash("sha256")
  .update(raw.replaceAll("\r\n", "\n"))
  .digest("hex");
const license =
  (await readFile(new URL(import.meta.url), "utf8")).split(" */")[0] +
  " */\n\n";
const header =
  license + "// Generated from openapi/powercontext.yaml. Do not edit.\n";
// Rust wire models share the same reachable OpenAPI schema graph as the reviewed operations.
const schemaNames = new Set();
function collectSchemas(value) {
  if (!value || typeof value !== "object") return;
  if (value.$ref?.startsWith("#/components/schemas/")) {
    const name = value.$ref.split("/").at(-1);
    if (!schemaNames.has(name)) {
      schemaNames.add(name);
      collectSchemas(contract.components.schemas[name]);
    }
  }
  for (const child of Object.values(value)) collectSchemas(child);
}
for (const item of Object.values(contract.paths))
  for (const op of Object.values(item))
    if (wanted.includes(op?.operationId)) collectSchemas(op);
function snakeCase(name) {
  return name
    .replace(/([a-z0-9])([A-Z])/g, "$1_$2")
    .replace(/([A-Z])([A-Z][a-z])/g, "$1_$2")
    .toLowerCase();
}
function run(command, args, cwd) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, {
      cwd,
      stdio: ["ignore", "pipe", "pipe"],
    });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk) => (stdout += chunk));
    child.stderr.on("data", (chunk) => (stderr += chunk));
    child.on("error", reject);
    child.on("close", (code) =>
      code === 0
        ? resolve(stdout)
        : reject(
            new Error(
              `${command} ${args.join(" ")} failed (${code})\n${stdout}\n${stderr}`,
            ),
          ),
    );
  });
}
async function generateRustModels() {
  const temporary = await mkdtemp(
    join(tmpdir(), "powercontext-desktop-openapi-"),
  );
  const output = join(temporary, "generated");
  const modelNames = [...schemaNames].sort();
  const generatorCli = createRequire(import.meta.url).resolve(
    "@openapitools/openapi-generator-cli/main.js",
  );
  try {
    const generatorConfig = JSON.parse(
      await readFile(new URL("openapitools.json", root), "utf8"),
    );
    const jar = join(
      dirname(generatorCli),
      "versions",
      `${generatorConfig["generator-cli"].version}.jar`,
    );
    try {
      await access(jar);
    } catch {
      await run(
        process.execPath,
        [generatorCli, "version"],
        fileURLToPath(root),
      );
    }
    // Discovery minor versions may add optional fields, including in nested contracts.
    const input = structuredClone(contract);
    for (const name of ["ServerInfo", "ContractVersion", "FeatureContract"]) {
      input.components.schemas[name]["x-rust-allow-unknown-fields"] = true;
    }
    // Child singleton enums already validate the discriminator. Let them own
    // its serde encoding too: an internal tag would consume the required field
    // on decode and emit it twice on encode.
    for (const schema of Object.values(input.components.schemas)) {
      const tag = schema.discriminator?.propertyName;
      if (!tag || !schema.oneOf?.length) continue;
      const tags = schema.oneOf.map((variant) => {
        const child = input.components.schemas[variant.$ref?.split("/").at(-1)];
        const values = child?.properties?.[tag]?.enum;
        return child?.required?.includes(tag) &&
          values?.length === 1 &&
          (!schema.discriminator.mapping ||
            schema.discriminator.mapping[values[0]] === variant.$ref)
          ? values[0]
          : undefined;
      });
      if (
        tags.every((value) => typeof value === "string") &&
        new Set(tags).size === tags.length
      ) {
        schema["x-rust-untagged-discriminator"] = true;
      }
    }
    const inputPath = join(temporary, "openapi.yaml");
    await writeFile(inputPath, YAML.stringify(input));
    await run(
      "java",
      [
        "-jar",
        jar,
        "generate",
        "-i",
        inputPath,
        "-g",
        "rust",
        "-t",
        fileURLToPath(new URL("./openapi-generator/rust/", import.meta.url)),
        "-o",
        output,
        "--global-property",
        `models=${modelNames.join(":")},modelDocs=false,modelTests=false`,
        "--additional-properties",
        "packageName=powercontext_wire,packageVersion=0.1.0,hideGenerationTimestamp=true,useChrono=false,avoidBoxedModels=true",
      ],
      fileURLToPath(root),
    );
    const imports = new Set();
    const sources = [];
    for (const name of modelNames) {
      const source = await readFile(
        join(output, "src", "models", `${snakeCase(name)}.rs`),
        "utf8",
      );
      sources.push({ name, source });
    }
    const declarationCounts = new Map();
    for (const { source } of sources) {
      for (const match of source.matchAll(/pub (?:struct|enum) (\w+)/gu)) {
        declarationCounts.set(
          match[1],
          (declarationCounts.get(match[1]) ?? 0) + 1,
        );
      }
    }
    const bodies = [];
    for (const { name, source: originalSource } of sources) {
      let source = originalSource;
      for (const match of source.matchAll(/pub (?:struct|enum) (\w+)/gu)) {
        const declaration = match[1];
        if (
          declarationCounts.get(declaration) > 1 &&
          !modelNames.includes(declaration)
        ) {
          const renamed = `${name}${declaration}`;
          source = source.replace(
            new RegExp(`\\b${declaration}\\b`, "gu"),
            renamed,
          );
        }
      }
      let body = source.replace(/^(?:\/\*[\s\S]*?\*\/\s*)+/u, "");
      body = body.replace(/^use crate::models;\s*$/gmu, "");
      body = body.replace(/^use .+;\s*$/gmu, (line) => {
        imports.add(line.trim());
        return "";
      });
      body = body.replaceAll("models::", "").trim();
      bodies.push(body);
    }
    const unformatted =
      [...imports].sort().join("\n") + "\n\n" + bodies.join("\n\n") + "\n";
    const wirePath = join(temporary, "wire.rs");
    await writeFile(wirePath, unformatted);
    await run("rustfmt", ["--edition", "2024", wirePath], fileURLToPath(root));
    return await readFile(wirePath, "utf8");
  } finally {
    await rm(temporary, { recursive: true, force: true });
  }
}
const rustModels = await generateRustModels();
// OpenAPI Generator also emits inline enums and helper models that are not
// component schemas. Include every generated Rust declaration in the ts-rs
// export list so referenced TypeScript names are emitted as well.
const rustModelNames = [
  ...rustModels.matchAll(/pub (?:struct|enum) (\w+)/gu),
].map(([, name]) => name);
const rustDeclarations = `\n#[rustfmt::skip]\npub fn declarations(config: &ts_rs::Config) -> Vec<String> {\n    vec![\n${[
  ...new Set([...schemaNames, ...rustModelNames]),
]
  .sort()
  .map((name) => `        <${name} as ts_rs::TS>::decl(config),`)
  .join("\n")}\n    ]\n}\n`;
const outputs = {
  "src-tauri/src/transport/wire.rs":
    header +
    "// rustfmt uses this generated layout verbatim.\n" +
    rustModels +
    rustDeclarations,
  "ui/src/generated/api.d.ts": header + astToString(await openapiTS(contract)),
  "ui/src/generated/operations.ts":
    header +
    "export const contractSha256 = " +
    JSON.stringify(digest) +
    ";\nexport const operations = " +
    JSON.stringify(operations, null, 2) +
    " as const;\n",
  "src-tauri/src/transport/operations.json":
    JSON.stringify({ contractSha256: digest, operations, discovery }, null, 2) +
    "\n",
};
for (const [name, text] of Object.entries(outputs)) {
  const target = new URL(name, root);
  if (process.argv.includes("--check")) {
    if ((await readFile(target, "utf8")).replaceAll("\r\n", "\n") !== text)
      throw new Error(
        "Contract drift: " + name + ". Run 'pnpm --dir desktop generate'.",
      );
  } else {
    await mkdir(fileURLToPath(new URL(".", target)), { recursive: true });
    await writeFile(target, text);
  }
}
console.log(
  process.argv.includes("--check")
    ? "Desktop contract matches OpenAPI."
    : "Desktop contract generated.",
);
