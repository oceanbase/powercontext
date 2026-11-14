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

import { test } from 'vitest'
import type { LaunchBundle } from '../src/handoff/contracts.ts'
import type { IntegrationCheck } from '../src/handoff/targets.ts'
import assert from 'node:assert/strict'
import { chmod, mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { inspectClaudeDesktopBundle } from '../src/handoff/adapters/claude-desktop.ts'

test.skipIf(process.platform !== 'darwin')('reads bundle identity and protocol without launching it', async () => {
  const root = await mkdtemp(join(tmpdir(), 'claude-bundle-test-'))
  const app = join(root, 'Claude.app')
  const contents = join(app, 'Contents')
  const signal = new AbortController().signal
  try {
    assert.deepEqual(await inspectClaudeDesktopBundle(app, signal), { state: 'missing' })
    await mkdir(join(contents, 'MacOS'), { recursive: true })
    const executable = join(contents, 'MacOS', 'Claude')
    await writeFile(executable, '#!/bin/sh\nexit 1\n')
    await chmod(executable, 0o700)
    const plist = (id: string, scheme: string) => `<?xml version="1.0" encoding="UTF-8"?><plist version="1.0"><dict>
      <key>CFBundleIdentifier</key><string>${id}</string>
      <key>CFBundleShortVersionString</key><string>4.29.0</string>
      <key>CFBundleExecutable</key><string>Claude</string>
      <key>CFBundleURLTypes</key><array><dict><key>CFBundleURLSchemes</key><array><string>${scheme}</string></array></dict></array>
      </dict></plist>`
    const info = join(contents, 'Info.plist')
    await writeFile(info, plist('com.anthropic.claudefordesktop', 'claude'))
    assert.deepEqual(await inspectClaudeDesktopBundle(app, signal), { state: 'present', version: '4.29.0' })
    await writeFile(info, plist('com.example.other', 'claude'))
    assert.deepEqual(await inspectClaudeDesktopBundle(app, signal), { state: 'invalid' })
    await writeFile(info, plist('com.anthropic.claudefordesktop', 'other'))
    assert.deepEqual(await inspectClaudeDesktopBundle(app, signal), { state: 'invalid' })
    await writeFile(info, 'broken')
    assert.deepEqual(await inspectClaudeDesktopBundle(app, signal), { state: 'unknown' })
    const controller = new AbortController()
    controller.abort()
    await assert.rejects(inspectClaudeDesktopBundle(app, controller.signal), { name: 'AbortError' })
  } finally {
    await rm(root, { recursive: true, force: true })
  }
})

test.skipIf(process.platform !== 'darwin')('Desktop adapter gates unobserved runtime and delegates qualified runtime checks', async () => {
  const { createClaudeDesktopAdapter } = await import('../src/handoff/adapters/claude-desktop.ts')
  const { nativeLaunchUrl } = await import('../src/handoff/native-launch.ts')
  const root = await mkdtemp(join(tmpdir(), 'claude-adapter-test-'))
  const app = join(root, 'Claude.app')
  const contents = join(app, 'Contents')
  const signal = new AbortController().signal
  const options = { appPath: app, configDir: join(root, 'config'), inspectHandler: async () => 'match' as const, qualification: { appVersion: '4.29.0', evidence: 'test fixture only' },
    buildReceiverPrompt: (bundle: LaunchBundle) => `Read ${bundle.packagePath}` }
  try {
    await mkdir(join(contents, 'MacOS'), { recursive: true })
    const executable = join(contents, 'MacOS', 'Claude')
    await writeFile(executable, '#!/bin/sh\nexit 1\n')
    await chmod(executable, 0o700)
    await writeFile(join(contents, 'Info.plist'), `<?xml version="1.0"?><plist version="1.0"><dict>
      <key>CFBundleIdentifier</key><string>com.anthropic.claudefordesktop</string>
      <key>CFBundleShortVersionString</key><string>4.29.0</string>
      <key>CFBundleExecutable</key><string>Claude</string>
      <key>CFBundleURLTypes</key><array><dict><key>CFBundleURLSchemes</key><array><string>claude</string></array></dict></array>
      </dict></plist>`)
    const manual = createClaudeDesktopAdapter({ appPath: app })
    assert.equal(manual.qualification, undefined)
    assert.equal(Object.hasOwn(manual, 'qualification'), false)
    const target = await manual.detect(signal)
    assert.deepEqual(target, { state: 'present', version: '4.29.0', configurationKey: 'claude-desktop:manual-configuration' })
    if (target.state !== 'present') throw new Error('fixture was not detected')
    assert.deepEqual(await manual.checkIntegration(target, signal), { state: 'needs_user_action', action: 'configure_target' })
    assert.deepEqual(await manual.installIntegration(target, signal), { state: 'needs_user_action', action: 'configure_target' })
    await assert.rejects(manual.resolveEnvironment(target, root, signal), { code: 'integration_unavailable' })
    const bundle = { packagePath: '/tmp/example.json', binding: { targetId: 'claude-desktop', targetVersion: '4.29.0' } } as LaunchBundle
    assert.equal(await manual.canReadPackage(target, bundle, signal), false)
    assert.throws(() => manual.launchIntent(target, bundle), { code: 'integration_unavailable' })
    assert.equal(createClaudeDesktopAdapter({ ...options, qualification: { appVersion: '4.29.0', evidence: '' } }).qualification, undefined)
    assert.equal(createClaudeDesktopAdapter(options).qualification, undefined)

    const environment = { effectiveCwd: root, serverIdentity: 'server', scopeId: 'scope',
      configurationGeneration: 'generation', newSession: true, unsentPrefill: true, packageAccess: true }
    let integration: IntegrationCheck = { state: 'needs_user_action', action: 'restart_target' }
    let readable = false
    const runtime = {
      configurationKey: 'shared-claude-test-config',
      async checkIntegration() { return integration },
      async resolveEnvironment(_target: unknown, cwd: string) { assert.equal(cwd, root); return environment },
      async canReadPackage(_target: unknown, value: LaunchBundle) { assert.equal(value, bundle); return readable },
    }
    const unqualifiedRuntime = createClaudeDesktopAdapter({ appPath: app, runtime })
    assert.equal(unqualifiedRuntime.qualification, undefined)
    const observed = await unqualifiedRuntime.detect(signal)
    if (observed.state !== 'present') throw new Error('fixture was not detected')
    assert.deepEqual(await unqualifiedRuntime.checkIntegration(observed, signal), { state: 'needs_user_action', action: 'configure_target' })
    assert.equal(await unqualifiedRuntime.canReadPackage(observed, bundle, signal), false)
    assert.throws(() => unqualifiedRuntime.launchIntent(observed, bundle), { code: 'integration_unavailable' })
    assert.equal(createClaudeDesktopAdapter({ ...options, runtime, qualification: { appVersion: '4.29.0', evidence: '  ' } }).qualification, undefined)
    assert.equal(createClaudeDesktopAdapter({ ...options, runtime, buildReceiverPrompt: undefined }).qualification, undefined)
    const qualified = createClaudeDesktopAdapter({ ...options, runtime })
    if (!qualified.qualification) throw new Error('unexpected manual facade')
    const wrongHandler = createClaudeDesktopAdapter({ ...options, runtime, inspectHandler: async () => 'different' })
    const unknownHandler = createClaudeDesktopAdapter({ ...options, runtime, inspectHandler: async () => 'unknown' })
    if (!wrongHandler.qualification || !unknownHandler.qualification) throw new Error('unexpected facade')
    const qualifiedTarget = await qualified.detect(signal)
    if (qualifiedTarget.state !== 'present') throw new Error('fixture was not detected')
    assert.equal(qualifiedTarget.configurationKey, runtime.configurationKey)
    assert.deepEqual(await wrongHandler.checkIntegration(qualifiedTarget, signal), { state: 'needs_user_action', action: 'configure_target' })
    assert.deepEqual(await unknownHandler.checkIntegration(qualifiedTarget, signal), { state: 'unknown' })
    assert.deepEqual(await createClaudeDesktopAdapter({ ...options, runtime, qualification: { ...options.qualification, appVersion: '4.28' } }).detect(signal), { state: 'unsupported' })
    assert.deepEqual(await qualified.checkIntegration(qualifiedTarget, signal), integration)
    integration = { state: 'disabled', repairable: false }
    assert.deepEqual(await qualified.checkIntegration(qualifiedTarget, signal), integration)
    integration = { state: 'ready' }
    assert.deepEqual(await qualified.checkIntegration(qualifiedTarget, signal), { state: 'ready' })
    assert.deepEqual(await qualified.resolveEnvironment(qualifiedTarget, root, signal), environment)
    assert.equal(await qualified.canReadPackage(qualifiedTarget, bundle, signal), false)
    readable = true
    assert.equal(await qualified.canReadPackage(qualifiedTarget, bundle, signal), true)
    const url = new URL(nativeLaunchUrl(qualified.id, root, qualified.launchIntent(qualifiedTarget, bundle)))
    assert.equal(`${url.protocol}//${url.host}${url.pathname}`, 'claude://code/new')
    assert.equal(url.searchParams.get('folder'), root)
    assert.equal(url.searchParams.get('q'), 'Read /tmp/example.json')
    assert.throws(() => qualified.launchIntent(qualifiedTarget, { ...bundle, binding: { ...bundle.binding, targetVersion: 'other' } }), { code: 'binding_mismatch' })
    await assert.rejects(qualified.checkIntegration({ ...qualifiedTarget, configurationKey: 'wrong' }, signal), { code: 'binding_mismatch' })
    const controller = new AbortController()
    controller.abort()
    await assert.rejects(qualified.checkIntegration(qualifiedTarget, controller.signal), { name: 'AbortError' })
  } finally {
    await rm(root, { recursive: true, force: true })
  }
})


test('advisory plugin registration distinguishes disabled, missing and unreadable settings', async () => {
  const { inspectClaudePluginRegistration } = await import('../src/handoff/adapters/claude-desktop.ts')
  const root = await mkdtemp(join(tmpdir(), 'claude-registration-test-'))
  const signal = new AbortController().signal
  try {
    assert.equal(await inspectClaudePluginRegistration(root, signal), 'missing')
    await mkdir(join(root, 'plugins'))
    const settings = join(root, 'settings.json')
    const inventory = join(root, 'plugins', 'installed_plugins.json')
    await writeFile(settings, JSON.stringify({ enabledPlugins: { 'powercontext@powercontext': true } }))
    await writeFile(inventory, JSON.stringify({ plugins: { 'powercontext@powercontext': [{ scope: 'user' }] } }))
    assert.equal(await inspectClaudePluginRegistration(root, signal), 'registered')
    await writeFile(settings, JSON.stringify({ enabledPlugins: { 'powercontext@powercontext': false } }))
    assert.equal(await inspectClaudePluginRegistration(root, signal), 'disabled')
    await writeFile(inventory, JSON.stringify({ plugins: {} }))
    assert.equal(await inspectClaudePluginRegistration(root, signal), 'missing')
    await writeFile(settings, '{')
    assert.equal(await inspectClaudePluginRegistration(root, signal), 'unknown')
  } finally {
    await rm(root, { recursive: true, force: true })
  }
})


test.skipIf(process.platform !== 'darwin')('Desktop calls the shared doctor and never treats CLI readiness as Desktop readiness', async () => {
  const { createClaudeDesktopAdapterWithIntegration } = await import('../src/handoff/adapters/claude-desktop.ts')
  const { claudeConfigurationKey } = await import('../src/handoff/adapters/claude-runtime.ts')
  const root = await mkdtemp(join(tmpdir(), 'claude-shared-desktop-'))
  try {
    const app = join(root, 'Claude.app')
    const contents = join(app, 'Contents')
    const configDir = join(root, 'config')
    const executable = join(root, 'powercontext')
    const record = join(root, 'calls')
    await mkdir(join(contents, 'MacOS'), { recursive: true })
    await writeFile(join(contents, 'MacOS', 'Claude'), '#!/bin/sh\nexit 1\n', { mode: 0o700 })
    await writeFile(join(contents, 'Info.plist'), `<?xml version="1.0"?><plist version="1.0"><dict>
      <key>CFBundleIdentifier</key><string>com.anthropic.claudefordesktop</string>
      <key>CFBundleShortVersionString</key><string>4.29.0</string>
      <key>CFBundleExecutable</key><string>Claude</string>
      <key>CFBundleURLTypes</key><array><dict><key>CFBundleURLSchemes</key><array><string>claude</string></array></dict></array>
      </dict></plist>`)
    await writeFile(executable, `#!/bin/sh\nprintf '%s\\n' "$*" >> "$RECORD"\nprintf '%s' '{"hosts":{"claude-code":{"checks":{"claude_code":{"status":"ok"},"plugin":{"status":"ok"},"server_liveness":{"status":"ok"},"server_readiness":{"status":"ok"}}}}}'\n`, { mode: 0o700 })
    const adapter = await createClaudeDesktopAdapterWithIntegration({
      appPath: app, inspectHandler: async () => 'match',
      qualification: { appVersion: '4.29.0', evidence: 'test fixture only' },
      buildReceiverPrompt: () => 'draft',
      runtime: { configurationKey: await claudeConfigurationKey(configDir),
        async checkIntegration() { return { state: 'needs_user_action' as const, action: 'configure_target' as const } },
        async resolveEnvironment() { throw new Error('fixture has no environment') },
        async canReadPackage() { return false } },
      integrationOptions: { configDir, powercontextExecutable: executable, environment: { PATH: '/usr/bin:/bin', RECORD: record } },
    })
    const signal = new AbortController().signal
    if (!adapter.qualification) throw new Error('unexpected manual facade')
    const target = await adapter.detect(signal)
    if (target.state !== 'present') throw new Error('fixture was not detected')
    assert.equal(target.configurationKey, await claudeConfigurationKey(configDir))
    assert.deepEqual(await adapter.checkIntegration(target, signal), { state: 'needs_user_action', action: 'configure_target' })
    assert.equal(await readFile(record, 'utf8'), 'doctor claude-code --json\n')
    assert.deepEqual(await adapter.installIntegration(target, signal), { state: 'needs_user_action', action: 'configure_target' })
    assert.equal(await readFile(record, 'utf8'), 'doctor claude-code --json\n')
  } finally { await rm(root, { recursive: true, force: true }) }
})
