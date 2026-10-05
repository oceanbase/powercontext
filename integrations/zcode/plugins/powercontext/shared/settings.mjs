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

import { closeSync, existsSync, openSync, readSync } from 'node:fs'
import { resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

export const PLUGIN_ROOT = fileURLToPath(new URL('..', import.meta.url))

export function nonempty(value) {
  return typeof value === 'string' && value.trim() ? value.trim() : undefined
}

export function enabled(value) {
  return ['1', 'true', 'yes', 'on'].includes((value ?? '').toLowerCase())
}

export function loadSettings() {
  const path = resolve(PLUGIN_ROOT, 'powercontext.json')
  let installed = {}
  if (existsSync(path)) {
    let file
    try {
      file = openSync(path, 'r')
      const buffer = Buffer.alloc(16_385)
      const bytes = readSync(file, buffer, 0, buffer.length, 0)
      if (bytes > 16_384) throw new Error('invalid_settings')
      installed = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(buffer.subarray(0, bytes)))
      if (!installed || typeof installed.server_url !== 'string' || !installed.server_url.trim()) {
        throw new Error('invalid_settings')
      }
    } catch {
      // An unreadable installed endpoint must never redirect writes to a fallback Server.
      installed = { server_url: 'invalid' }
    } finally { if (file !== undefined) closeSync(file) }
  }
  const boundary = process.env.POWERCONTEXT_ZCODE_BOUNDARY_FLUSH
  const capture = process.env.POWERCONTEXT_ZCODE_CAPTURE_PROMPTS
  return {
    serverUrl: installed.server_url || process.env.POWERCONTEXT_ZCODE_SERVER_URL || 'http://127.0.0.1:8000',
    serverUrlSource: installed.server_url ? 'installed' : process.env.POWERCONTEXT_ZCODE_SERVER_URL ? 'environment' : 'default',
    allowInsecureHttp: installed.allow_insecure_http === true,
    capturePrompts: capture !== undefined ? !['0', 'false', 'no', 'off'].includes(capture.toLowerCase()) : installed.capture_prompts !== false,
    boundaryFlush: boundary !== undefined ? !['0', 'false', 'no', 'off'].includes(boundary.toLowerCase()) : installed.boundary_flush === true,
    authorization: process.env.POWERCONTEXT_ZCODE_AUTHORIZATION,
    remoteWorkspace: enabled(process.env.POWERCONTEXT_ZCODE_REMOTE_WORKSPACE),
    explicitScopeId: process.env.POWERCONTEXT_ZCODE_SCOPE_ID || undefined,
  }
}
