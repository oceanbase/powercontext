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

import { spawn, execFile } from 'node:child_process'
import { createHash, createHmac, randomBytes } from 'node:crypto'
import { lstat, mkdtemp, readFile, realpath, rm, writeFile } from 'node:fs/promises'
import { dirname, isAbsolute, join, relative } from 'node:path'
import { createInterface } from 'node:readline'
import { promisify } from 'node:util'
import type { LaunchBundle } from '../contracts.ts'
import { HandoffError } from '../errors.ts'
import type { InstallationResult, IntegrationCheck, PresentTarget, TargetEnvironment } from '../targets.ts'
import type { CodexDesktopRuntime } from './codex-desktop.ts'

const execFileAsync = promisify(execFile)
const REQUIRED_TOOLS = ['get_connection_identity', 'continue_handoff', 'acknowledge_handoff']
const SAFE_PART = /^[A-Za-z0-9._-]+$/u
const MAX_OUTPUT = 2 * 1024 * 1024

export interface CodexDesktopRuntimeOptions {
  /** Paths belong to the exact Desktop profile and delivery store selected by the Host. */
  readonly cliPath: string
  readonly codexHome: string
  readonly deliveryRoot: string
  /** Persistent Host key; never logged or persisted by this adapter. */
  readonly generationKey: Buffer
  readonly setup?: { readonly executable: string; readonly source?: string; readonly serverUrl: string }
}

type ObjectValue = Record<string, unknown>
function object(value: unknown): ObjectValue {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('Invalid Codex observation')
  return value as ObjectValue
}
function text(value: unknown): string {
  if (typeof value !== 'string' || !value.trim() || value !== value.trim()) throw new Error('Invalid Codex observation')
  return value
}

// App-server JSON maps can arrive in a different order on every process start.
// Keep array order significant, since it can express configuration precedence.
function configurationJson(value: unknown): string {
  return JSON.stringify(value, (_key, item: unknown) => {
    if (!item || typeof item !== 'object' || Array.isArray(item)) return item
    const entries = Object.entries(item)
    entries.sort(([left], [right]) => left < right ? -1 : left > right ? 1 : 0)
    return Object.fromEntries(entries)
  })
}
function validServerUrl(value: string): boolean {
  try {
    const url = new URL(value)
    return ['http:', 'https:'].includes(url.protocol) && !!url.hostname
      && !url.username && !url.password && !url.search && !url.hash
  } catch { return false }
}
function envFor(options: CodexDesktopRuntimeOptions): NodeJS.ProcessEnv {
  return { ...process.env, CODEX_HOME: options.codexHome, PATH: `${dirname(options.cliPath)}:${process.env.PATH ?? '/usr/bin:/bin'}` }
}
async function command(file: string, args: string[], cwd: string, env: NodeJS.ProcessEnv, signal: AbortSignal): Promise<string> {
  const { stdout } = await execFileAsync(file, args, { cwd, env, signal, timeout: 30_000, maxBuffer: MAX_OUTPUT, encoding: 'utf8' })
  return stdout
}

type AppServerObservation = { hooks: ObjectValue; config: ObjectValue; mcp: ObjectValue;
  requirements: ObjectValue; plugins: ObjectValue }

/** One short-lived app-server, with no thread creation or model calls. */
async function inspectAppServer(options: CodexDesktopRuntimeOptions, cwd: string, signal: AbortSignal): Promise<AppServerObservation>
async function inspectAppServer(options: CodexDesktopRuntimeOptions, cwd: string, signal: AbortSignal,
  mode: 'requirements'): Promise<{ requirements: ObjectValue }>
async function inspectAppServer(options: CodexDesktopRuntimeOptions, cwd: string, signal: AbortSignal,
  mode?: 'requirements'): Promise<AppServerObservation | { requirements: ObjectValue }> {
  signal.throwIfAborted()
  const child = spawn(options.cliPath, ['app-server', '--listen', 'stdio://'], {
    cwd, env: envFor(options), stdio: ['pipe', 'pipe', 'ignore'], shell: false,
  })
  const lines = createInterface({ input: child.stdout })
  let sequence = 0
  let size = 0
  const pending = new Map<number, { resolve: (value: unknown) => void; reject: (reason: Error) => void }>()
  let failure: Error | undefined
  const fail = () => {
    failure ??= new Error('Codex runtime probe unavailable')
    for (const request of pending.values()) request.reject(failure)
    pending.clear()
  }
  const abort = () => { fail(); child.kill('SIGKILL') }
  const timer = setTimeout(abort, 30_000)
  signal.addEventListener('abort', abort, { once: true })
  child.once('error', fail)
  child.once('exit', fail)
  lines.on('line', line => {
    size += Buffer.byteLength(line)
    if (size > MAX_OUTPUT) { abort(); return }
    try {
      const message = object(JSON.parse(line))
      if (typeof message.id !== 'number') return
      const request = pending.get(message.id)
      if (!request) return
      pending.delete(message.id)
      if (message.error !== undefined) request.reject(new Error('Codex runtime request failed'))
      else request.resolve(message.result)
    } catch { abort() }
  })
  const call = (method: string, params: ObjectValue): Promise<unknown> => {
    if (failure || signal.aborted) return Promise.reject(failure ?? new Error('Cancelled'))
    const id = ++sequence
    return new Promise((resolve, reject) => {
      pending.set(id, { resolve, reject })
      child.stdin.write(`${JSON.stringify({ id, method, params })}\n`, error => { if (error) fail() })
    })
  }
  try {
    const initialized = object(await call('initialize', { clientInfo: { name: 'powercontext_handoff', version: '1' },
      capabilities: { experimentalApi: true, requestAttestation: false } }))
    if (await realpath(text(initialized.codexHome)) !== await realpath(options.codexHome) || initialized.platformOs !== 'macos') {
      throw new Error('Codex runtime profile mismatch')
    }
    child.stdin.write('{"method":"initialized","params":{}}\n')
    if (mode === 'requirements') {
      return { requirements: object(await call('configRequirements/read', {})) }
    }
    const [hooks, config, mcp, requirements, plugins] = await Promise.all([
      call('hooks/list', { cwds: [cwd] }), call('config/read', { cwd, includeLayers: false }),
      call('mcpServerStatus/list', { serverName: 'powercontext', limit: 100, detail: 'toolsAndAuthOnly' }),
      call('configRequirements/read', {}), call('plugin/installed', { cwds: [cwd] }),
    ])
    return { hooks: object(hooks), config: object(config), mcp: object(mcp), requirements: object(requirements), plugins: object(plugins) }
  } finally {
    clearTimeout(timer)
    signal.removeEventListener('abort', abort)
    lines.close()
    child.stdin.destroy()
    child.kill('SIGKILL')
  }
}

/** Version-qualified Desktop stores its local composer selection separately from config.toml. */
async function desktopPermission(options: CodexDesktopRuntimeOptions, requirements: ObjectValue): Promise<{ profile: string; selection: string }> {
  const state = object(JSON.parse(await readFile(join(options.codexHome, '.codex-global-state.json'), 'utf8')))
  const selections = Object.entries(state).filter(([key]) => key.startsWith('electron-owner-atoms:'))
    .map(([, value]) => object(value)['permission-selection-by-host-id:local']).filter(value => value != null)
  if (selections.length !== 1) throw new Error('Desktop permission selection unavailable')
  const selection = object(selections[0])
  if (selection.kind !== 'agent-mode') throw new Error('Unqualified Desktop permission selection')
  const modes: Record<string, { profile: string; sandbox: string; approval: string; reviewer: string }> = {
    'read-only': { profile: ':read-only', sandbox: 'read-only', approval: 'on-request', reviewer: 'user' },
    auto: { profile: ':workspace', sandbox: 'workspace-write', approval: 'on-request', reviewer: 'user' },
    'guardian-approvals': { profile: ':workspace', sandbox: 'workspace-write', approval: 'on-request', reviewer: 'guardian_subagent' },
    'full-access': { profile: ':danger-full-access', sandbox: 'danger-full-access', approval: 'never', reviewer: 'user' },
  }
  const mode = modes[text(selection.agentMode)]
  if (!mode) throw new Error('Desktop permission selection unavailable')
  const configured = requirements.requirements === null ? null : object(requirements.requirements)
  if (configured) {
    if (configured.allowedPermissionProfiles != null && object(configured.allowedPermissionProfiles)[mode.profile] !== true) {
      throw new Error('Desktop profile restricted')
    }
    for (const [name, value] of [['allowedSandboxModes', mode.sandbox], ['allowedApprovalPolicies', mode.approval],
      ['allowedApprovalsReviewers', mode.reviewer]] as const) {
      if (configured[name] != null && (!Array.isArray(configured[name]) || !configured[name].includes(value))) {
        throw new Error('Desktop permission restricted')
      }
    }
  }
  return { profile: mode.profile, selection: `${selection.kind}:${String(selection.agentMode)}:${mode.profile}` }
}

interface InstalledPlugin { pluginId: string; marketplace: string; version: string; enabled: boolean }
function installedPlugin(result: ObjectValue): InstalledPlugin | undefined {
  if (!Array.isArray(result.marketplaces) || !Array.isArray(result.marketplaceLoadErrors) || result.marketplaceLoadErrors.length) {
    throw new Error('Invalid Codex plugin inventory')
  }
  const found = result.marketplaces.map(object).flatMap(marketplace => {
    if (!Array.isArray(marketplace.plugins)) throw new Error('Invalid marketplace inventory')
    return marketplace.plugins.map(object).filter(item => item.name === 'powercontext' && item.installed === true)
      .map((item): ObjectValue => ({ ...item, marketplaceName: marketplace.name }))
  })
  if (found.length > 1) throw new Error('Ambiguous PowerContext installation')
  if (!found.length) return undefined
  const item = found[0]
  const marketplace = text(item.marketplaceName), version = text(item.localVersion), pluginId = text(item.id)
  if (!SAFE_PART.test(marketplace) || !SAFE_PART.test(version) || ['.', '..'].includes(marketplace) || ['.', '..'].includes(version)
    || pluginId !== `powercontext@${marketplace}` || typeof item.enabled !== 'boolean') throw new Error('Invalid plugin identity')
  return { marketplace, version, pluginId, enabled: item.enabled }
}

function mcpReady(result: ObjectValue, plugin: InstalledPlugin): boolean {
  if (!Array.isArray(result.data) || result.nextCursor) return false
  const servers = result.data.map(object).filter(item => item.name === 'powercontext' && item.pluginId === plugin.pluginId)
  if (servers.length !== 1 || servers[0].toolsError) return false
  const tools = object(servers[0].tools)
  return REQUIRED_TOOLS.every(name => Object.hasOwn(tools, name))
}

async function hookEvidence(result: ObjectValue, cwd: string, plugin: InstalledPlugin, root: string): Promise<string[]> {
  if (!Array.isArray(result.data) || result.data.length !== 1) throw new Error('Invalid hook inventory')
  const entry = object(result.data[0])
  if (await realpath(text(entry.cwd)) !== await realpath(cwd) || !Array.isArray(entry.hooks)
    || !Array.isArray(entry.errors) || entry.errors.length) throw new Error('Hook inventory unavailable')
  const hooks = entry.hooks.map(object)
  const expected = [
    { event: 'sessionStart', file: 'session_binding.py' },
    { event: 'preToolUse', file: 'bind_tools.py' },
    { event: 'userPromptSubmit', file: 'recall.py' },
  ]
  const hashes: string[] = []
  for (const wanted of expected) {
    const matches = hooks.filter(hook => hook.pluginId === plugin.pluginId && hook.eventName === wanted.event
      && hook.source === 'plugin' && hook.handlerType === 'command' && typeof hook.command === 'string'
      && hook.command.includes(`/hooks/${wanted.file}`))
    if (matches.length !== 1) throw new Error('PowerContext hook unavailable')
    const hook = matches[0]
    if (hook.enabled !== true || !['trusted', 'managed'].includes(String(hook.trustStatus))
      || await realpath(text(hook.sourcePath)) !== await realpath(join(root, 'hooks/hooks.json'))
      || (wanted.event === 'sessionStart'
        ? typeof hook.matcher !== 'string' || !/^[a-z]+(?:\|[a-z]+)*$/u.test(hook.matcher) || !hook.matcher.split('|').includes('startup')
        : wanted.event === 'preToolUse' ? hook.matcher !== 'mcp__powercontext__.*' : hook.matcher != null && hook.matcher !== '')) {
      throw new Error('PowerContext hook not trusted')
    }
    hashes.push(text(hook.currentHash))
  }
  return hashes
}

// Use the installed plugin's settings and resolver, including its own URL-bound credential.
// Only identity, Scope and a keyed configuration fingerprint leave the subprocess.
const SCOPE_PROBE = String.raw`
import hashlib,hmac,json,os,shlex,sys,time
from pathlib import Path
from urllib.request import Request
sys.path.insert(0,sys.argv[1])
from settings import CodexPluginSettings,_stored_authorization
from scripts.scope_binding import resolve_scope_id,open_bounded,_read_bounded
s=CodexPluginSettings()
configuration=json.loads((Path(sys.argv[1])/'.mcp.json').read_text())['mcpServers']['powercontext']
helper=configuration.get('http_headers_helper')
if helper:
 root=Path(sys.argv[1]).resolve()
 credential=Path(os.environ['CODEX_HOME']).absolute()/'powercontext'/'credentials.json'
 expected=['uv','run','--frozen','--quiet','--project',str(root),'python',str(root/'mcp_headers.py'),'--credential-file',str(credential)]
 if shlex.split(helper)!=expected: raise ValueError('unqualified credential helper')
 stored=_stored_authorization(s.server_url)
 if stored and (s.authorization is None or s.authorization.get_secret_value()!=stored):
  raise ValueError('MCP and hook credentials differ')
deadline=time.monotonic()+s.http_budget_seconds
scope=resolve_scope_id(sys.argv[2],session_id=None,settings=s,deadline=deadline)
h={"Accept":"application/json"}
if s.authorization is not None: h["Authorization"]=s.authorization.get_secret_value()
req=Request(s.server_url+"/v1/connection/identity",headers=h,method="GET")
with open_bounded(req,timeout=s.request_timeout_seconds) as r:
 identity=json.loads(_read_bounded(r,deadline=deadline))
credential=s.authorization.get_secret_value() if s.authorization else ""
values=json.dumps([s.server_url,s.scope_id,s.allow_insecure_http,credential],separators=(",",":"),ensure_ascii=False)
fingerprint=hmac.new(bytes.fromhex(os.environ["PC_HANDOFF_OBSERVATION_KEY"]),values.encode(),hashlib.sha256).hexdigest()
print(json.dumps({"scope":scope,"identity":identity,"endpoint":s.server_url,"generation":fingerprint,"hasHelper":bool(helper)},separators=(",",":")))
`

async function scopeObservation(options: CodexDesktopRuntimeOptions, cwd: string, root: string, key: string, signal: AbortSignal) {
  const python = join(root, '.venv/bin/python')
  const env = { ...envFor(options), PC_HANDOFF_OBSERVATION_KEY: key }
  const result = object(JSON.parse(await command(python, ['-c', SCOPE_PROBE, root, cwd], cwd, env, signal)))
  const identity = object(result.identity)
  const serverIdentity = text(identity.server_id)
  const scopeId = text(result.scope)
  const auth = identity.authentication, access = identity.access_mode
  const principal = identity.principal === null ? null : object(identity.principal)
  if ((auth !== 'disabled' && auth !== 'authenticated') || (access !== 'disabled' && access !== 'enforced')
    || (auth === 'disabled' && principal !== null) || (access === 'enforced' && auth !== 'authenticated')
    || (auth === 'authenticated' && (!principal || !['user', 'service'].includes(String(principal.type)) || !text(principal.id)))) {
    throw new Error('Invalid target identity')
  }
  if (typeof result.hasHelper !== 'boolean') throw new Error('Invalid helper state')
  return { serverIdentity, scopeId, identity, endpoint: text(result.endpoint), generation: text(result.generation), hasHelper: result.hasHelper }
}

async function sandboxHash(options: CodexDesktopRuntimeOptions, profile: string, cwd: string, path: string, signal: AbortSignal): Promise<string> {
  const output = await command(options.cliPath, ['sandbox', '-P', profile, '--include-managed-config',
    '-C', cwd, '--', '/usr/bin/shasum', '-a', '256', '--', path],
    cwd, envFor(options), signal)
  const hash = output.match(/^([a-f0-9]{64})\s/u)?.[1]
  if (!hash) throw new Error('Sandbox read failed')
  return hash
}

export function createCodexDesktopRuntime(options: CodexDesktopRuntimeOptions): CodexDesktopRuntime {
  if (![options.cliPath, options.codexHome, options.deliveryRoot].every(isAbsolute)
    || options.cliPath.includes(':') || !Buffer.isBuffer(options.generationKey) || options.generationKey.length !== 32
    || (options.setup && (!isAbsolute(options.setup.executable) || (options.setup.source !== undefined && !isAbsolute(options.setup.source))
      || !validServerUrl(options.setup.serverUrl)))) {
    throw new Error('Invalid Codex runtime paths')
  }
  const key = Buffer.from(options.generationKey).toString('hex')
  const configurationKey = `codex:${options.codexHome}`
  return {
    configurationKey,
    async checkIntegration(_target, signal): Promise<IntegrationCheck> {
      try {
        const observation = await inspectAppServer(options, options.codexHome, signal)
        const plugin = installedPlugin(observation.plugins)
        if (!plugin) return { state: 'missing', repairable: !!options.setup }
        if (!plugin.enabled) return { state: 'disabled', repairable: false }
        return mcpReady(observation.mcp, plugin)
          ? { state: 'ready' } : { state: 'needs_user_action', action: 'configure_target' }
      } catch {
        signal.throwIfAborted()
        return { state: 'unknown' }
      }
    },
    async installIntegration(_target, signal): Promise<InstallationResult> {
      signal.throwIfAborted()
      const setup = options.setup
      if (!setup) return { state: 'needs_user_action', action: 'configure_target' }
      return new Promise(resolve => {
        const args = ['setup', 'codex', ...(setup.source ? ['--source', setup.source] : []), '--server-url', setup.serverUrl, '--json']
        const child = spawn(setup.executable, args,
          { env: envFor(options), cwd: options.codexHome, stdio: 'ignore', shell: false, detached: true })
        let started = false, interrupted = false
        const stop = () => { interrupted = true; if (child.pid) { try { process.kill(-child.pid, 'SIGKILL') } catch { /* exited */ } } }
        const timer = setTimeout(stop, 120_000)
        signal.addEventListener('abort', stop, { once: true })
        child.once('spawn', () => { started = true; if (signal.aborted) stop() })
        child.once('error', () => { clearTimeout(timer); signal.removeEventListener('abort', stop); resolve({ state: started ? 'unknown' : 'failed' }) })
        child.once('close', code => {
          clearTimeout(timer); signal.removeEventListener('abort', stop)
          resolve(!interrupted && code === 0 ? { state: 'needs_user_action', action: 'restart_target' } : { state: 'unknown' })
        })
      })
    },
    async resolveEnvironment(_target, cwd, signal): Promise<TargetEnvironment> {
      try {
        const observed = await inspectAppServer(options, cwd, signal)
        const plugin = installedPlugin(observed.plugins)
        if (!plugin?.enabled) throw new Error('Plugin unavailable')
        const root = join(options.codexHome, 'plugins/cache', plugin.marketplace, 'powercontext', plugin.version)
        const [scope, rootPath] = await Promise.all([scopeObservation(options, cwd, root, key, signal), realpath(root)])
        if (!mcpReady(observed.mcp, plugin)) throw new Error('MCP unavailable')
        const configuration = object(observed.config.config)
        if (configuration.mcp_servers != null && Object.hasOwn(object(configuration.mcp_servers), 'powercontext')) {
          throw new Error('PowerContext MCP override not qualified')
        }
        if (configuration.plugins != null) {
          const settings = object(configuration.plugins)[plugin.pluginId]
          if (settings != null && object(settings).mcp_servers != null) throw new Error('Plugin MCP override not qualified')
        }
        const mcpServer = (observed.mcp.data as unknown[]).map(object)
          .find(item => item.name === 'powercontext' && item.pluginId === plugin.pluginId)
        if (!mcpServer || mcpServer.httpOrigin !== new URL(scope.endpoint).origin) throw new Error('MCP origin differs')
        const hooks = await hookEvidence(observed.hooks, cwd, plugin, rootPath)
        const receiveHandler = join(rootPath, 'hooks/dsh_handoff.py')
        const receiverInfo = await lstat(receiveHandler)
        if (!receiverInfo.isFile() || receiverInfo.size < 1 || receiverInfo.size > 64 * 1024) {
          throw new Error('Handoff receiver unavailable')
        }
        const receiverHash = createHash('sha256').update(await readFile(receiveHandler, { signal })).digest('hex')
        const permission = await desktopPermission(options, observed.requirements)
        const inventory: unknown = JSON.parse(await command(options.cliPath, ['mcp', 'list', '--json'], cwd, envFor(options), signal))
        if (!Array.isArray(inventory)) throw new Error('Invalid MCP inventory')
        const servers = inventory.map(object).filter(item => item.name === 'powercontext' && item.enabled === true)
        if (servers.length !== 1) throw new Error('MCP configuration unavailable')
        const transport = object(servers[0].transport)
        const headers = object(transport.env_http_headers)
        if (headers.Authorization !== 'POWERCONTEXT_CODEX_AUTHORIZATION' || Object.keys(headers).length !== 1
          || !!transport.http_headers_helper !== scope.hasHelper || transport.bearer_token_env_var
          || (transport.http_headers && Object.keys(object(transport.http_headers)).length)) {
          throw new Error('MCP authorization configuration differs')
        }
        const url = new URL(text(transport.url))
        const expected = new URL(`${scope.endpoint.replace(/\/$/u, '')}/mcp/`)
        if (url.username || url.password || url.search || url.hash || url.href.replace(/\/$/u, '') !== expected.href.replace(/\/$/u, '')) {
          throw new Error('MCP and hook endpoints differ')
        }
        const temporary = await mkdtemp(join(options.deliveryRoot, '.codex-probe-'))
        let packageAccess = false
        try {
          const path = join(temporary, 'probe')
          const contents = randomBytes(32)
          await writeFile(path, contents, { mode: 0o600, flag: 'wx' })
          packageAccess = await sandboxHash(options, permission.profile, cwd, path, signal) === createHash('sha256').update(contents).digest('hex')
        } finally { await rm(temporary, { recursive: true, force: true }) }
        const configurationGeneration = createHmac('sha256', Buffer.from(key, 'hex')).update(configurationJson([
          plugin, hooks, receiverHash, scope.generation, scope.endpoint, scope.identity, scope.serverIdentity, scope.scopeId,
          permission, observed.config, observed.requirements,
        ])).digest('hex')
        return { effectiveCwd: await realpath(cwd), serverIdentity: scope.serverIdentity, scopeId: scope.scopeId,
          configurationGeneration, newSession: true, unsentPrefill: true, packageAccess }
      } catch {
        signal.throwIfAborted()
        throw new HandoffError('integration_unavailable')
      }
    },
    async canReadPackage(_target, bundle: LaunchBundle, signal): Promise<boolean> {
      try {
        const root = await realpath(options.deliveryRoot)
        const observed = await inspectAppServer(options, bundle.binding.effectiveCwd, signal, 'requirements')
        const permission = await desktopPermission(options, observed.requirements)
        const path = await realpath(bundle.packagePath)
        const rel = relative(root, path)
        if (!rel || rel.startsWith('..') || isAbsolute(rel) || !/^[a-f0-9]{64}$/u.test(bundle.packageSha256)) return false
        return await sandboxHash(options, permission.profile, bundle.binding.effectiveCwd, path, signal) === bundle.packageSha256
      } catch {
        signal.throwIfAborted()
        return false
      }
    },
  }
}
