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

import { createRequire } from 'node:module'
import { homedir } from 'node:os'
import { join } from 'node:path'
import { pathToFileURL } from 'node:url'

export function profileNodeModulesDir(env: NodeJS.ProcessEnv = process.env): string {
  const home = env.DSH_HOME?.trim() || join(homedir(), '.dsh')
  const profile = env.DSH_PROFILE?.trim() || 'web'
  return join(home, 'profiles', profile, 'node_modules')
}

function profileModulesAnchor(env: NodeJS.ProcessEnv = process.env): string {
  return join(profileNodeModulesDir(env), 'powercontext-dsh-resolver.cjs')
}

function resolvePeer(specifier: string): string {
  try {
    return createRequire(import.meta.url).resolve(specifier)
  } catch {
    return createRequire(profileModulesAnchor()).resolve(specifier)
  }
}

export async function loadPeer<T>(specifier: string): Promise<T> {
  const href = pathToFileURL(resolvePeer(specifier)).href
  return await import(href) as T
}
