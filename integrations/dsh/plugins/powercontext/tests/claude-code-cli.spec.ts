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

import { createHash } from 'node:crypto'
import { mkdtemp, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { describe, expect, it, vi } from 'vitest'
import type { LaunchBundle } from '../src/handoff/contracts.ts'
import { createClaudeCodeCliAdapter, createClaudeCodeCliTarget, createDefaultClaudeCodeCliAdapter,
  createDeferredClaudeCodeCliTarget, type ClaudeCliReceiver } from '../src/handoff/adapters/claude-code-cli.ts'
import type { ClaudeIntegration } from '../src/handoff/adapters/claude-runtime.ts'

const probe = vi.hoisted(() => vi.fn())
vi.mock('../src/handoff/adapters/claude-code-cli-probe.ts', () => ({ probeClaudeCodeCli: probe }))

const target = { state: 'present', version: '2.1.301', configurationKey: 'shared-claude-profile' } as const
const signal = new AbortController().signal
const env = { effectiveCwd: '/workspace', serverIdentity: 'server', scopeId: 'scope',
  configurationGeneration: 'generation', newSession: true, unsentPrefill: true, packageAccess: true }

function fixture() {
  probe.mockReset().mockResolvedValue({ state: 'present', version: target.version, executable: '/bin/claude' })
  const integration: ClaudeIntegration = { configurationKey: target.configurationKey,
    check: vi.fn().mockResolvedValue({ state: 'ready' }), install: vi.fn().mockResolvedValue({ state: 'completed' }) }
  const receiver: ClaudeCliReceiver = { handler: vi.fn().mockResolvedValue('ready'),
    environment: vi.fn().mockResolvedValue(env), canReadPackage: vi.fn().mockResolvedValue(true) }
  const adapter = createClaudeCodeCliAdapter({ integration, receiver, qualification: { targetVersion: target.version,
    evidence: 'fixture qualification' }, buildReceiverPrompt: () => 'Please review the handoff before continuing' })
  if (!adapter.qualification) throw new Error('test needs a qualified adapter')
  return { adapter, integration, receiver }
}

function bundle(packagePath: string, packageSha256: string): LaunchBundle {
  return { deliveryId: 'delivery', packagePath, packageSha256,
    source: { sessionId: 'session', messageId: 'message', throughSeq: 1, cwd: '/workspace', workspaceObservedAt: 'now' },
    binding: { preflightId: 'preflight', revision: 1, source: { sessionId: 'session', messageId: 'message', throughSeq: 1,
      cwd: '/workspace', workspaceObservedAt: 'now' }, targetId: 'claude-code-cli', targetVersion: target.version,
      adapterVersion: '1', effectiveCwd: '/workspace', serverIdentity: 'server', connectionRef: 'connection',
      connectionGeneration: 1, scopeId: 'scope' },
    handoffSelection: { kind: 'prepared', digest: 'digest' },
    receiptAttempt: { receiptAttemptId: 'receipt', sourceType: 'content', sourceId: 'source' } }
}

describe('Claude Code CLI adapter', () => {
  it('detects an unqualified CLI without allowing native delivery', async () => {
    const { integration } = fixture()
    const adapter = createClaudeCodeCliAdapter({ integration })
    expect(createClaudeCodeCliAdapter().qualification).toBeUndefined()
    expect(adapter.qualification).toBeUndefined()
    expect(await adapter.detect(signal)).toEqual(target)
    expect(await adapter.installIntegration(target, signal)).toEqual({ state: 'needs_user_action', action: 'configure_target' })
    expect(() => adapter.launchIntent(target, bundle('/tmp/handoff', 'x'))).toThrow()
    const invalid = createClaudeCodeCliAdapter({ integration, qualification: { targetVersion: target.version, evidence: '' } })
    expect(invalid.qualification).toBeUndefined()
  })

  it('creates a detectable unqualified target when PowerContext is missing from PATH', async () => {
    fixture()
    const dir = await mkdtemp(join(tmpdir(), 'pc-claude-registry-'))
    try {
      const adapter = await createClaudeCodeCliTarget({ integrationOptions: { configDir: dir,
        environment: { PATH: '/usr/bin:/bin' } } })
      expect(adapter.qualification).toBeUndefined()
      const detection = await adapter.detect(signal)
      expect(detection.state).toBe('present')
      if (detection.state !== 'present') throw new Error('fixture CLI not detected')
      expect(await adapter.checkIntegration(detection, signal)).toEqual({ state: 'needs_user_action', action: 'configure_target' })
      expect(() => adapter.launchIntent(detection, bundle('/tmp/handoff', 'x'))).toThrow()
      const missing = await createClaudeCodeCliTarget({ integrationOptions: { configDir: dir,
        environment: { PATH: '/usr/bin:/bin' }, powercontextExecutable: join(dir, 'missing') } })
      expect(await missing.checkIntegration(detection, signal)).toEqual({ state: 'needs_user_action', action: 'configure_target' })
    } finally {
      await rm(dir, { recursive: true, force: true })
    }
  })

  it('offers a synchronous registry entry without a PowerContext executable', async () => {
    fixture()
    const dir = await mkdtemp(join(tmpdir(), 'pc-claude-default-'))
    try {
      const adapter = createDefaultClaudeCodeCliAdapter({ integrationOptions: { configDir: dir,
        environment: { PATH: '/usr/bin:/bin' } } })
      expect(adapter.qualification).toBeUndefined()
      const detection = await adapter.detect(signal)
      if (detection.state !== 'present') throw new Error('fixture CLI not detected')
      expect(await adapter.checkIntegration(detection, signal)).toEqual({ state: 'needs_user_action', action: 'configure_target' })
      expect(() => adapter.launchIntent(detection, bundle('/tmp/handoff', 'x'))).toThrow()
    } finally {
      await rm(dir, { recursive: true, force: true })
    }
  })

  it('allows a qualified CLI with the receiving plugin already installed and no setup executable', async () => {
    const { receiver } = fixture()
    const dir = await mkdtemp(join(tmpdir(), 'pc-claude-configured-'))
    try {
      const executable = join(dir, 'claude')
      const writeInventory = (enabled: boolean) => writeFile(executable,
        `#!/bin/sh\nprintf '%s' '[{"id":"powercontext@powercontext","enabled":${enabled}}]'\n`, { mode: 0o755 })
      await writeInventory(true)
      probe.mockResolvedValue({ state: 'present', version: target.version, executable })
      const adapter = createDeferredClaudeCodeCliTarget({
        receiver, buildReceiverPrompt: () => 'Review the handoff',
        qualification: { targetVersion: target.version, evidence: 'fixture' },
        integrationOptions: { configDir: dir, environment: { HOME: dir, PATH: '/usr/bin:/bin' },
          powercontextExecutable: join(dir, 'missing') },
      })
      const detected = await adapter.detect(signal)
      if (detected.state !== 'present') throw new Error('fixture CLI not detected')
      expect(await adapter.checkIntegration(detected, signal)).toEqual({ state: 'ready' })
      expect(await adapter.resolveEnvironment(detected, '/workspace', signal)).toEqual(env)
      expect(adapter.launchIntent(detected, bundle('/tmp/handoff', 'x'))).toEqual({ draft: 'Review the handoff' })
      await writeInventory(false)
      expect(await adapter.checkIntegration(detected, signal)).toEqual({ state: 'needs_user_action', action: 'configure_target' })
    } finally {
      await rm(dir, { recursive: true, force: true })
    }
  })

  it('requires a detected, version-qualified CLI and the registered handler', async () => {
    const { adapter, receiver, integration } = fixture()
    expect(await adapter.detect(signal)).toEqual(target)
    vi.mocked(receiver.handler).mockResolvedValueOnce('needs_user_action').mockResolvedValueOnce('unknown')
    expect(await adapter.checkIntegration(target, signal)).toEqual({ state: 'needs_user_action', action: 'configure_target' })
    expect(await adapter.checkIntegration(target, signal)).toEqual({ state: 'unknown' })
    expect(integration.check).not.toHaveBeenCalled()
    expect(await adapter.checkIntegration(target, signal)).toEqual({ state: 'ready' })
    vi.mocked(integration.check).mockResolvedValueOnce({ state: 'missing', repairable: true })
    expect(await adapter.checkIntegration(target, signal)).toEqual({ state: 'missing', repairable: true })
    probe.mockResolvedValueOnce({ state: 'present', version: '3.0.0', executable: '/bin/claude' })
    expect(await adapter.detect(signal)).toEqual({ state: 'unsupported' })
    probe.mockResolvedValueOnce({ state: 'missing' })
    expect(await adapter.detect(signal)).toEqual({ state: 'missing' })
  })

  it('uses the shared installer and rejects changed target identity', async () => {
    const { adapter, integration } = fixture()
    expect(await adapter.installIntegration(target, signal)).toEqual({ state: 'completed' })
    expect(integration.install).toHaveBeenCalledWith(signal)
    await expect(adapter.installIntegration({ ...target, configurationKey: 'different' }, signal)).rejects.toMatchObject({ code: 'binding_mismatch' })
    expect(integration.install).toHaveBeenCalledTimes(1)
    const controller = new AbortController()
    controller.abort()
    await expect(adapter.installIntegration(target, controller.signal)).rejects.toThrow()
  })

  it('requires observations of the destination session and never invents routing', async () => {
    const { adapter, receiver } = fixture()
    expect(await adapter.resolveEnvironment(target, '/workspace', signal)).toEqual(env)
    vi.mocked(receiver.environment).mockResolvedValueOnce(undefined)
    await expect(adapter.resolveEnvironment(target, '/workspace', signal)).rejects.toMatchObject({ code: 'integration_unavailable' })
    vi.mocked(receiver.environment).mockResolvedValueOnce({ ...env, unsentPrefill: false })
    await expect(adapter.resolveEnvironment(target, '/workspace', signal)).rejects.toMatchObject({ code: 'integration_unavailable' })
    vi.mocked(receiver.environment).mockResolvedValueOnce({ ...env, effectiveCwd: '/wrong' })
    await expect(adapter.resolveEnvironment(target, '/workspace', signal)).rejects.toMatchObject({ code: 'integration_unavailable' })
    await expect(adapter.resolveEnvironment(target, '/a/../workspace', signal)).rejects.toMatchObject({ code: 'binding_mismatch' })
  })

  it('checks exact bytes and receiver access before building the unsent draft', async () => {
    const { adapter, receiver } = fixture()
    const dir = await mkdtemp(join(tmpdir(), 'pc-claude-handoff-'))
    try {
      const path = join(dir, 'package.json')
      const content = '{"handoff":"example"}'
      await writeFile(path, content)
      const digest = createHash('sha256').update(content).digest('hex')
      const delivery = bundle(path, digest)
      expect(await adapter.canReadPackage(target, delivery, signal)).toBe(true)
      vi.mocked(receiver.canReadPackage).mockResolvedValueOnce(false)
      expect(await adapter.canReadPackage(target, delivery, signal)).toBe(false)
      await writeFile(path, 'different')
      expect(await adapter.canReadPackage(target, delivery, signal)).toBe(false)
      expect(adapter.launchIntent(target, delivery)).toEqual({ draft: 'Please review the handoff before continuing' })
      expect(() => adapter.launchIntent(target, { ...delivery, binding: { ...delivery.binding, targetId: 'codex-desktop' } })).toThrow()
    } finally {
      await rm(dir, { recursive: true, force: true })
    }
  })
})
