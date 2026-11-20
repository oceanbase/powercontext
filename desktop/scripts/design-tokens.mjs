/* Copyright (c) 2026 OceanBase. Licensed under the Apache License, Version 2.0. */

import { readFile, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import YAML from "yaml";

const desktop = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const source = resolve(desktop, "DESIGN.md");
const output = resolve(desktop, "ui", "src", "generated", "design-tokens.css");
const markdown = await readFile(source, "utf8");
const match = markdown.match(/^---\n([\s\S]*?)\n---/);
if (!match) throw new Error("DESIGN.md must start with YAML frontmatter");
const design = YAML.parse(match[1]);

const lines = [
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
