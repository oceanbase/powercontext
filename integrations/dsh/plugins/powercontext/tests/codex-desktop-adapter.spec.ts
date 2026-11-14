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
import { mkdtemp, mkdir, readFile, rm, symlink, writeFile } from 'node:fs/promises'
import { homedir, tmpdir } from 'node:os'
import { join } from 'node:path'
import { promisify } from 'node:util'
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { createCodexDesktopAdapter, type CodexDesktopRuntime } from '../src/handoff/adapters/codex-desktop.ts'
import type { LaunchBundle } from '../src/handoff/contracts.ts'
import { nativeLaunchUrl } from '../src/handoff/native-launch.ts'

const targetVersion = '26.1106.51428 (26605); codex-cli 0.167.0-alpha.4.1'
const signal = new AbortController().signal
const target = { state: 'present', version: targetVersion, configurationKey: 'test-codex-config' } as const
const bundle = { packagePath: '/private/handoff + 中.json', binding: { effectiveCwd: '/tmp/project + 中',
  targetId: 'codex-desktop', targetVersion } } as LaunchBundle
const fallbackTarget = { ...target, configurationKey: `codex:${process.env.CODEX_HOME || join(homedir(), '.codex')}` }
const matchHandler = async () => 'match' as const
let temporary: string | undefined
let executableRoot: string | undefined
let executable: string

beforeAll(async () => {
  if (process.platform !== 'darwin') return
  executableRoot = await mkdtemp(join(tmpdir(), 'pc-codex-test-cli-'))
  executable = join(executableRoot, 'codex')
  await writeFile(executable, `#!/bin/sh
if [ "$1" = "--version" ]; then echo "codex-cli 0.167.0-alpha.4.1"
elif [ -f "$0.invalid" ]; then echo invalid-json
else echo '{"installed":[]}'
fi
`, { mode: 0o755 })
  // Warm the one test helper once; repeatedly creating new executable files can
  // delay macOS execution under parallel tests beyond the real probe deadline.
  await promisify(execFile)(executable, ['--version'], { timeout: 20_000 })
}, 30_000)

afterAll(async () => { if (executableRoot) await rm(executableRoot, { recursive: true, force: true }) })

function runtime(): CodexDesktopRuntime {
  return {
    configurationKey: 'test-codex-config',
    checkIntegration: vi.fn(async () => ({ state: 'unknown' } as const)),
    installIntegration: vi.fn(async () => ({ state: 'needs_user_action', action: 'configure_target' } as const)),
    resolveEnvironment: vi.fn(async () => ({ effectiveCwd: '/tmp/project + 中', serverIdentity: 'server', scopeId: 'scope',
      configurationGeneration: 'generation', newSession: true, unsentPrefill: true, packageAccess: false })),
    canReadPackage: vi.fn(async () => false),
  }
}

async function fixture() {
  temporary = await mkdtemp(join(tmpdir(), 'pc-codex-desktop-'))
  const app = join(temporary, 'ChatGPT.app')
  const cliDir = join(app, 'Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS')
  await mkdir(cliDir, { recursive: true })
  await writeFile(join(app, 'Contents/Info.plist'), `<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict><key>CFBundleIdentifier</key><string>com.openai.codex</string>
<key>CFBundleShortVersionString</key><string>26.1106.51428</string><key>CFBundleVersion</key><string>26605</string>
<key>CFBundleURLTypes</key><array><dict><key>CFBundleURLSchemes</key><array><string>codex</string></array></dict></array>
</dict></plist>`)
  await symlink(executable, join(cliDir, 'codex'))
  return app
}

afterEach(async () => { if (temporary) await rm(temporary, { recursive: true, force: true }); temporary = undefined })

describe('Codex Desktop handoff adapter', () => {
  it.skipIf(process.platform !== 'darwin')('detects a qualified bundle and embedded CLI without PATH', async () => {
    const appPath = await fixture()
    const adapter = createCodexDesktopAdapter({ appPath, runtime: runtime(), buildReceiverPrompt: () => 'draft',
      qualification: { evidence: 'fixture only', targetVersion } })
    expect(await adapter.detect(signal)).toEqual(target)
    const unqualified = createCodexDesktopAdapter({ appPath, runtime: runtime(), buildReceiverPrompt: () => 'draft',
      qualification: { evidence: 'fixture only', targetVersion: 'different build' } })
    expect(await unqualified.detect(signal)).toEqual({ state: 'unsupported' })
    await rm(join(appPath, 'Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex'))
    expect(await adapter.detect(signal)).toEqual({ state: 'unknown' })
    await rm(appPath, { recursive: true })
    expect(await adapter.detect(signal)).toEqual({ state: 'missing' })
  }, 30_000)

  it('uses the actual runtime observations and preserves cancellation', async () => {
    const host = runtime()
    const adapter = createCodexDesktopAdapter({ runtime: host, inspectHandler: matchHandler, buildReceiverPrompt: () => 'draft',
      qualification: { evidence: 'fixture only', targetVersion } })
    expect(await adapter.checkIntegration(target, signal)).toEqual({ state: 'unknown' })
    expect(await adapter.installIntegration(target, signal)).toEqual({ state: 'needs_user_action', action: 'configure_target' })
    expect((await adapter.resolveEnvironment(target, '/tmp/project + 中', signal)).packageAccess).toBe(false)
    expect(await adapter.canReadPackage(target, bundle, signal)).toBe(false)
    const cancelled = AbortSignal.abort()
    await expect(adapter.checkIntegration(target, cancelled)).rejects.toThrow()
    expect(host.checkIntegration).toHaveBeenCalledTimes(1)
  }, 30_000)

  it('blocks unqualified use and rechecks the registered handler before package access', async () => {
    const host = runtime()
    const unqualified = createCodexDesktopAdapter({ runtime: host })
    expect(await unqualified.checkIntegration(target, signal)).toEqual({ state: 'needs_user_action', action: 'configure_target' })
    expect(await unqualified.installIntegration(target, signal)).toEqual({ state: 'needs_user_action', action: 'configure_target' })
    await expect(unqualified.resolveEnvironment(target, '/tmp', signal)).rejects.toThrow()
    expect(await unqualified.canReadPackage(target, bundle, signal)).toBe(false)
    expect(host.installIntegration).not.toHaveBeenCalled()
    const inspectHandler = vi.fn().mockResolvedValueOnce('different').mockResolvedValueOnce('unknown')
      .mockResolvedValueOnce('match').mockResolvedValueOnce('missing')
    const adapter = createCodexDesktopAdapter({ runtime: host, inspectHandler, buildReceiverPrompt: () => 'draft',
      qualification: { evidence: 'fixture only', targetVersion } })
    expect(await adapter.checkIntegration(target, signal)).toEqual({ state: 'needs_user_action', action: 'configure_target' })
    expect(await adapter.checkIntegration(target, signal)).toEqual({ state: 'unknown' })
    expect(host.checkIntegration).not.toHaveBeenCalled()
    expect(await adapter.checkIntegration(target, signal)).toEqual({ state: 'unknown' })
    expect(host.checkIntegration).toHaveBeenCalledTimes(1)
    expect(await adapter.canReadPackage(target, bundle, signal)).toBe(false)
    expect(host.canReadPackage).not.toHaveBeenCalled()
  })

  it('rejects a different target configuration or handoff binding', async () => {
    const adapter = createCodexDesktopAdapter({ runtime: runtime(), inspectHandler: matchHandler,
      buildReceiverPrompt: () => 'draft', qualification: { evidence: 'fixture only', targetVersion } })
    await expect(adapter.checkIntegration({ ...target, configurationKey: 'other' }, signal)).rejects.toThrow()
    await expect(adapter.canReadPackage({ ...target, version: 'other' }, bundle, signal)).rejects.toThrow()
    expect(() => adapter.launchIntent(target, { ...bundle, binding: { ...bundle.binding, targetId: 'other' } })).toThrow()
    expect(() => adapter.launchIntent(target, { ...bundle, binding: { ...bundle.binding, targetVersion: 'other' } })).toThrow()
  })

  it.skipIf(process.platform !== 'darwin')('probes the embedded CLI but never treats its result as Desktop readiness', async () => {
    const appPath = await fixture()
    const cli = join(appPath, 'Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex')
    const adapter = createCodexDesktopAdapter({ appPath, inspectHandler: matchHandler, buildReceiverPrompt: () => 'draft',
      qualification: { evidence: 'fixture only', targetVersion } })
    const detected = await adapter.detect(signal)
    expect(detected.state).toBe('present')
    if (detected.state !== 'present') throw new Error('fixture was not detected')
    expect(await adapter.checkIntegration(detected, signal)).toEqual({ state: 'needs_user_action', action: 'configure_target' })
    expect(await adapter.canReadPackage(detected, bundle, signal)).toBe(false)
    await expect(adapter.resolveEnvironment(detected, '/tmp/project', signal)).rejects.toThrow()
    expect(await adapter.installIntegration(detected, signal)).toEqual({ state: 'needs_user_action', action: 'configure_target' })
    await writeFile(`${cli}.invalid`, '')
    expect(await adapter.checkIntegration(detected, signal)).toEqual({ state: 'unknown' })
  }, 30_000)

  it.skipIf(process.platform !== 'darwin')('runs consented setup with fixed argv and does not claim Desktop readiness', async () => {
    const appPath = await fixture()
    const executable = join(temporary!, 'powercontext')
    const output = join(temporary!, 'setup-args')
    await writeFile(executable, '#!/bin/sh\nprintf "%s\\n" "$@" > "$(dirname "$0")/setup-args"\n', { mode: 0o755 })
    const source = join(temporary!, 'source; literal')
    const adapter = createCodexDesktopAdapter({ appPath, buildReceiverPrompt: () => 'draft',
      setup: { executable, source, serverUrl: 'http://localhost:8765' },
      qualification: { evidence: 'fixture only', targetVersion } })
    expect(await adapter.installIntegration(fallbackTarget, signal)).toEqual({ state: 'needs_user_action', action: 'configure_target' })
    expect((await readFile(output, 'utf8')).trim().split('\n')).toEqual([
      'setup', 'codex', '--source', source, '--server-url', 'http://localhost:8765', '--json',
    ])
    expect(() => createCodexDesktopAdapter({ appPath, buildReceiverPrompt: () => 'draft',
      setup: { executable, source, serverUrl: 'https://user:secret@example.com' },
      qualification: { evidence: 'fixture only', targetVersion } })).toThrow()
  }, 30_000)

  it.skipIf(process.platform !== 'darwin')('settles an interrupted setup as unknown', async () => {
    const appPath = await fixture()
    const executable = join(temporary!, 'powercontext')
    await writeFile(executable, '#!/bin/sh\nsleep 5\n', { mode: 0o755 })
    const adapter = createCodexDesktopAdapter({ appPath, buildReceiverPrompt: () => 'draft',
      setup: { executable, source: temporary!, serverUrl: 'http://localhost:8765' },
      qualification: { evidence: 'fixture only', targetVersion } })
    const controller = new AbortController()
    const pending = adapter.installIntegration(fallbackTarget, controller.signal)
    setTimeout(() => controller.abort(), 100)
    expect(await pending).toEqual({ state: 'unknown' })
  })

  it('passes the exact bundle to the injected prompt builder and encodes an unsent draft', () => {
    const buildReceiverPrompt = vi.fn(() => 'Read the handoff: /private/handoff + 中.json')
    const adapter = createCodexDesktopAdapter({ runtime: runtime(), buildReceiverPrompt,
      qualification: { evidence: 'fixture only', targetVersion } })
    const intent = adapter.launchIntent(target, bundle, 'zh-CN')
    expect(buildReceiverPrompt).toHaveBeenCalledWith(bundle, 'zh-CN')
    const uri = new URL(nativeLaunchUrl(adapter.id, bundle.binding.effectiveCwd, intent))
    expect(uri.protocol).toBe('codex:')
    expect(uri.searchParams.get('path')).toBe(bundle.binding.effectiveCwd)
    expect(uri.searchParams.get('prompt')).toBe(intent.draft)
    const empty = createCodexDesktopAdapter({ runtime: runtime(), buildReceiverPrompt: () => ' ',
      qualification: { evidence: 'fixture only', targetVersion } })
    expect(() => empty.launchIntent(target, bundle)).toThrow()
  })
})
