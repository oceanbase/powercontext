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
import { access, lstat, readFile, readdir, realpath } from 'node:fs/promises'
import { isAbsolute, join, relative } from 'node:path'
import { promisify } from 'node:util'
import type { ClaudeCliReceiver } from './claude-code-cli.ts'
import type { TargetEnvironment } from '../targets.ts'

const execute = promisify(execFile)
const powercontextMcpServer = 'plugin:powercontext:powercontext'
const mcpHeadersHelper = 'python3 "${CLAUDE_PLUGIN_ROOT}/scripts/mcp_headers.py"'
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
# Claude merges the helper's saved credential over the static header.
mcp_authorization_header = os.environ.get('POWERCONTEXT_CLAUDE_AUTHORIZATION')
if sys.argv[4] == 'headers-helper':
    from claude_code_settings import mcp_authorization
    mcp_authorization_header = mcp_authorization(endpoint + '/mcp') or mcp_authorization_header
def identity(authorization):
    headers = {'Accept': 'application/json'}
    if authorization: headers['Authorization'] = authorization
    req = Request(endpoint + '/v1/connection/identity', headers=headers)
    with build_opener(NoRedirect()).open(req, timeout=3) as response:
        return json.loads(response.read(1048577))
a = identity(settings.authorization)
b = identity(mcp_authorization_header)
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
if mcp_authorization_header:
    headers['Authorization'] = mcp_authorization_header
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
  /** Exact macOS deep-link terminal preference and evidence for this environment. */
  readonly environmentEvidence: { readonly terminal: string; readonly evidence: string }
  readonly pythonExecutable: string
  /** Persistent fingerprint key and roots qualified for receiver Read access. */
  readonly packageStorage: () => { generationKey: Buffer; qualifiedPackageRoots: readonly string[] }
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

async function hashPluginFile(root: string, file: string, signal: AbortSignal) {
  const path = join(root, file)
  const info = await lstat(path)
  if (!info.isFile() || info.size < 1 || info.size > 64 * 1024) throw new Error('Invalid receiving plugin file')
  return createHash('sha256').update(await readFile(path, { signal })).digest('hex')
}

async function command(file: string, args: string[], env: NodeJS.ProcessEnv, signal: AbortSignal, cwd?: string) {
  signal.throwIfAborted()
  return (await execute(file, args, { env, cwd, signal, timeout: 15_000, maxBuffer: 1_048_576, encoding: 'utf8' })).stdout
}

export async function installedClaudePowerContext(executable: string, env: NodeJS.ProcessEnv, signal: AbortSignal, cwd?: string) {
  const list: unknown = JSON.parse(await command(executable, ['plugin', 'list', '--json'], env, signal, cwd))
  if (!Array.isArray(list)) throw new Error('Invalid Claude plugin inventory')
  const plugins = list.filter(value => value?.id === 'powercontext@powercontext')
  if (plugins.length > 1) throw new Error('Ambiguous Claude plugin inventory')
  return plugins[0] as { enabled?: boolean; errors?: unknown[]; installPath?: string } | undefined
}

const managed = '/Library/Application Support/ClaudeCode'
const terminalApps: Readonly<Record<string, string>> = {
  Terminal: '/System/Applications/Utilities/Terminal.app',
  Ghostty: '/Applications/Ghostty.app',
  iTerm: '/Applications/iTerm.app',
  kitty: '/Applications/kitty.app',
  Alacritty: '/Applications/Alacritty.app',
  WezTerm: '/Applications/WezTerm.app',
}

interface ClaudeProject {
  worktreeRoot: string
  projectRoot: string
}

/** Claude keys project state by the main checkout, even from a linked worktree or subdirectory. */
async function observeProject(cwd: string, env: NodeJS.ProcessEnv, signal: AbortSignal): Promise<ClaudeProject> {
  if (await realpath(cwd) !== cwd) throw new Error('Noncanonical working directory')
  // Claude discovers the project from the working directory, independently of Git environment overrides.
  const gitEnv = { PATH: env.PATH, HOME: env.HOME, LC_ALL: 'C' }
  let layout: string
  try {
    layout = await command('git', ['rev-parse', '--path-format=absolute', '--show-toplevel',
      '--git-dir', '--git-common-dir'], gitEnv, signal, cwd)
  } catch (error) {
    signal.throwIfAborted()
    // Git reports an ordinary non-repository differently if discovery stops at a mount.
    const { code, stderr } = error as { code?: number | string; stderr?: string }
    const [reason, detail, extra] = stderr?.trimEnd().split('\n') ?? []
    const noRepository = reason === 'fatal: not a git repository (or any of the parent directories): .git'
      && detail === undefined
    const stoppedAtMount = /^fatal: not a git repository \(or any parent up to mount point [^\r\n]+\)$/.test(reason ?? '')
      && detail === 'Stopping at filesystem boundary (GIT_DISCOVERY_ACROSS_FILESYSTEM not set).'
      && extra === undefined
    if (code !== 128 || (!noRepository && !stoppedAtMount)) throw error
    return { worktreeRoot: cwd, projectRoot: cwd }
  }
  const lines = layout.split('\n')
  const [topLevel, gitDir, commonDir, end] = lines
  if (lines.length !== 4 || end !== ''
    || !topLevel || !gitDir || !commonDir || ![topLevel, gitDir, commonDir].every(isAbsolute)) {
    throw new Error('Unknown Git worktree layout')
  }
  const worktreeRoot = await realpath(topLevel)
  let projectRoot = worktreeRoot
  if (await realpath(gitDir) !== await realpath(commonDir)) {
    const worktrees = await command('git', ['worktree', 'list', '--porcelain', '-z'], gitEnv, signal, cwd)
    const [main = ''] = worktrees.split('\0\0', 1)
    const [worktree, ...fields] = main.split('\0')
    if (!worktree?.startsWith('worktree ') || !isAbsolute(worktree.slice(9)) || fields.includes('bare')) {
      throw new Error('Unknown Git main checkout')
    }
    projectRoot = await realpath(worktree.slice(9))
  }
  return { worktreeRoot, projectRoot }
}

function observeProjectMcp(preferences: Record<string, any>, projectRoot: string) {
  const projectKey = projectRoot.normalize('NFC')
  const projects = object(preferences.projects)
  if (preferences.projects !== undefined && !projects) return undefined
  const entry = projects?.[projectKey]
  const project = object(entry)
  if (entry !== undefined && !project) return undefined
  const disabled = project?.disabledMcpServers
  if (disabled !== undefined && (!Array.isArray(disabled) || disabled.some(name => typeof name !== 'string'))) return undefined
  if (disabled?.includes(powercontextMcpServer)) return undefined
  return { projectKey, disabledMcpServers: [...new Set<string>(disabled)].sort() }
}

/** Reject policy overrides rather than trying to reproduce Claude's Read and sandbox rules. */
async function observeSettings(configDir: string, cwd: string, { worktreeRoot, projectRoot }: ClaudeProject) {
  const settings = await readObject(join(configDir, 'settings.json'))
  if (Object.hasOwn(settings, 'permissions') || Object.hasOwn(settings, 'sandbox')) return undefined
  const workspaceSettings = new Set([join(projectRoot, '.claude', 'settings.local.json')])
  for (const directory of new Set([cwd, worktreeRoot])) {
    workspaceSettings.add(join(directory, '.claude', 'settings.json'))
    workspaceSettings.add(join(directory, '.claude', 'settings.local.json'))
    const mcp = await readObject(join(directory, '.mcp.json'))
    if ((mcp.mcpServers !== undefined && !object(mcp.mcpServers)) || mcp.mcpServers?.powercontext) return undefined
  }
  for (const path of workspaceSettings) {
    const value = await readObject(path)
    if (value.env || value.pluginConfigs || value.enabledPlugins || value.disableAllHooks
      || Object.hasOwn(value, 'permissions') || Object.hasOwn(value, 'sandbox')
      || value.mcpServers || value.deniedMcpServers || value.allowedMcpServers
      || value.enabledMcpjsonServers || value.disabledMcpjsonServers) return undefined
  }
  for (const name of ['managed-settings.json', 'managed-mcp.json']) {
    if (Object.keys(await readObject(join(managed, name))).length) return undefined
  }
  try {
    if ((await readdir(join(managed, 'managed-settings.d'))).some(name => name.endsWith('.json'))) return undefined
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error
  }
  return settings
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
  if (!options.environmentEvidence.terminal.trim() || !options.environmentEvidence.evidence.trim()) {
    throw new Error('Verified terminal evidence is required')
  }
  const env = { ...options.launchEnvironment }
  const globalConfigFile = env.CLAUDE_CONFIG_DIR
    ? join(options.configDir, '.claude.json') : join(env.HOME ?? homedir(), '.claude.json')
  const qualifiedTerminal = options.environmentEvidence.terminal
  const observeTerminal = async (deepLinkTerminal: unknown) => {
    // The CLI may otherwise fall back to a different installed emulator.
    if (!Object.hasOwn(terminalApps, qualifiedTerminal) || deepLinkTerminal !== qualifiedTerminal) return undefined
    if (process.platform === 'darwin') {
      try { await access(terminalApps[qualifiedTerminal]!, constants.F_OK) }
      catch (error) {
        if ((error as NodeJS.ErrnoException).code === 'ENOENT') return undefined
        throw error
      }
    }
    return qualifiedTerminal
  }
  const observeReceiverPolicy = async (cwd: string, signal: AbortSignal) => {
    signal.throwIfAborted()
    if (await realpath(options.configDir) !== options.configDir) return undefined
    const selectedConfig = env.CLAUDE_CONFIG_DIR ?? join(env.HOME ?? homedir(), '.claude')
    if (!isAbsolute(selectedConfig) || await realpath(selectedConfig) !== options.configDir) return undefined
    const preferences = await readObject(globalConfigFile)
    const terminal = await observeTerminal(preferences.deepLinkTerminal)
    if (!terminal) return undefined
    const project = await observeProject(cwd, env, signal)
    const projectMcp = observeProjectMcp(preferences, project.projectRoot)
    if (!projectMcp) return undefined
    const settings = await observeSettings(options.configDir, cwd, project)
    if (!settings) return undefined
    return { terminal, projectMcp, settings }
  }
  const inspect = async (executable: string, cwd: string, signal: AbortSignal): Promise<TargetEnvironment | undefined> => {
    try {
      const policy = await observeReceiverPolicy(cwd, signal)
      if (!policy) return undefined
      const { terminal, projectMcp, settings } = policy
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
      const plugin = await installedClaudePowerContext(executable, targetEnv, signal, cwd)
      if (plugin?.enabled !== true || plugin.errors?.length || !plugin.installPath || !isAbsolute(plugin.installPath)) return undefined
      const pluginRoot = await realpath(plugin.installPath)
      const manifest = await readObject(join(pluginRoot, '.claude-plugin', 'plugin.json'))
      const mcp = await readObject(join(pluginRoot, '.mcp.json'))
      const hooks = await readObject(join(pluginRoot, 'hooks/hooks.json'))
      const promptHooks = hooks.hooks?.UserPromptSubmit
      if (!Array.isArray(promptHooks) || !promptHooks.some(entry => !entry?.matcher && Array.isArray(entry?.hooks)
        && entry.hooks.some((hook: any) => hook?.type === 'command' && hook.command === 'python3'
          && Array.isArray(hook.args) && hook.args.length === 1
          && hook.args[0] === '${CLAUDE_PLUGIN_ROOT}/hooks/user_prompt_submit.py'))) return undefined
      if (mcp.powercontext?.url !== '${user_config.server_url}/mcp'
        || mcp.powercontext?.headers?.Authorization !== '${POWERCONTEXT_CLAUDE_AUTHORIZATION:-}') return undefined
      const helper = mcp.powercontext.headersHelper
      if (helper !== undefined && helper !== mcpHeadersHelper) return undefined
      const usesHelper = helper === mcpHeadersHelper
      const receiverFile = usesHelper ? 'powercontext_lifecycle/dsh_handoff.py' : 'hooks/dsh_handoff.py'
      const receiverFiles = [await hashPluginFile(pluginRoot, 'hooks/user_prompt_submit.py', signal),
        await hashPluginFile(pluginRoot, receiverFile, signal)]
      if (usesHelper) receiverFiles.push(await hashPluginFile(pluginRoot, 'scripts/mcp_headers.py', signal))
      const pluginConfig = settings.pluginConfigs?.['powercontext@powercontext']
      if (pluginConfig !== undefined && !object(pluginConfig)
        || pluginConfig?.options !== undefined && !object(pluginConfig.options)) return undefined
      const opts = pluginConfig?.options ?? {}
      const endpoint = opts.server_url ?? manifest.userConfig?.server_url?.default
      if (typeof endpoint !== 'string' || !endpoint.trim()) return undefined
      targetEnv.CLAUDE_PLUGIN_OPTION_SERVER_URL = endpoint
      if (typeof opts.allow_insecure_http === 'boolean') targetEnv.CLAUDE_PLUGIN_OPTION_ALLOW_INSECURE_HTTP = String(opts.allow_insecure_http)
      const parsed = object(JSON.parse(await command(options.pythonExecutable, ['-c', PROBE, pluginRoot, cwd, endpoint,
        usesHelper ? 'headers-helper' : 'static-header'], targetEnv, signal, cwd)))
      if (!parsed || typeof parsed.serverIdentity !== 'string' || !parsed.serverIdentity
        || typeof parsed.scopeId !== 'string' || !parsed.scopeId) return undefined
      const { generationKey, qualifiedPackageRoots } = options.packageStorage()
      if (generationKey.length < 32) return undefined
      const roots = await Promise.all(qualifiedPackageRoots.map(root => realpath(root)))
      const credentialFile = join(options.configDir, 'powercontext', 'credentials.json')
      const clientsFile = targetEnv.POWERCONTEXT_CLIENT_CONFIG_FILE ?? join(targetEnv.HOME ?? '', '.config', 'powercontext', 'clients.json')
      const credentials = await readObject(credentialFile)
      const clients = await readObject(clientsFile)
      const generation = createHmac('sha256', generationKey).update(JSON.stringify([await realpath(executable), pluginRoot,
        manifest, mcp, settings, Object.fromEntries(Object.entries(targetEnv).sort(([a], [b]) => a < b ? -1 : a > b ? 1 : 0)),
        credentials, clients, roots, parsed, promptHooks, receiverFiles, options.environmentEvidence, terminal, projectMcp])).digest('hex')
      return { effectiveCwd: cwd, serverIdentity: parsed.serverIdentity, scopeId: parsed.scopeId,
        configurationGeneration: generation, newSession: true, unsentPrefill: true, packageAccess: roots.length > 0 }
    } catch {
      if (signal.aborted) signal.throwIfAborted()
      return undefined
    }
  }
  return {
    async handler(executable, _version, signal) {
      try {
        signal.throwIfAborted()
        if (!await observeTerminal((await readObject(globalConfigFile)).deepLinkTerminal)) return 'needs_user_action'
        return await registeredHandler(executable, env, signal)
      } catch {
        signal.throwIfAborted()
        return 'unknown'
      }
    },
    environment: inspect,
    async canReadPackage(_executable, bundle, signal) {
      try {
        signal.throwIfAborted()
        if (!await observeReceiverPolicy(bundle.binding.effectiveCwd, signal)) return false
        const path = await realpath(bundle.packagePath)
        const roots = await Promise.all(options.packageStorage().qualifiedPackageRoots.map(root => realpath(root)))
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
