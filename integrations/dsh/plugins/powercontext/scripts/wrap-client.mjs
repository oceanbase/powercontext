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

import { existsSync, readFileSync, renameSync, rmSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')
const input = join(root, 'lib', 'client.cjs')
const output = join(root, 'lib', 'client.js')
const license = `/*
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
`
if (!existsSync(input)) {
  throw new Error('client CJS bundle not found; run tsdown before wrap-client.mjs')
}
const body = readFileSync(input, 'utf8')
const wrapper = `${license}window.__ModuleLoader__.load({
\tid: "powercontext-dsh",
\tfactory: (require) => {
\t\tvar module = { exports: {} };
\t\tvar exports = module.exports;
\t\t(function (module, exports, require) {
${body}
\t\t})(module, exports, require);
\t\treturn module.exports;
\t}
});
`
writeFileSync(output, wrapper)
// tsdown names CJS declarations `.d.cts`; the package export advertises `.d.ts`.
const cts = join(root, 'lib', 'client.d.cts')
if (existsSync(cts)) renameSync(cts, join(root, 'lib', 'client.d.ts'))
for (const stale of [input, `${input}.map`, join(root, 'lib', 'client.js.map')]) rmSync(stale, { force: true })
