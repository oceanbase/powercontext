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

import { readFile, writeFile } from 'node:fs/promises'

import { defineConfig } from 'tsdown'

const LICENSE_HEADER = `/*
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
 */`

async function normalizeGeneratedFile(path: URL): Promise<void> {
  const content = await readFile(path, 'utf8')
  await writeFile(path, `${content.trimEnd()}\n`, 'utf8')
}

export default defineConfig({
  entry: { index: 'src/index.ts' },
  outDir: 'lib',
  format: ['esm'],
  dts: true,
  clean: true,
  platform: 'node',
  target: 'es2022',
  fixedExtension: false,
  external: [/^@opencode-ai\//, /^node:/],
  banner: LICENSE_HEADER,
  hooks: {
    'build:done': async () => {
      await Promise.all([
        normalizeGeneratedFile(new URL('./lib/index.js', import.meta.url)),
        normalizeGeneratedFile(new URL('./lib/index.d.ts', import.meta.url)),
      ])
    },
  },
})
