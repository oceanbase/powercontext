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
import { mkdtemp, mkdir, realpath, rm, writeFile } from 'node:fs/promises'
import { createServer } from 'node:http'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { expect, it } from 'vitest'
import type { LaunchBundle } from '../src/handoff/contracts.ts'
import { createClaudeCodeCliReceiver } from '../src/handoff/adapters/claude-code-cli-runtime.ts'

it('resolves the installed plugin against the Server and rejects routing and access changes', async () => {
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
    await new Promise<void>(resolveServer => server.listen(0, '127.0.0.1', resolveServer))
    const address = server.address()
    if (!address || typeof address === 'string') throw new Error('missing server address')
    const endpoint = `http://127.0.0.1:${address.port}`
    await writeFile(join(config, 'settings.json'), JSON.stringify({ pluginConfigs: { 'powercontext@powercontext': { options: { server_url: endpoint } } } }))
    const output = JSON.stringify([{ id: 'powercontext@powercontext', enabled: true, installPath: plugin }])
    await writeFile(executable, `#!/bin/sh\nprintf '%s' '${output}'\n`, { mode: 0o755 })
    const receiver = createClaudeCodeCliReceiver({ configDir: config, pythonExecutable: '/usr/bin/python3',
      generationKey: Buffer.alloc(32, 1), environmentEvidence: { terminal: 'fixture-terminal', evidence: 'fixture' }, launchEnvironment: { HOME: root, PATH: '/usr/bin:/bin', CLAUDE_CONFIG_DIR: config }, qualifiedPackageRoots: [packages] })
    const signal = new AbortController().signal
    const observed = await receiver.environment(executable, workspace, signal)
    expect(observed).toMatchObject({ effectiveCwd: workspace, serverIdentity: 'server-one', scopeId: 'scope-one',
      newSession: true, unsentPrefill: true, packageAccess: true })
    expect(observed?.configurationGeneration).toMatch(/^[0-9a-f]{64}$/)
    const recreated = createClaudeCodeCliReceiver({ configDir: config, pythonExecutable: '/usr/bin/python3',
      generationKey: Buffer.alloc(32, 1), environmentEvidence: { terminal: 'fixture-terminal', evidence: 'fixture' }, launchEnvironment: { CLAUDE_CONFIG_DIR: config, PATH: '/usr/bin:/bin', HOME: root }, qualifiedPackageRoots: [packages] })
    expect((await recreated.environment(executable, workspace, signal))?.configurationGeneration).toBe(observed?.configurationGeneration)
    await writeFile(join(config, 'settings.json'), JSON.stringify({ env: 'invalid' }))
    expect(await receiver.environment(executable, workspace, signal)).toBeUndefined()
    await writeFile(join(config, 'settings.json'), JSON.stringify({ pluginConfigs: { 'powercontext@powercontext': { options: { server_url: endpoint } } } }))
    const defaultProfile = join(root, '.claude')
    await mkdir(defaultProfile)
    await writeFile(join(defaultProfile, 'settings.json'), JSON.stringify({ pluginConfigs: { 'powercontext@powercontext': { options: { server_url: endpoint } } } }))
    const defaultReceiver = createClaudeCodeCliReceiver({ configDir: defaultProfile, pythonExecutable: '/usr/bin/python3',
      generationKey: Buffer.alloc(32, 1), environmentEvidence: { terminal: 'fixture-terminal', evidence: 'fixture' },
      launchEnvironment: { HOME: root, PATH: '/usr/bin:/bin' }, qualifiedPackageRoots: [packages] })
    expect((await defaultReceiver.environment(executable, workspace, signal))?.scopeId).toBe('scope-one')
    const wrongReceiver = createClaudeCodeCliReceiver({ configDir: config, pythonExecutable: '/usr/bin/python3',
      generationKey: Buffer.alloc(32, 1), environmentEvidence: { terminal: 'fixture-terminal', evidence: 'fixture' },
      launchEnvironment: { HOME: root, PATH: '/usr/bin:/bin' }, qualifiedPackageRoots: [packages] })
    expect(await wrongReceiver.environment(executable, workspace, signal)).toBeUndefined()
    handoffToolsAvailable = false
    expect(await receiver.environment(executable, workspace, signal)).toBeUndefined()
    handoffToolsAvailable = true
    const packagePath = join(packages, 'handoff.json')
    await writeFile(packagePath, 'handoff')
    const bundle = { packagePath, packageSha256: createHash('sha256').update('handoff').digest('hex') } as LaunchBundle
    expect(await receiver.canReadPackage(executable, bundle, signal)).toBe(true)
    const defaultTerminal = createClaudeCodeCliReceiver({ configDir: defaultProfile, pythonExecutable: '/usr/bin/python3',
      generationKey: Buffer.alloc(32, 1), environmentEvidence: { terminal: 'macOS', evidence: 'fixture' },
      launchEnvironment: { HOME: root, PATH: '/usr/bin:/bin' },
      qualifiedPackageRoots: [packages], observeDefaultTerminal: true })
    await writeFile(join(root, '.claude.json'), JSON.stringify({ deepLinkTerminal: 'unverified' }))
    expect(await defaultTerminal.environment(executable, workspace, signal)).toBeUndefined()
    expect(await defaultTerminal.canReadPackage(executable, bundle, signal)).toBe(false)
    await writeFile(packagePath, 'changed')
    expect(await receiver.canReadPackage(executable, bundle, signal)).toBe(false)
    await writeFile(join(workspace, '.mcp.json'), JSON.stringify({ mcpServers: { powercontext: { url: 'http://wrong' } } }))
    expect(await receiver.environment(executable, workspace, signal)).toBeUndefined()
    await rm(join(workspace, '.mcp.json'))
    await mkdir(join(workspace, '.claude'))
    await writeFile(join(workspace, '.claude', 'settings.json'), JSON.stringify({ env: { POWERCONTEXT_CLAUDE_SCOPE_ID: 'different' } }))
    expect(await receiver.environment(executable, workspace, signal)).toBeUndefined()
  } finally {
    await new Promise<void>(resolveServer => server.close(() => resolveServer()))
    await rm(root, { recursive: true, force: true })
  }
}, 30_000)
