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

import { readFile, writeFile, mkdir } from "node:fs/promises";
import { createHash } from "node:crypto";
import { fileURLToPath } from "node:url";
import YAML from "yaml";
import openapiTS, { astToString } from "openapi-typescript";

const root = new URL("../", import.meta.url);
const contractUrl = new URL("../openapi/powercontext.yaml", root);
const raw = await readFile(contractUrl, "utf8");
const contract = YAML.parse(raw);
const wanted = [
  "get_liveness",
  "get_readiness",
  "get_capabilities",
  "get_access_principal",
  "list_scopes",
  "get_scope",
  "get_default_scope",
  "remember_memory",
  "search_memory",
  "get_memory_entry",
];
const operations = {};
for (const [path, item] of Object.entries(contract.paths)) {
  for (const [method, op] of Object.entries(item)) {
    if (wanted.includes(op?.operationId)) {
      if (operations[op.operationId]) throw new Error("Duplicate operation");
      operations[op.operationId] = { method: method.toUpperCase(), path };
    }
  }
}
if (Object.keys(operations).length !== wanted.length)
  throw new Error("Missing public operation");
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
function rustType(schema) {
  if (schema.$ref) return schema.$ref.split("/").at(-1);
  switch (schema.type) {
    case "string":
      return "String";
    case "integer":
      return "i64";
    case "number":
      return "f64";
    case "boolean":
      return "bool";
    case "array":
      return `Vec<${rustType(schema.items)}>`;
    case "object":
      if (
        schema.additionalProperties &&
        typeof schema.additionalProperties === "object"
      )
        return `std::collections::BTreeMap<String, ${rustType(schema.additionalProperties)}>`;
      throw new Error("Unsupported inline object in Desktop wire model");
    default:
      throw new Error(`Unsupported wire schema ${JSON.stringify(schema)}`);
  }
}
const rustModels = [...schemaNames]
  .sort()
  .map((name) => {
    const schema = contract.components.schemas[name];
    const derive =
      "#[derive(Clone, Debug, PartialEq, serde::Deserialize, serde::Serialize, ts_rs::TS)]";
    if (schema.enum) {
      const values = schema.enum.filter((v) => v !== null);
      const members = values.map(
        (value) =>
          `    #[serde(rename = ${JSON.stringify(value)})]\n    ${value
            .split(/[^a-zA-Z0-9]+/)
            .map((part) => part[0].toUpperCase() + part.slice(1))
            .join("")},`,
      );
      return `${derive}\npub enum ${name} {\n${members.join("\n")}\n}\n`;
    }
    if (schema.type !== "object")
      return `pub type ${name} = ${rustType(schema)};\n`;
    const fields = Object.entries(schema.properties).map(([field, value]) => {
      let type = rustType(value);
      if (value.nullable || !schema.required?.includes(field))
        type = `Option<${type}>`;
      return `${!schema.required?.includes(field) ? '    #[serde(skip_serializing_if = "Option::is_none")]\n    #[ts(optional = nullable)]\n' : ""}    pub r#${field}: ${type},`;
    });
    return `${derive}\n#[serde(deny_unknown_fields)]\npub struct ${name} {\n${fields.join("\n")}\n}\n`;
  })
  .join("\n");
const rustDeclarations = `\n#[rustfmt::skip]\npub fn declarations(config: &ts_rs::Config) -> Vec<String> {\n    vec![\n${[
  ...schemaNames,
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
    JSON.stringify({ contractSha256: digest, operations }, null, 2) + "\n",
};
for (const [name, text] of Object.entries(outputs)) {
  const target = new URL(name, root);
  if (process.argv.includes("--check")) {
    if ((await readFile(target, "utf8")).replaceAll("\r\n", "\n") !== text)
      throw new Error("Contract drift: " + name);
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
