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

import { execFile } from 'node:child_process'
import { constants } from 'node:fs'
import { access, stat } from 'node:fs/promises'
import { delimiter, isAbsolute, join } from 'node:path'
import { promisify } from 'node:util'

const execute = promisify(execFile)

export type ClaudeCodeCliProbe =
  | { state: 'present'; executable: string; version: string }
  | { state: 'missing' | 'unknown' }

/**
 * Probe the same PATH that ClaudeInstaller uses, without starting an interactive
 * session or modifying Claude configuration. Presence is not qualification: a
 * registered URL handler and the receiver's routing still require verification.
 */
export async function probeClaudeCodeCli(signal: AbortSignal, path = process.env.PATH ?? ''): Promise<ClaudeCodeCliProbe> {
  signal.throwIfAborted()
  for (const directory of path.split(delimiter)) {
    // Relative entries depend on the installer cwd, so do not skip one and select a different binary.
    if (!isAbsolute(directory)) return { state: 'unknown' }
    const executable = join(directory, 'claude')
    try {
      await access(executable, constants.X_OK)
      if (!(await stat(executable)).isFile()) continue
    } catch (error) {
      if (signal.aborted) signal.throwIfAborted()
      const code = (error as NodeJS.ErrnoException).code
      if (code === 'ENOENT' || code === 'ENOTDIR' || code === 'EACCES') continue
      return { state: 'unknown' }
    }
    try {
      const { stdout } = await execute(executable, ['--version'], {
        signal, timeout: 5_000, maxBuffer: 4_096, encoding: 'utf8',
      })
      signal.throwIfAborted()
      const match = /^(\d+\.\d+\.\d+(?:[-+][\w.-]+)?)\s+\(Claude Code\)\s*$/u.exec(stdout)
      return match ? { state: 'present', executable, version: match[1]! } : { state: 'unknown' }
    } catch {
      if (signal.aborted) signal.throwIfAborted()
      return { state: 'unknown' }
    }
  }
  signal.throwIfAborted()
  return { state: 'missing' }
}
