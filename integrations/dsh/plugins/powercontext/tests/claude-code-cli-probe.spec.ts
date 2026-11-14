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

import { strict as assert } from 'node:assert'
import { mkdtemp, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { delimiter, join } from 'node:path'
import { test, vi } from 'vitest'

const runVersion = vi.hoisted(() => vi.fn())
vi.mock('node:child_process', () => {
  const execFile = Object.assign(() => {}, { [Symbol.for('nodejs.util.promisify.custom')]: runVersion })
  return { execFile }
})
import { probeClaudeCodeCli } from '../src/handoff/adapters/claude-code-cli-probe.ts'

test('detects a CLI using only the safe version query', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'pc-claude-probe-'))
  try {
    const executable = join(directory, 'claude')
    await writeFile(executable, '', { mode: 0o755 })
    const signal = new AbortController().signal
    runVersion.mockReset().mockResolvedValueOnce({ stdout: '2.1.301 (Claude Code)\n' })
    assert.deepEqual(await probeClaudeCodeCli(signal, directory), {
      state: 'present', executable, version: '2.1.301',
    })
    assert.deepEqual(runVersion.mock.calls[0]?.slice(0, 2), [executable, ['--version']])
    assert.equal(runVersion.mock.calls[0]?.[2].signal, signal)
    runVersion.mockResolvedValueOnce({ stdout: 'unrecognized\n' })
    assert.deepEqual(await probeClaudeCodeCli(signal, directory), { state: 'unknown' })
  } finally {
    await rm(directory, { recursive: true, force: true })
  }
}, 20_000)

test('reports absence and propagates cancellation', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'pc-claude-probe-'))
  try {
    assert.deepEqual(await probeClaudeCodeCli(new AbortController().signal, directory), { state: 'missing' })
    assert.deepEqual(await probeClaudeCodeCli(new AbortController().signal, `relative${delimiter}${directory}`), { state: 'unknown' })
    const controller = new AbortController()
    controller.abort(new Error('cancelled'))
    await assert.rejects(probeClaudeCodeCli(controller.signal, directory), /cancelled/)
  } finally {
    await rm(directory, { recursive: true, force: true })
  }
})
