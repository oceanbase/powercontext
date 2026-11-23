/*
Copyright (c) 2026 OceanBase.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
*/
import { build } from "esbuild";
import { compile } from "sass";
import { readFile, writeFile, stat } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";
const root = path.dirname(fileURLToPath(import.meta.url));
const output = path.resolve(root, "../../plugins/knowledge-base/ui");
const license = (await readFile(path.join(root, "src/main.tsx"), "utf8")).split("*/", 1)[0] + "*/";
await build({
  absWorkingDir: root,
  entryPoints: ["src/main.tsx"],
  outfile: path.join(output, "app.js"),
  bundle: true,
  format: "iife",
  platform: "browser",
  target: "es2022",
  minify: true,
  define: { "process.env.NODE_ENV": '"production"' },
  legalComments: "eof",
  banner: { js: license, css: license },
  loader: { ".woff2": "dataurl", ".woff": "dataurl", ".ttf": "dataurl", ".svg": "dataurl", ".png": "dataurl" },
  plugins: [
    {
      name: "sass",
      setup(context) {
        context.onLoad({ filter: /\.scss$/ }, (args) => ({
          contents: compile(args.path).css,
          loader: "css",
          resolveDir: path.dirname(args.path),
        }));
      },
    },
  ],
});
await writeFile(path.join(output, "index.html"), await readFile(path.join(root, "index.html")));
for (const name of ["app.js", "app.css", "index.html"]) {
  const file = path.join(output, name);
  const contents = await readFile(file, "utf8");
  await writeFile(
    file,
    contents.replace(/[ \t]+$/gm, "").replace(/^([ \t]+)/gm, (indent) => indent.replace(/\t/g, "  ")),
  );
  const size = (await stat(file)).size;
  if (size > 5 * 1024 * 1024) throw new Error(`${name} exceeds the plugin asset size limit`);
  console.log(`${name}: ${(size / 1024).toFixed(1)} KiB`);
}
