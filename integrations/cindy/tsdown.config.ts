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
import { fileURLToPath } from 'node:url'
import { readFileSync, writeFileSync } from 'node:fs'

const source = new URL('./src/worker.ts', import.meta.url)
const worker = new URL('./plugins/powercontext-hooks/node/worker.cjs', import.meta.url)
const license = readFileSync(source, 'utf8').split('*/', 1)[0] + '*/'

export default {
  entry: { worker: fileURLToPath(new URL('./src/worker.ts', import.meta.url)) },
  outDir: fileURLToPath(new URL('./plugins/powercontext-hooks/node', import.meta.url)),
  format: ['cjs'],
  platform: 'node',
  target: 'es2022',
  dts: false,
  clean: true,
  banner: license,
  onSuccess: () => { writeFileSync(worker, readFileSync(worker, 'utf8').trimEnd() + '\n') },
}
