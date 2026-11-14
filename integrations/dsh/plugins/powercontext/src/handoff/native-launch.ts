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


import { spawn } from 'node:child_process'
import { isAbsolute } from 'node:path'
import { containsSecret } from '../secrets.ts'
import { HandoffError } from './errors.ts'
import type { NativeLaunchIntent, NativeTargetId } from './targets.ts'

const DESTINATIONS = {
  'codex-desktop': ['codex://threads/new', 'path', 'prompt'],
  'claude-desktop': ['claude://code/new', 'folder', 'q'],
  'claude-code-cli': ['claude-cli://open', 'cwd', 'q'],
} as const

const SAFE_ENV = { PATH: '/usr/bin:/bin', LANG: 'C' }
const OPEN_TIMEOUT_MS = 3_000

export interface SystemLaunch {
  dispatch(): Promise<'opened' | 'failed' | 'unknown'>
}

export type LaunchPlanner = (target: NativeTargetId, effectiveCwd: string, intent: NativeLaunchIntent) => SystemLaunch

export function nativeLaunchUrl(
  target: NativeTargetId, cwd: string, { draft }: NativeLaunchIntent, authorization?: string,
): string {
  const route = DESTINATIONS[target]
  if (!route || !isAbsolute(cwd) || /[\x00-\x1f\x7f]/u.test(cwd) || typeof draft !== 'string' || !draft.trim()
    || /[\x00-\x08\x0b-\x1f\x7f]|[\uD800-\uDFFF]/u.test(draft)
    || Buffer.byteLength(draft) > 2_048) {
    throw new HandoffError('payload_invalid')
  }
  if (containsSecret(draft, authorization) || containsSecret(cwd, authorization)) {
    throw new HandoffError('secret_rejected')
  }
  const [base, cwdKey, draftKey] = route
  const uri = `${base}?${cwdKey}=${encodeURIComponent(cwd)}&${draftKey}=${encodeURIComponent(draft)}`
  if (Buffer.byteLength(uri) > 4_096) throw new HandoffError('payload_invalid')
  return uri
}

export function planNativeLaunch(
  target: NativeTargetId, cwd: string, intent: NativeLaunchIntent, authorization?: string,
): SystemLaunch {
  if (process.platform !== 'darwin') throw new HandoffError('deployment_unsupported')
  const uri = nativeLaunchUrl(target, cwd, intent, authorization)
  return {
    dispatch() {
      return new Promise(resolve => {
        const child = spawn('/usr/bin/open', [uri], { shell: false, env: SAFE_ENV, stdio: 'ignore', cwd: '/' })
        let started = false
        const timer = setTimeout(() => {
          // The OS may have accepted the request even if the opener is still running.
          child.kill()
          resolve('unknown')
        }, OPEN_TIMEOUT_MS)
        child.once('spawn', () => { started = true })
        child.once('error', () => {
          clearTimeout(timer)
          resolve(started ? 'unknown' : 'failed')
        })
        child.once('exit', (code, signal) => {
          clearTimeout(timer)
          resolve(signal !== null || code === null ? 'unknown' : code === 0 ? 'opened' : 'failed')
        })
      })
    },
  }
}
