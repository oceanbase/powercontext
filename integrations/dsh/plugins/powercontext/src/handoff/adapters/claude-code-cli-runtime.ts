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
import { createHash, createHmac } from 'node:crypto'
import { constants } from 'node:fs'
import { homedir } from 'node:os'
import { access, lstat, readFile, realpath } from 'node:fs/promises'
import { isAbsolute, join, relative } from 'node:path'
import { promisify } from 'node:util'
import type { ClaudeCliReceiver } from './claude-code-cli.ts'
import type { TargetEnvironment } from '../targets.ts'

const execute = promisify(execFile)
const PROBE = String.raw`
import json, os, sys, time
from urllib.request import Request, build_opener, HTTPRedirectHandler
sys.path.insert(0, sys.argv[1])
from claude_code_settings import ClaudeCodePluginSettings
from scripts.workspace_scope import resolve_scope_id
class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl): return None
settings = ClaudeCodePluginSettings.from_environment()
endpoint = sys.argv[3].rstrip('/')
if endpoint != settings.server_url.rstrip('/'):
    raise ValueError('MCP and hook endpoints differ')
def identity(authorization):
    headers = {'Accept': 'application/json'}
    if authorization: headers['Authorization'] = authorization
    req = Request(endpoint + '/v1/connection/identity', headers=headers)
    with build_opener(NoRedirect()).open(req, timeout=3) as response:
        return json.loads(response.read(1048577))
a = identity(settings.authorization)
b = identity(os.environ.get('POWERCONTEXT_CLAUDE_AUTHORIZATION'))
fields = ('server_id', 'authentication', 'access_mode', 'principal')
if any(a.get(k) != b.get(k) for k in fields): raise ValueError('MCP and hook identities differ')
if a.get('authentication') not in ('disabled', 'authenticated'): raise ValueError('unknown authentication')
if a.get('access_mode') not in ('disabled', 'enforced'): raise ValueError('unknown access mode')
if a.get('access_mode') == 'enforced' and a.get('authentication') != 'authenticated': raise ValueError('unauthorized')
if a.get('authentication') == 'disabled' and a.get('principal') is not None: raise ValueError('invalid anonymous identity')
if a.get('authentication') == 'authenticated':
    principal = a.get('principal')
    if not isinstance(principal, dict) or principal.get('type') not in ('user', 'service') or not principal.get('id'): raise ValueError('invalid principal')
if not isinstance(a.get('server_id'), str) or not a['server_id']: raise ValueError('missing server identity')
# Verify the actual MCP authentication and the operations the receiver needs.
headers = {'Accept': 'application/json, text/event-stream', 'Content-Type': 'application/json'}
if os.environ.get('POWERCONTEXT_CLAUDE_AUTHORIZATION'):
    headers['Authorization'] = os.environ['POWERCONTEXT_CLAUDE_AUTHORIZATION']
def rpc(message):
    req = Request(endpoint + '/mcp/', data=json.dumps(message).encode(), headers=headers, method='POST')
    with build_opener(NoRedirect()).open(req, timeout=3) as response:
        session = response.headers.get('mcp-session-id')
        if session: headers['mcp-session-id'] = session
        data = response.read(1048577)
        if len(data) > 1048576: raise ValueError('MCP response too large')
        if not data: return None
        if 'text/event-stream' in response.headers.get('Content-Type', ''):
            events = [line[5:].strip() for line in data.decode().splitlines() if line.startswith('data:')]
            data = events[-1].encode() if events else b'{}'
        return json.loads(data)
try:
    initialized = rpc({'jsonrpc':'2.0', 'id':1, 'method':'initialize', 'params':{'protocolVersion':'2025-03-26', 'capabilities':{}, 'clientInfo':{'name':'powercontext-handoff-preflight','version':'1'}}})
    protocol = initialized['result']['protocolVersion']
    if not isinstance(protocol, str): raise ValueError('invalid MCP protocol')
    headers['MCP-Protocol-Version'] = protocol
    rpc({'jsonrpc':'2.0', 'method':'notifications/initialized'})
    names = set()
    cursor = None
    for page in range(8):
        result = rpc({'jsonrpc':'2.0', 'id':2+page, 'method':'tools/list', 'params':{'cursor':cursor} if cursor else {}})['result']
        names.update(item['name'] for item in result['tools'] if isinstance(item, dict) and isinstance(item.get('name'), str))
        cursor = result.get('nextCursor')
        if not cursor: break
    else: raise ValueError('MCP tool pages exceeded')
    if not {'get_connection_identity', 'continue_handoff', 'acknowledge_handoff'} <= names: raise ValueError('MCP handoff tools unavailable')
finally:
    if headers.get('mcp-session-id'):
        try:
            req = Request(endpoint + '/mcp/', headers=headers, method='DELETE')
            with build_opener(NoRedirect()).open(req, timeout=3): pass
        except Exception: pass
scope = resolve_scope_id(sys.argv[2], session_id=None, settings=settings, deadline=time.monotonic()+settings.http_budget_seconds)
print(json.dumps({'serverIdentity': a['server_id'], 'scopeId': scope}, separators=(',', ':')))
`

export interface ClaudeCodeCliRuntimeOptions {
  /** Actual environment observed for the qualified handler-launched terminal. */
  readonly launchEnvironment: NodeJS.ProcessEnv
  readonly configDir: string
  /** Identifies the real terminal and qualification evidence for this environment. */
  readonly environmentEvidence: { readonly terminal: string; readonly evidence: string }
  readonly pythonExecutable: string
  /** Persistent host-owned secret used only to fingerprint effective configuration. */
  readonly generationKey: Buffer
  /** Roots whose receiver Read access was actually tested for the qualified CLI/terminal. */
  readonly qualifiedPackageRoots: readonly string[]
}

function object(value: unknown): Record<string, any> | undefined {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, any> : undefined
}

async function readObject(path: string): Promise<Record<string, any>> {
  try {
    const info = await lstat(path)
    if (!info.isFile() || info.size > 1_048_576) throw new Error('Invalid settings file')
    const result = object(JSON.parse(await readFile(path, 'utf8')))
    if (!result) throw new Error('Invalid settings')
    return result
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === 'ENOENT') return {}
    throw error
  }
}

async function command(file: string, args: string[], env: NodeJS.ProcessEnv, signal: AbortSignal, cwd?: string) {
  signal.throwIfAborted()
  return (await execute(file, args, { env, cwd, signal, timeout: 15_000, maxBuffer: 1_048_576, encoding: 'utf8' })).stdout
}

/** Check Launch Services' selected app and its actual executable, without opening the URL. */
async function registeredHandler(executable: string, env: NodeJS.ProcessEnv, signal: AbortSignal): Promise<'ready' | 'needs_user_action' | 'unknown'> {
  if (process.platform !== 'darwin') return 'unknown'
  try {
    const app = (await command('/usr/bin/osascript', ['-l', 'JavaScript', '-e',
      "ObjC.import('AppKit'); let u=$.NSWorkspace.sharedWorkspace.URLForApplicationToOpenURL($.NSURL.URLWithString('claude-cli://open')); u.isNil() ? '' : ObjC.unwrap(u.path)"], env, signal)).trim()
    if (!app) return 'needs_user_action'
    const expected = join(env.HOME || '', 'Applications', 'Claude Code URL Handler.app')
    if (!isAbsolute(expected) || await realpath(app) !== await realpath(expected)) return 'needs_user_action'
    const name = (await command('/usr/libexec/PlistBuddy', ['-c', 'Print :CFBundleExecutable', join(app, 'Contents', 'Info.plist')], env, signal)).trim()
    if (!name || name.includes('/') || name === '.' || name === '..') return 'unknown'
    return await realpath(join(app, 'Contents', 'MacOS', name)) === await realpath(executable) ? 'ready' : 'needs_user_action'
  } catch (error) {
    if (signal.aborted) signal.throwIfAborted()
    return (error as NodeJS.ErrnoException).code === 'ENOENT' ? 'needs_user_action' : 'unknown'
  }
}

/**
 * The environment and readable roots are taken from real version-specific
 * qualification; do not populate them from the DSH process or a fixture.
 * Claude's plugin-list command may initialize its configuration and write files,
 * so callers must account for that effect before invoking this probe.
 */
export function createClaudeCodeCliReceiver(options: ClaudeCodeCliRuntimeOptions): ClaudeCliReceiver {
  if (!isAbsolute(options.configDir) || !isAbsolute(options.pythonExecutable)) throw new Error('Absolute paths required')
  if (options.generationKey.length < 32 || !options.environmentEvidence.terminal.trim()
    || !options.environmentEvidence.evidence.trim()) throw new Error('Verified terminal evidence and a persistent generation key are required')
  const key = Buffer.from(options.generationKey)
  const env = { ...options.launchEnvironment }
  const inspect = async (executable: string, cwd: string, signal: AbortSignal): Promise<TargetEnvironment | undefined> => {
    try {
      const info = await realpath(cwd)
      if (info !== cwd) return undefined
      if (await realpath(options.configDir) !== options.configDir) return undefined
      const selectedConfig = env.CLAUDE_CONFIG_DIR ?? join(env.HOME ?? homedir(), '.claude')
      if (!isAbsolute(selectedConfig) || await realpath(selectedConfig) !== options.configDir) return undefined
      const settings = await readObject(join(options.configDir, 'settings.json'))
      // Claude can merge project settings and managed policy, so never silently
      // predict the routing when they provide plugin or environment overrides.
      let gitRoot = cwd
      try {
        const observedRoot = (await command('git', ['rev-parse', '--show-toplevel'], env, signal, cwd)).trim()
        if (isAbsolute(observedRoot)) gitRoot = await realpath(observedRoot)
      } catch { if (signal.aborted) signal.throwIfAborted() }
      for (const path of [join(cwd, '.claude', 'settings.json'), join(cwd, '.claude', 'settings.local.json'),
        join(gitRoot, '.claude', 'settings.json'), join(gitRoot, '.claude', 'settings.local.json'),
        '/Library/Application Support/ClaudeCode/managed-settings.json']) {
        const value = await readObject(path)
        if (value.env || value.pluginConfigs || value.enabledPlugins || value.disableAllHooks || value.permissions
          || value.sandbox || value.mcpServers || value.deniedMcpServers || value.allowedMcpServers
          || value.enabledMcpjsonServers || value.disabledMcpjsonServers) return undefined
      }
      for (const path of [join(cwd, '.mcp.json'), join(gitRoot, '.mcp.json'),
        '/Library/Application Support/ClaudeCode/managed-mcp.json']) {
        const mcpConfig = await readObject(path)
        if (mcpConfig.mcpServers !== undefined && !object(mcpConfig.mcpServers)) return undefined
        if (mcpConfig.mcpServers?.powercontext || path.endsWith('/managed-mcp.json') && Object.keys(mcpConfig).length) return undefined
      }
      if (settings.env !== undefined && !object(settings.env)
        || settings.pluginConfigs !== undefined && !object(settings.pluginConfigs)
        || settings.enabledPlugins !== undefined && !object(settings.enabledPlugins)
        || settings.enabledPlugins?.['powercontext@powercontext'] === false
        || settings.mcpServers !== undefined && !object(settings.mcpServers)) return undefined
      if (settings.mcpServers?.powercontext || settings.deniedMcpServers || settings.allowedMcpServers
        || settings.enabledMcpjsonServers || settings.disabledMcpjsonServers) return undefined
      const settingsEnv = object(settings.env) ?? {}
      // A profile override inside settings is not a proven startup route.
      if (Object.hasOwn(settingsEnv, 'CLAUDE_CONFIG_DIR')) return undefined
      const targetEnv: NodeJS.ProcessEnv = { ...env }
      for (const [name, value] of Object.entries(settingsEnv)) {
        if (typeof value !== 'string') return undefined
        targetEnv[name] = value
      }
      if (settings.disableAllHooks === true) return undefined
      const list = JSON.parse(await command(executable, ['plugin', 'list', '--json'], targetEnv, signal, cwd))
      if (!Array.isArray(list)) return undefined
      const plugins = list.filter(value => value?.id === 'powercontext@powercontext' && value?.enabled === true && !value?.errors?.length)
      if (plugins.length !== 1 || !isAbsolute(plugins[0].installPath ?? '')) return undefined
      const pluginRoot = await realpath(plugins[0].installPath)
      const manifest = await readObject(join(pluginRoot, '.claude-plugin', 'plugin.json'))
      const mcp = await readObject(join(pluginRoot, '.mcp.json'))
      const hooks = await readObject(join(pluginRoot, 'hooks/hooks.json'))
      const promptHooks = hooks.hooks?.UserPromptSubmit
      if (!Array.isArray(promptHooks) || !promptHooks.some(entry => !entry?.matcher && Array.isArray(entry?.hooks)
        && entry.hooks.some((hook: any) => hook?.type === 'command' && hook.command === 'python3'
          && Array.isArray(hook.args) && hook.args.length === 1
          && hook.args[0] === '${CLAUDE_PLUGIN_ROOT}/hooks/user_prompt_submit.py'))) return undefined
      const receive = join(pluginRoot, 'hooks/dsh_handoff.py')
      const receiveInfo = await lstat(receive)
      if (!receiveInfo.isFile() || receiveInfo.size < 1 || receiveInfo.size > 64 * 1024) return undefined
      const receiverHash = createHash('sha256').update(await readFile(receive, { signal })).digest('hex')
      if (mcp.powercontext?.url !== '${user_config.server_url}/mcp'
        || mcp.powercontext?.headers?.Authorization !== '${POWERCONTEXT_CLAUDE_AUTHORIZATION:-}') return undefined
      const pluginConfig = settings.pluginConfigs?.['powercontext@powercontext']
      if (pluginConfig !== undefined && !object(pluginConfig)
        || pluginConfig?.options !== undefined && !object(pluginConfig.options)) return undefined
      const opts = pluginConfig?.options ?? {}
      const endpoint = opts.server_url ?? manifest.userConfig?.server_url?.default
      if (typeof endpoint !== 'string' || !endpoint.trim()) return undefined
      targetEnv.CLAUDE_PLUGIN_OPTION_SERVER_URL = endpoint
      if (typeof opts.allow_insecure_http === 'boolean') targetEnv.CLAUDE_PLUGIN_OPTION_ALLOW_INSECURE_HTTP = String(opts.allow_insecure_http)
      const parsed = object(JSON.parse(await command(options.pythonExecutable, ['-c', PROBE, pluginRoot, cwd, endpoint], targetEnv, signal, cwd)))
      if (!parsed || typeof parsed.serverIdentity !== 'string' || !parsed.serverIdentity
        || typeof parsed.scopeId !== 'string' || !parsed.scopeId) return undefined
      const roots = await Promise.all(options.qualifiedPackageRoots.map(root => realpath(root)))
      const credentialFile = join(options.configDir, 'powercontext', 'credentials.json')
      const clientsFile = targetEnv.POWERCONTEXT_CLIENT_CONFIG_FILE ?? join(targetEnv.HOME ?? '', '.config', 'powercontext', 'clients.json')
      const credentials = await readObject(credentialFile)
      const clients = await readObject(clientsFile)
      const generation = createHmac('sha256', key).update(JSON.stringify([await realpath(executable), pluginRoot,
        manifest, mcp, settings, Object.fromEntries(Object.entries(targetEnv).sort(([a], [b]) => a < b ? -1 : a > b ? 1 : 0)),
        credentials, clients, roots, parsed, promptHooks, receiverHash, options.environmentEvidence])).digest('hex')
      return { effectiveCwd: cwd, serverIdentity: parsed.serverIdentity, scopeId: parsed.scopeId,
        configurationGeneration: generation, newSession: true, unsentPrefill: true, packageAccess: roots.length > 0 }
    } catch {
      if (signal.aborted) signal.throwIfAborted()
      return undefined
    }
  }
  return {
    handler: (executable, _version, signal) => registeredHandler(executable, env, signal),
    environment: inspect,
    async canReadPackage(_executable, bundle, signal) {
      try {
        signal.throwIfAborted()
        const path = await realpath(bundle.packagePath)
        const roots = await Promise.all(options.qualifiedPackageRoots.map(root => realpath(root)))
        if (!roots.some(root => { const rel = relative(root, path); return rel !== '' && !rel.startsWith('..') && !isAbsolute(rel) })) return false
        const info = await lstat(bundle.packagePath)
        if (!info.isFile()) return false
        await access(path, constants.R_OK)
        const content = await readFile(path, { signal })
        return createHash('sha256').update(content).digest('hex') === bundle.packageSha256
      } catch {
        if (signal.aborted) signal.throwIfAborted()
        return false
      }
    },
  }
}
