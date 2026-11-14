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

import { mkdtemp, mkdir, readFile, realpath, rm, symlink, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { expect, it } from 'vitest'
import { claudeConfigurationKey, createClaudeIntegration } from '../src/handoff/adapters/claude-runtime.ts'

it('shares one configuration key across symlinks and missing descendants', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'pc-claude-config-'))
  try {
    await mkdir(join(dir, 'real'))
    await symlink(join(dir, 'real'), join(dir, 'alias'))
    expect(await claudeConfigurationKey(join(dir, 'real', 'new')))
      .toBe(await claudeConfigurationKey(join(dir, 'alias', 'new')))
    await expect(claudeConfigurationKey('relative')).rejects.toThrow()
  } finally { await rm(dir, { recursive: true, force: true }) }
})

it('calls the existing doctor and setup using the same explicit Claude profile', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'pc-claude-setup-'))
  try {
    const executable = join(dir, 'powercontext')
    const record = join(dir, 'record')
    await writeFile(executable, `#!/bin/sh\nprintf '%s\\n' "$CLAUDE_CONFIG_DIR" "$*" >> "$RECORD"\nif [ "$1" = doctor ]; then\n printf '%s' '{"hosts":{"claude-code":{"checks":{"claude_code":{"status":"ok"},"plugin":{"status":"ok"},"server_liveness":{"status":"ok"},"server_readiness":{"status":"ok"}}}}}'\nelse\n printf '%s' '{"hosts":{"claude-code":{"setup":"complete"}}}'\nfi\n`, { mode: 0o755 })
    const service = await createClaudeIntegration({ configDir: join(dir, 'profile'), powercontextExecutable: executable,
      environment: { PATH: '/usr/bin:/bin', RECORD: record }, serverUrl: 'http://127.0.0.1:8000' })
    const signal = new AbortController().signal
    expect(await service.check(signal)).toEqual({ state: 'ready' })
    await writeFile(executable, `#!/bin/sh\nprintf '%s' '{"hosts":{"claude-code":{"checks":{"claude_code":{"status":"ok"},"plugin":{"status":"ok"},"server_readiness":{"status":"failed"}}}}}'\n`, { mode: 0o755 })
    expect(await service.check(signal)).toEqual({ state: 'needs_user_action', action: 'configure_target' })
    await writeFile(executable, `#!/bin/sh\nprintf '%s\\n' "$CLAUDE_CONFIG_DIR" "$*" >> "$RECORD"\nprintf '%s' '{"hosts":{"claude-code":{"setup":"complete"}}}'\n`, { mode: 0o755 })
    expect(await service.install(signal)).toEqual({ state: 'completed' })
    const profile = join(await realpath(dir), 'profile')
    expect(await readFile(record, 'utf8')).toBe(`${profile}\ndoctor claude-code --json\n${profile}\nsetup claude-code --json --server-url http://127.0.0.1:8000\n`)
  } finally { await rm(dir, { recursive: true, force: true }) }
}, 20_000)

it('preserves an unset default Claude configuration directory', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'pc-claude-default-config-'))
  try {
    const executable = join(dir, 'powercontext')
    const record = join(dir, 'record')
    await writeFile(executable, `#!/bin/sh\nprintf '%s' "\${CLAUDE_CONFIG_DIR-unset}" > "$RECORD"\nprintf '%s' '{"hosts":{"claude-code":{"checks":{"claude_code":{"status":"ok"},"plugin":{"status":"ok"},"server_readiness":{"status":"ok"}}}}}'\n`, { mode: 0o755 })
    const service = await createClaudeIntegration({ configDir: join(dir, '.claude'), powercontextExecutable: executable,
      environment: { HOME: dir, PATH: '/usr/bin:/bin', RECORD: record } })
    expect(await service.check(new AbortController().signal)).toEqual({ state: 'ready' })
    expect(await readFile(record, 'utf8')).toBe('unset')
    const explicit = await createClaudeIntegration({ configDir: join(dir, '.claude'), powercontextExecutable: executable,
      environment: { HOME: dir, PATH: '/usr/bin:/bin', RECORD: record, CLAUDE_CONFIG_DIR: join(dir, '.claude') } })
    expect(await explicit.check(new AbortController().signal)).toEqual({ state: 'ready' })
    expect(await readFile(record, 'utf8')).toBe(join(dir, '.claude'))
    await expect(createClaudeIntegration({ configDir: join(dir, '.claude'), powercontextExecutable: executable,
      environment: { HOME: dir, CLAUDE_CONFIG_DIR: join(dir, 'different') } })).rejects.toThrow(/does not match/)
  } finally { await rm(dir, { recursive: true, force: true }) }
}, 20_000)

it('reports an absent plugin and treats malformed or interrupted setup as unknown', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'pc-claude-setup-'))
  try {
    const executable = join(dir, 'powercontext')
    await writeFile(executable, `#!/bin/sh\nif [ "$1" = doctor ]; then\n printf '%s' '{"hosts":{"claude-code":{"checks":{"claude_code":{"status":"ok"},"plugin":{"status":"failed","detail":"PowerContext plugin is not installed"}}}}}'\nelse\n printf '%s' 'bad result'\nfi\n`, { mode: 0o755 })
    const service = await createClaudeIntegration({ configDir: dir, powercontextExecutable: executable, environment: { PATH: '/usr/bin:/bin' } })
    expect(await service.check(new AbortController().signal)).toEqual({ state: 'missing', repairable: true })
    expect(await service.install(new AbortController().signal)).toEqual({ state: 'unknown' })
    const controller = new AbortController()
    controller.abort()
    await expect(service.install(controller.signal)).rejects.toThrow()
  } finally { await rm(dir, { recursive: true, force: true }) }
}, 20_000)

it('does not settle a cancelled installer while a child can still write', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'pc-claude-cancel-'))
  const controller = new AbortController()
  let pending: ReturnType<Awaited<ReturnType<typeof createClaudeIntegration>>['install']> | undefined
  try {
    const executable = join(dir, 'powercontext')
    const marker = join(dir, 'started')
    const write = join(dir, 'late-write')
    await writeFile(executable, `#!/bin/sh\n(/bin/sleep 30; printf unsafe > "$LATE_WRITE") &\nprintf '%s' "$!" > "$MARKER"\nwait\n`, { mode: 0o755 })
    const service = await createClaudeIntegration({ configDir: dir, powercontextExecutable: executable,
      environment: { PATH: '/usr/bin:/bin', MARKER: marker, LATE_WRITE: write } })
    pending = service.install(controller.signal)
    const waitForStartedChild = async () => {
      for (;;) {
        controller.signal.throwIfAborted()
        try {
          const value = await readFile(marker, 'utf8')
          if (/^[1-9][0-9]*$/.test(value)) return Number(value)
        } catch (error) {
          if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error
        }
        await new Promise(resolve => setTimeout(resolve, 25))
      }
    }
    const childPid = await Promise.race([
      waitForStartedChild(),
      pending.then(result => { throw new Error(`Installer exited before starting its child: ${result.state}`) }),
    ])
    controller.abort()
    expect(await pending).toEqual({ state: 'unknown' })
    try {
      process.kill(childPid, 0)
      throw new Error('Installer child is still running')
    } catch (error) {
      expect((error as NodeJS.ErrnoException).code).toBe('ESRCH')
    }
    await expect(readFile(write)).rejects.toMatchObject({ code: 'ENOENT' })
  } finally {
    controller.abort()
    await pending?.catch(() => undefined)
    await rm(dir, { recursive: true, force: true })
  }
}, 30_000)
