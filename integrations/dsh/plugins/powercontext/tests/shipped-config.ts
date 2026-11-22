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

import { readFileSync } from 'node:fs'
import type { PluginConfig } from '../src/config.ts'

/** Return the plugin config that `cordis.patch.yml` writes into every installation. */
export function shippedConfig(): PluginConfig {
  const lines = readFileSync(new URL('../cordis.patch.yml', import.meta.url), 'utf8').split('\n')
  const start = lines.findIndex(line => line.trim() === 'config:')
  if (start < 0) throw new Error('cordis.patch.yml has no plugin config')
  const values: Record<string, unknown> = {}
  for (const line of lines.slice(start + 1)) {
    const match = /^ {8}(\w+): (.+)$/.exec(line)
    if (!match) break
    values[match[1]!] = JSON.parse(match[2]!)
  }
  return values as PluginConfig
}
