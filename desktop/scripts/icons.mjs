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
import { access, mkdir, readFile, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { execFileSync } from "node:child_process";
const root = new URL("../", import.meta.url);
const output = new URL("src-tauri/icons/", root);
const temporary = new URL(".artifacts/icon-generation/", root);
const canonicalSource = new URL(
  "../website/assets/powercontext-color.png",
  root,
);
const packagedSource = new URL("brand.png", output);
const iconSize = 240;
const tileSize = 192;
const tileInset = (iconSize - tileSize) / 2;
const markSize = 134;
const markInset = (iconSize - markSize) / 2;
await mkdir(temporary, { recursive: true });
await mkdir(output, { recursive: true });
const source = await access(canonicalSource).then(
  () => canonicalSource,
  () => packagedSource,
);
// Extract the project's square mark, never a screenshot or third-party logo.
const brand = await sharp(fileURLToPath(source))
  .extract({ left: 0, top: 0, width: iconSize, height: iconSize })
  .png()
  .toBuffer();
await writeFile(new URL("brand.png", temporary), brand);
// Keep the tile inside the canvas so its visible footprint matches neighboring
// macOS icons instead of filling every pixel available to the launcher.
const appIcon = await sharp({
  create: {
    width: iconSize,
    height: iconSize,
    channels: 4,
    background: { r: 0, g: 0, b: 0, alpha: 0 },
  },
})
  .composite([
    {
      input: Buffer.from(
        `<svg width="${tileSize}" height="${tileSize}" xmlns="http://www.w3.org/2000/svg"><rect width="${tileSize}" height="${tileSize}" rx="42" fill="#fff"/></svg>`,
      ),
      left: tileInset,
      top: tileInset,
    },
    {
      input: await sharp(brand)
        .resize(markSize, markSize, { fit: "contain" })
        .png()
        .toBuffer(),
      left: markInset,
      top: markInset,
    },
  ])
  .png()
  .toBuffer();
await writeFile(new URL("app-icon.png", temporary), appIcon);
execFileSync(
  process.execPath,
  [
    fileURLToPath(new URL("node_modules/@tauri-apps/cli/tauri.js", root)),
    "icon",
    fileURLToPath(new URL("app-icon.png", temporary)),
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

// The ICO encoder can compress the same PNG frames differently across platforms.
async function icoPixels(bytes) {
  const headerSize = 6;
  const entrySize = 16;
  const header = bytes.subarray(0, headerSize);
  const count = header.readUInt16LE(4);
  const directoryEnd = headerSize + count * entrySize;
  if (directoryEnd > bytes.length) throw new Error("Truncated ICO directory");

  const frames = [];
  for (let entry = headerSize; entry < directoryEnd; entry += entrySize) {
    const size = bytes.readUInt32LE(entry + 8);
    const offset = bytes.readUInt32LE(entry + 12);
    if (offset < directoryEnd || offset + size > bytes.length)
      throw new Error("Invalid ICO image bounds");
    const { data, info } = await sharp(bytes.subarray(offset, offset + size))
      .ensureAlpha()
      .raw()
      .toBuffer({ resolveWithObject: true });
    if (
      info.width !== (bytes[entry] || 256) ||
      info.height !== (bytes[entry + 1] || 256)
    )
      throw new Error("ICO image dimensions do not match the directory");
    frames.push(bytes.subarray(entry, entry + 8), data);
  }
  return Buffer.concat([header, ...frames]);
}

for (const name of ["brand.png", "icon.png", "icon.ico", "icon.icns"]) {
  const expected =
    name === "brand.png"
      ? brand
      : await readFile(new URL("derived/" + name, temporary));
  const destination = new URL(name, output);
  if (process.argv.includes("--check")) {
    const actual = await readFile(destination);
    const normalize =
      name === "icon.ico"
        ? icoPixels
        : name === "icon.icns"
          ? canonicalIcon
          : (bytes) => bytes;
    if (!(await normalize(actual)).equals(await normalize(expected)))
      throw new Error("Brand resource drift: " + name);
  } else {
    await writeFile(destination, expected);
  }
}
console.log("Desktop brand resources match their canonical source.");
