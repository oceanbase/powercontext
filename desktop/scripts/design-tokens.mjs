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

import { readFile, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import YAML from "yaml";

const desktop = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const source = resolve(desktop, "DESIGN.md");
const output = resolve(desktop, "ui", "src", "generated", "design-tokens.css");
const license = YAML.parse(
  await readFile(resolve(desktop, "..", ".licenserc.yaml"), "utf8"),
).header.license.content;
const markdown = await readFile(source, "utf8");
const match = markdown.match(/^---\n([\s\S]*?)\n---/);
if (!match) throw new Error("DESIGN.md must start with YAML frontmatter");
const design = YAML.parse(match[1]);

const lines = [
  "/*",
  ...license
    .trimEnd()
    .split("\n")
    .map((line) => (line ? ` * ${line}` : " *")),
  " */",
  "",
  "/* Generated from DESIGN.md. Do not edit by hand. */",
  ":root {",
];
for (const [name, value] of Object.entries(design.colors ?? {})) {
  lines.push(`  --pc-color-${name}: ${value};`);
}
for (const [name, value] of Object.entries(design.rounded ?? {})) {
  lines.push(`  --pc-radius-${name}: ${value};`);
}
for (const [name, value] of Object.entries(design.spacing ?? {})) {
  lines.push(`  --pc-space-${name}: ${value};`);
}
for (const [name, value] of Object.entries(design["type-scale"] ?? {})) {
  lines.push(`  --pc-text-${name}: ${value};`);
}
for (const group of [
  "shadows",
  "motion",
  "layout",
  "controls",
  "borders",
  "z-index",
]) {
  for (const [name, value] of Object.entries(design[group] ?? {})) {
    lines.push(`  --pc-${group}-${name}: ${value};`);
  }
}
for (const [role, values] of Object.entries(design.typography ?? {})) {
  const token = role.replaceAll("_", "-");
  if (values.fontFamily)
    lines.push(`  --pc-font-${token}: ${values.fontFamily};`);
  if (values.fontSize) lines.push(`  --pc-text-${token}: ${values.fontSize};`);
  if (values.fontWeight)
    lines.push(`  --pc-weight-${token}: ${values.fontWeight};`);
  if (values.lineHeight)
    lines.push(`  --pc-leading-${token}: ${values.lineHeight};`);
  if (values.letterSpacing)
    lines.push(`  --pc-tracking-${token}: ${values.letterSpacing};`);
}
lines.push("}");
await writeFile(output, `${lines.join("\n")}\n`, "utf8");
