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

import { execFileSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import { mkdtemp, mkdir, realpath, rm, writeFile } from 'node:fs/promises'
import { createServer } from 'node:http'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { expect, it } from 'vitest'
import type { LaunchBundle } from '../src/handoff/contracts.ts'
import { createClaudeCodeCliReceiver } from '../src/handoff/adapters/claude-code-cli-runtime.ts'

interface ReceiverFixture {
  root: string
  config: string
  workspace: string
  executable: string
  endpoint: string
  receiver: ReturnType<typeof createClaudeCodeCliReceiver>
  signal: AbortSignal
  launch: LaunchBundle
  setHandoffToolsAvailable: (available: boolean) => void
}

async function withReceiver(use: (fixture: ReceiverFixture) => Promise<void>) {
  const root = await realpath(await mkdtemp(join(tmpdir(), 'pc-claude-live-probe-')))
  const config = join(root, 'profile')
  const workspace = join(root, 'workspace')
  const packages = join(root, 'packages')
  const plugin = resolve('../../../claude-code/plugins/powercontext')
  const executable = join(root, 'claude')
  let handoffToolsAvailable = true
  const server = createServer((req, res) => {
    res.setHeader('content-type', 'application/json')
    if (req.url === '/v1/connection/identity') res.end(JSON.stringify({ server_id: 'server-one', authentication: 'disabled', access_mode: 'disabled', principal: null }))
    else if (req.url === '/v1/scope-bindings/resolve') res.end(JSON.stringify({ scope_id: 'scope-one' }))
    else if (req.url === '/mcp/') {
      let body = ''
      req.on('data', chunk => { body += chunk })
      req.on('end', () => {
        const message = JSON.parse(body || '{}')
        if (message.method === 'initialize') res.end(JSON.stringify({ jsonrpc: '2.0', id: 1, result: { protocolVersion: '2025-03-26' } }))
        else if (message.method === 'tools/list') res.end(JSON.stringify({ jsonrpc: '2.0', id: message.id, result: { tools: (handoffToolsAvailable ? ['get_connection_identity', 'continue_handoff', 'acknowledge_handoff'] : ['continue_handoff', 'acknowledge_handoff']).map(name => ({ name })) } }))
        else { res.statusCode = 202; res.end() }
      })
    }
    else { res.statusCode = 404; res.end('{}') }
  })
  try {
    await Promise.all([mkdir(config), mkdir(workspace), mkdir(packages)])
    const preferences = JSON.stringify({ deepLinkTerminal: 'Terminal' })
    await writeFile(join(config, '.claude.json'), preferences)
    await writeFile(join(root, '.claude.json'), preferences)
    await new Promise<void>(resolveServer => server.listen(0, '127.0.0.1', resolveServer))
    const address = server.address()
    if (!address || typeof address === 'string') throw new Error('missing server address')
    const endpoint = `http://127.0.0.1:${address.port}`
    await writeFile(join(config, 'settings.json'), JSON.stringify({ pluginConfigs: { 'powercontext@powercontext': { options: { server_url: endpoint } } } }))
    const output = JSON.stringify([{ id: 'powercontext@powercontext', enabled: true, installPath: plugin }])
    await writeFile(executable, `#!/bin/sh\nprintf '%s' '${output}'\n`, { mode: 0o755 })
    const receiver = createClaudeCodeCliReceiver({ configDir: config, pythonExecutable: '/usr/bin/python3',
      generationKey: Buffer.alloc(32, 1), environmentEvidence: { terminal: 'Terminal', evidence: 'fixture' }, launchEnvironment: { HOME: root, PATH: '/usr/bin:/bin', CLAUDE_CONFIG_DIR: config }, qualifiedPackageRoots: [packages] })
    const signal = new AbortController().signal
    const packagePath = join(packages, 'handoff.json')
    await writeFile(packagePath, 'handoff')
    const packageSha256 = createHash('sha256').update('handoff').digest('hex')
    const launch = { packagePath, packageSha256, binding: { effectiveCwd: workspace } } as LaunchBundle
    await use({ root, config, workspace, executable, endpoint, receiver, signal, launch,
      setHandoffToolsAvailable: available => { handoffToolsAvailable = available } })
  } finally {
    await new Promise<void>(resolveServer => server.close(() => resolveServer()))
    await rm(root, { recursive: true, force: true })
  }
}

const test = it.extend<{ fixture: ReceiverFixture }>({
  fixture: async ({}, use) => withReceiver(use),
})

function gitWorktree(main: string, linked: string) {
  const env = { PATH: '/usr/bin:/bin', GIT_CONFIG_GLOBAL: '/dev/null', GIT_CONFIG_NOSYSTEM: '1' }
  const git = (...args: string[]) => execFileSync('git', args, { cwd: main, env, stdio: 'pipe' })
  git('init', '-q', '--template=')
  git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'seed', '--allow-empty')
  git('worktree', 'add', '-q', '--detach', linked)
}

test('resolves the installed plugin against the Server and rejects routing and access changes', async ({ fixture }) => {
  const { root, config, workspace, executable, endpoint, receiver, signal, launch, setHandoffToolsAvailable } = fixture
  const packages = join(root, 'packages')
  const overridePreferences = join(config, '.claude.json')
  const terminal = (deepLinkTerminal: string) => JSON.stringify({ deepLinkTerminal })
  const observed = await receiver.environment(executable, workspace, signal)
  expect(observed).toMatchObject({ effectiveCwd: workspace, serverIdentity: 'server-one', scopeId: 'scope-one',
    newSession: true, unsentPrefill: true, packageAccess: true })
  expect(observed?.configurationGeneration).toMatch(/^[0-9a-f]{64}$/)
  const recreated = createClaudeCodeCliReceiver({ configDir: config, pythonExecutable: '/usr/bin/python3',
    generationKey: Buffer.alloc(32, 1), environmentEvidence: { terminal: 'Terminal', evidence: 'fixture' }, launchEnvironment: { CLAUDE_CONFIG_DIR: config, PATH: '/usr/bin:/bin', HOME: root }, qualifiedPackageRoots: [packages] })
  expect((await recreated.environment(executable, workspace, signal))?.configurationGeneration).toBe(observed?.configurationGeneration)
  await writeFile(join(config, 'settings.json'), JSON.stringify({ env: 'invalid' }))
  expect(await receiver.environment(executable, workspace, signal)).toBeUndefined()
  await writeFile(join(config, 'settings.json'), JSON.stringify({ pluginConfigs: { 'powercontext@powercontext': { options: { server_url: endpoint } } } }))
  const defaultProfile = join(root, '.claude')
  await mkdir(defaultProfile)
  await writeFile(join(defaultProfile, 'settings.json'), JSON.stringify({ pluginConfigs: { 'powercontext@powercontext': { options: { server_url: endpoint } } } }))
  const defaultReceiver = createClaudeCodeCliReceiver({ configDir: defaultProfile, pythonExecutable: '/usr/bin/python3',
    generationKey: Buffer.alloc(32, 1), environmentEvidence: { terminal: 'Terminal', evidence: 'fixture' },
    launchEnvironment: { HOME: root, PATH: '/usr/bin:/bin' }, qualifiedPackageRoots: [packages] })
  expect((await defaultReceiver.environment(executable, workspace, signal))?.scopeId).toBe('scope-one')
  await writeFile(join(root, '.claude.json'), JSON.stringify({ deepLinkTerminal: 'Terminal',
    projects: { [workspace]: { disabledMcpServers: ['plugin:powercontext:powercontext'] } } }))
  expect(await defaultReceiver.environment(executable, workspace, signal)).toBeUndefined()
  expect((await receiver.environment(executable, workspace, signal))?.scopeId).toBe('scope-one')
  const wrongReceiver = createClaudeCodeCliReceiver({ configDir: config, pythonExecutable: '/usr/bin/python3',
    generationKey: Buffer.alloc(32, 1), environmentEvidence: { terminal: 'Terminal', evidence: 'fixture' },
    launchEnvironment: { HOME: root, PATH: '/usr/bin:/bin' }, qualifiedPackageRoots: [packages] })
  expect(await wrongReceiver.environment(executable, workspace, signal)).toBeUndefined()
  setHandoffToolsAvailable(false)
  expect(await receiver.environment(executable, workspace, signal)).toBeUndefined()
  setHandoffToolsAvailable(true)
  expect(await receiver.canReadPackage(executable, launch, signal)).toBe(true)
  // The handler executable and file are unchanged; the receiver's terminal and policies still matter.
  await writeFile(overridePreferences, terminal('Ghostty'))
  expect(await receiver.environment(executable, workspace, signal)).toBeUndefined()
  expect(await receiver.canReadPackage(executable, launch, signal)).toBe(false)
  await writeFile(overridePreferences, terminal('Terminal'))
  await writeFile(join(config, 'settings.json'), JSON.stringify({
    permissions: { deny: ['Read(~/.powercontext-dsh-handoff/**)'] },
  }))
  expect(await receiver.environment(executable, workspace, signal)).toBeUndefined()
  expect(await receiver.canReadPackage(executable, launch, signal)).toBe(false)
  await writeFile(join(config, 'settings.json'), JSON.stringify({ pluginConfigs: { 'powercontext@powercontext': { options: { server_url: endpoint } } } }))
  await writeFile(launch.packagePath, 'changed')
  expect(await receiver.canReadPackage(executable, launch, signal)).toBe(false)
  await writeFile(join(workspace, '.mcp.json'), JSON.stringify({ mcpServers: { powercontext: { url: 'http://wrong' } } }))
  expect(await receiver.environment(executable, workspace, signal)).toBeUndefined()
  await rm(join(workspace, '.mcp.json'))
  await mkdir(join(workspace, '.claude'))
  await writeFile(join(workspace, '.claude', 'settings.json'), JSON.stringify({ env: { POWERCONTEXT_CLAUDE_SCOPE_ID: 'different' } }))
  expect(await receiver.environment(executable, workspace, signal)).toBeUndefined()
}, 30_000)

test('projects resolve MCP toggle state from the main checkout, and bind the effective list', async ({ fixture }) => {
  const { config, workspace, root, receiver, executable, signal, launch } = fixture
  const main = join(root, 'main-café'.normalize('NFD'))
  const projectKey = main.normalize('NFC')
  const linked = join(root, 'linked')
  await mkdir(main)
  gitWorktree(main, linked)
  const cwd = join(linked, 'subdirectory')
  await mkdir(cwd)
  const linkedLaunch = { ...launch, binding: { ...launch.binding, effectiveCwd: cwd } }
  const preferenceFile = join(config, '.claude.json')
  const serverName = 'plugin:powercontext:powercontext'
  const writeProjects = (projects: unknown) => writeFile(preferenceFile, JSON.stringify({ deepLinkTerminal: 'Terminal', projects }))

  const baseline = await receiver.environment(executable, cwd, signal)
  expect(baseline?.packageAccess).toBe(true)
  const nonGit = await receiver.environment(executable, workspace, signal)
  expect(nonGit?.packageAccess).toBe(true)
  expect(nonGit?.configurationGeneration).not.toBe(baseline?.configurationGeneration)
  await writeProjects({ [cwd]: { disabledMcpServers: [serverName] }, [linked]: { disabledMcpServers: [serverName] } })
  expect((await receiver.environment(executable, cwd, signal))?.configurationGeneration).toBe(baseline?.configurationGeneration)

  await writeProjects({ [projectKey]: { disabledMcpServers: [serverName], enabledMcpServers: [serverName] } })
  expect(await receiver.environment(executable, cwd, signal)).toBeUndefined()
  expect(await receiver.canReadPackage(executable, linkedLaunch, signal)).toBe(false)
  await writeProjects({ [projectKey]: { disabledMcpServers: serverName } })
  expect(await receiver.environment(executable, cwd, signal)).toBeUndefined()
  await writeProjects({ [projectKey]: { disabledMcpServers: ['other-server'] } })
  const effective = await receiver.environment(executable, cwd, signal)
  expect(effective?.packageAccess).toBe(true)
  expect(effective?.configurationGeneration).not.toBe(baseline?.configurationGeneration)
  await writeProjects({})
  expect((await receiver.environment(executable, cwd, signal))?.configurationGeneration).toBe(baseline?.configurationGeneration)
}, 30_000)

test('rechecks main checkout local settings for a linked worktree, including package Read policy', async ({ fixture }) => {
  const { workspace: main, root, receiver, executable, signal, launch } = fixture
  const linked = join(root, 'linked')
  gitWorktree(main, linked)
  const cwd = join(linked, 'subdirectory')
  await mkdir(cwd)
  const linkedLaunch = { ...launch, binding: { ...launch.binding, effectiveCwd: cwd } }
  const claudeDir = join(main, '.claude')
  await mkdir(claudeDir)
  const settings = join(claudeDir, 'settings.local.json')

  // The main checkout's shared settings are not inherited by the CLI.
  await writeFile(join(claudeDir, 'settings.json'), JSON.stringify({ permissions: { deny: ['Read'] } }))
  const baseline = await receiver.environment(executable, cwd, signal)
  expect(baseline?.packageAccess).toBe(true)
  expect(await receiver.canReadPackage(executable, linkedLaunch, signal)).toBe(true)
  await writeFile(settings, JSON.stringify({ permissions: { deny: ['Read'] } }))
  expect(await receiver.environment(executable, cwd, signal)).toBeUndefined()
  expect(await receiver.canReadPackage(executable, linkedLaunch, signal)).toBe(false)
  await writeFile(settings, JSON.stringify({ disableAllHooks: true }))
  expect(await receiver.environment(executable, cwd, signal)).toBeUndefined()
  await rm(settings)
  expect((await receiver.environment(executable, cwd, signal))?.configurationGeneration).toBe(baseline?.configurationGeneration)
  await writeFile(join(linked, '.git'), 'gitdir: ./missing-worktree-metadata\n')
  expect(await receiver.environment(executable, cwd, signal)).toBeUndefined()
  expect(await receiver.canReadPackage(executable, linkedLaunch, signal)).toBe(false)
}, 30_000)
