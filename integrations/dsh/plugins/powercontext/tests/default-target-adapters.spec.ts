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
import { mkdir, mkdtemp, rm, symlink, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { promisify } from 'node:util'
import { afterAll, afterEach, beforeAll, expect, it } from 'vitest'
import { createDefaultTargetAdapters } from '../src/handoff/adapters/defaults.ts'
import type { LaunchBundle } from '../src/handoff/contracts.ts'
import { TargetCatalog } from '../src/handoff/targets.ts'

let temporary = ''
let executableRoot = ''
let executable = ''
const signal = new AbortController().signal
const version = '26.1106.51428 (26605); codex-cli 0.167.0-alpha.4.1'
const bundle = { binding: { targetId: 'codex-desktop', targetVersion: version } } as LaunchBundle

beforeAll(async () => {
  if (process.platform !== 'darwin') return
  executableRoot = await mkdtemp(join(tmpdir(), 'pc-codex-registry-cli-'))
  executable = join(executableRoot, 'codex')
  await writeFile(executable, '#!/bin/sh\nprintf "codex-cli 0.167.0-alpha.4.1\\n"\n', { mode: 0o755 })
  await promisify(execFile)(executable, ['--version'], { timeout: 20_000 })
}, 30_000)

afterAll(async () => { if (executableRoot) await rm(executableRoot, { recursive: true, force: true }) })

async function fixture() {
  temporary = await mkdtemp(join(tmpdir(), 'pc-codex-registry-'))
  const app = join(temporary, 'ChatGPT.app')
  const bin = join(app, 'Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS')
  await mkdir(bin, { recursive: true })
  await writeFile(join(app, 'Contents/Info.plist'), `<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict><key>CFBundleIdentifier</key><string>com.openai.codex</string>
<key>CFBundleShortVersionString</key><string>26.1106.51428</string><key>CFBundleVersion</key><string>26605</string>
<key>CFBundleURLTypes</key><array><dict><key>CFBundleURLSchemes</key><array><string>codex</string></array></dict></array>
</dict></plist>`)
  await symlink(executable, join(bin, 'codex'))
  return { root: temporary, codexHome: temporary, codexAppPath: app, generationKey: Buffer.alloc(32, 7) }
}

afterEach(async () => { if (temporary) await rm(temporary, { recursive: true, force: true }); temporary = '' })

it.skipIf(process.platform !== 'darwin')('offers Codex and CLI preflight by default, with Desktop Code still detection-only', async () => {
  const options = await fixture()
  const adapters = createDefaultTargetAdapters(options)
  expect(adapters.map(adapter => adapter.id)).toEqual(['codex-desktop', 'claude-desktop', 'claude-code-cli'])
  const catalog = new TargetCatalog(adapters, 'darwin')
  expect(catalog.list().filter(row => row.availability === 'requires_preflight').map(row => row.targetId))
    .toEqual(['codex-desktop', 'claude-code-cli'])
  expect(catalog.list().find(row => row.targetId === 'claude-desktop')).toMatchObject({
    qualification: null, diagnostics: 'available', availability: 'unavailable', action: 'manual_setup_or_export',
  })
  expect(await catalog.diagnose('codex-desktop', signal)).toEqual({ state: 'present', version })
  expect(() => catalog.require('claude-desktop')).toThrow()
  await rm(options.codexAppPath, { recursive: true })
  expect(await catalog.diagnose('codex-desktop', signal)).toEqual({ state: 'missing' })
}, 30_000)

it.skipIf(process.platform !== 'darwin')('uses the shared unsent draft for the supported Codex build only', async () => {
  const options = await fixture()
  const [adapter] = createDefaultTargetAdapters(options)
  const catalog = new TargetCatalog([adapter], 'darwin')
  const result = await adapter.detect(signal)
  expect(result.state).toBe('present')
  if (result.state !== 'present') throw new Error('fixture not detected')
  const packagePath = join(options.root, 'handoff.json')
  expect(catalog.require('codex-desktop').launchIntent(result, { ...bundle, packagePath }, 'zh-CN').draft)
    .toEqual(`请用 PowerContext 读取这份交接包，继续剩下的工作，并用简体中文回复：\n${JSON.stringify(packagePath)}`)
  expect(() => adapter.launchIntent({ ...result, version: 'other version' }, bundle)).toThrow()
  const otherPlatform = new TargetCatalog([adapter], 'linux')
  expect(() => otherPlatform.require('codex-desktop')).toThrow()
  expect(otherPlatform.list().find(item => item.targetId === 'codex-desktop')).toMatchObject({
    qualification: null, diagnostics: 'available', action: 'manual_setup_or_export',
  })
  expect(() => new TargetCatalog([adapter, adapter], 'linux')).toThrow()
}, 30_000)

it('requires a profile root and persistent generation key', () => {
  expect(() => createDefaultTargetAdapters({ root: 'relative', generationKey: Buffer.alloc(32) })).toThrow()
  expect(() => createDefaultTargetAdapters({ root: '/tmp', generationKey: Buffer.alloc(31) })).toThrow()
})
