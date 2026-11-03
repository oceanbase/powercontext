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

import sharp from "sharp";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { execFileSync } from "node:child_process";
const root = new URL("../", import.meta.url);
const source = new URL("../website/assets/powercontext-color.png", root);
const output = new URL("src-tauri/icons/", root);
const temporary = new URL(".artifacts/icon-generation/", root);
await mkdir(temporary, { recursive: true });
await mkdir(output, { recursive: true });
// Extract the project's square mark, never a screenshot or third-party logo.
const brand = await sharp(fileURLToPath(source))
  .extract({ left: 0, top: 0, width: 240, height: 240 })
  .png()
  .toBuffer();
await writeFile(new URL("brand.png", temporary), brand);
execFileSync(
  process.execPath,
  [
    fileURLToPath(new URL("node_modules/@tauri-apps/cli/tauri.js", root)),
    "icon",
    fileURLToPath(new URL("brand.png", temporary)),
    "--output",
    fileURLToPath(new URL("derived/", temporary)),
  ],
  { stdio: "pipe" },
);
// ICNS encodes a map of image chunks; chunk order is not stable across CLI runs.
function canonicalIcon(bytes) {
  const chunks = [];
  for (let offset = 8; offset < bytes.length; ) {
    const size = bytes.readUInt32BE(offset + 4);
    if (size < 8 || offset + size > bytes.length)
      throw new Error("Invalid ICNS chunk");
    chunks.push(bytes.subarray(offset, offset + size));
    offset += size;
  }
  return Buffer.concat([bytes.subarray(0, 8), ...chunks.sort(Buffer.compare)]);
}
for (const name of ["brand.png", "icon.png", "icon.ico", "icon.icns"]) {
  const expected =
    name === "brand.png"
      ? brand
      : await readFile(new URL("derived/" + name, temporary));
  const destination = new URL(name, output);
  if (process.argv.includes("--check")) {
    const actual = await readFile(destination);
    const normalize = name === "icon.icns" ? canonicalIcon : (bytes) => bytes;
    if (!normalize(actual).equals(normalize(expected)))
      throw new Error("Brand resource drift: " + name);
  } else {
    await writeFile(destination, expected);
  }
}
console.log("Desktop brand resources match their canonical source.");
