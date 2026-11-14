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
import { mkdir, mkdtemp, readFile, realpath, rm, symlink, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { afterEach, expect, it } from 'vitest'
import { createCodexDesktopRuntime } from '../src/handoff/adapters/codex-desktop-runtime.ts'
import type { LaunchBundle } from '../src/handoff/contracts.ts'

const signal = new AbortController().signal
const target = { state: 'present', version: 'fixture', configurationKey: 'fixture' } as const
let temp = ''

async function fixture() {
  temp = await mkdtemp(join(tmpdir(), 'pc-codex-runtime-'))
  const home = join(temp, 'home'), cwd = join(temp, 'project'), deliveryRoot = join(temp, 'delivery')
  const root = join(home, 'plugins/cache/example/powercontext/1.0.0')
  await Promise.all(['hooks', 'scripts', '.venv/bin'].map(path => mkdir(join(root, path), { recursive: true })))
  await mkdir(cwd); await mkdir(deliveryRoot)
  await writeFile(join(home, '.codex-global-state.json'), JSON.stringify({
    'electron-owner-atoms:fixture': { 'permission-selection-by-host-id:local': { kind: 'agent-mode', agentMode: 'auto' } },
  }))
  await writeFile(join(root, 'hooks/hooks.json'), '{}')
  await writeFile(join(root, 'hooks/dsh_handoff.py'), '# PowerContext handoff receiver')
  await writeFile(join(root, '.mcp.json'), JSON.stringify({ mcpServers: { powercontext: {} } }))
  await symlink('/usr/bin/python3', join(root, '.venv/bin/python'))
  await writeFile(join(root, 'settings.py'), `import json,os\ndef _state(): return json.load(open(os.path.join(os.path.dirname(__file__),'scope.json')))\ndef _stored_authorization(url): return _state().get('stored')\nclass Secret:\n def __init__(self,value): self.value=value\n def get_secret_value(self): return self.value\nclass CodexPluginSettings:\n def __init__(self):\n  self.server_url='http://127.0.0.1:19123'\n  self.authorization=Secret(_state()['auth']) if _state().get('auth') else None\n  self.scope_id=None\n  self.allow_insecure_http=False\n  self.http_budget_seconds=5\n  self.request_timeout_seconds=3\n`)
  await writeFile(join(root, 'scripts/scope_binding.py'), `import json,os\ndef resolve_scope_id(cwd,session_id,settings,deadline):\n return json.load(open(os.path.join(os.path.dirname(__file__),'../scope.json')))['scope']\nclass Response:\n def __enter__(self): return self\n def __exit__(self,*args): pass\ndef open_bounded(request,timeout): return Response()\ndef _read_bounded(response,deadline): return b'{"server_id":"server-a","authentication":"disabled","access_mode":"disabled","principal":null}'\n`)
  await writeFile(join(root, 'scope.json'), '{"scope":"scope-a"}')
  const state = join(temp, 'state.json')
  await writeFile(state, JSON.stringify({ enabled: true, installed: true, trusted: true, tools: true, sandbox: true }))
  const cli = join(temp, 'codex')
  await writeFile(cli, `#!/usr/bin/env node
const fs=require('fs'),crypto=require('crypto'),readline=require('readline');
const s=JSON.parse(fs.readFileSync(${JSON.stringify(state)})); const a=process.argv.slice(2);
const root=${JSON.stringify(root)}, cwd=process.cwd(), home=${JSON.stringify(home)};
const plugin={name:'powercontext',installed:true,enabled:s.enabled,marketplaceName:'example',version:'1.0.0',pluginId:'powercontext@example'};
if(a[0]==='plugin'){console.log(JSON.stringify({installed:s.installed?[plugin]:[]}));process.exit()}
if(a[0]==='mcp'){console.log(JSON.stringify([{name:'powercontext',enabled:true,transport:{url:s.endpoint||'http://127.0.0.1:19123/mcp/',http_headers_helper:s.helper?'[REDACTED]':null,env_http_headers:{Authorization:'POWERCONTEXT_CODEX_AUTHORIZATION'}}}]));process.exit()}
if(a[0]==='sandbox'){if(!s.sandbox||a[a.indexOf('-P')+1]!== (s.expectedProfile||':workspace'))process.exit(1);let p=a.at(-1);console.log(crypto.createHash('sha256').update(fs.readFileSync(p)).digest('hex')+'  '+p);process.exit()}
if(a[0]==='app-server'){
 const hooks=['sessionStart','preToolUse','userPromptSubmit'].filter(name=>!s.noReceiveHook||name!=='userPromptSubmit').map((eventName,i)=>({pluginId:plugin.pluginId,eventName,source:'plugin',handlerType:'command',command:root+'/hooks/'+['session_binding.py','bind_tools.py','recall.py'][i],sourcePath:root+'/hooks/hooks.json',enabled:true,trustStatus:s.trusted?'trusted':'modified',matcher:['startup|resume|clear|compact','mcp__powercontext__.*',null][i],currentHash:'hash'+i}));
 readline.createInterface({input:process.stdin}).on('line',line=>{let q=JSON.parse(line);if(!q.id)return;let result;
 if(q.method==='plugin/installed' && s.ignorePlugin)return;
 if(q.method==='initialize')result={codexHome:home,platformOs:'macos'};
 if(q.method==='hooks/list')result={data:[{cwd:q.params.cwds[0],hooks,errors:[],warnings:[]}]};
 if(q.method==='config/read')result={config:{sandbox_mode:s.mode||'workspace-write',...(s.override?{mcp_servers:{powercontext:{}}}:{})},origins:s.reverseOrigins?{z:{name:'z'},a:{name:'a'}}:{a:{name:'a'},z:{name:'z'}},layers:null};
 if(q.method==='configRequirements/read')result={requirements:s.restrictions||null};
 if(q.method==='plugin/installed')result={marketplaces:[{name:'example',plugins:s.installed?[{name:'powercontext',id:'powercontext@example',installed:true,enabled:s.enabled,localVersion:'1.0.0'}]:[]}],marketplaceLoadErrors:[]};
 if(q.method==='mcpServerStatus/list')result={data:[{name:'powercontext',pluginId:plugin.pluginId,httpOrigin:'http://127.0.0.1:19123',tools:s.tools?{get_connection_identity:{},continue_handoff:{},acknowledge_handoff:{}}:{},toolsError:null}],nextCursor:null};
 console.log(JSON.stringify({id:q.id,result}));});
}
`, { mode: 0o755 })
  const setup = join(temp, 'powercontext')
  await writeFile(setup, `#!/bin/sh
if [ "$1" = doctor ]; then
 echo '{"hosts":{"codex":{"checks":{"plugin":{"status":"ok"},"mcp_configuration":{"status":"ok"},"authorization":{"status":"ok"},"mcp_tools":{"status":"ok"}}}}}'
 exit 0
fi
printf "%s\\n" "$@" > "$(dirname "$0")/setup-args"
`, { mode: 0o755 })
  const options = { cliPath: cli, codexHome: home, deliveryRoot, generationKey: Buffer.alloc(32, 7),
    setup: { executable: setup, source: temp, serverUrl: 'http://127.0.0.1:19123' } }
  return { home, root, cwd, deliveryRoot, state, options }
}

async function state(path: string, update: Record<string, unknown>) {
  const previous = JSON.parse(await readFile(path, 'utf8'))
  await writeFile(path, JSON.stringify({ ...previous, ...update }))
}

afterEach(async () => { if (temp) await rm(temp, { recursive: true, force: true }); temp = '' })

it('observes target tools, trusted hooks, Scope and sandbox package readability', async () => {
  const f = await fixture()
  const runtime = createCodexDesktopRuntime(f.options)
  expect(await runtime.checkIntegration(target, signal)).toEqual({ state: 'ready' })
  const env = await runtime.resolveEnvironment(target, f.cwd, signal)
  expect(env).toMatchObject({ effectiveCwd: await realpath(f.cwd), serverIdentity: 'server-a', scopeId: 'scope-a',
    newSession: true, unsentPrefill: true, packageAccess: true })
  const path = join(f.deliveryRoot, 'package.json'), bytes = Buffer.from('exact package')
  await writeFile(path, bytes, { mode: 0o600 })
  const bundle = { packagePath: path, packageSha256: createHash('sha256').update(bytes).digest('hex'),
    binding: { effectiveCwd: f.cwd } } as LaunchBundle
  expect(await runtime.canReadPackage(target, bundle, signal)).toBe(true)
  await writeFile(path, 'different')
  expect(await runtime.canReadPackage(target, bundle, signal)).toBe(false)
  await state(f.state, { trusted: false })
  await expect(runtime.resolveEnvironment(target, f.cwd, signal)).rejects.toThrow()
  await state(f.state, { trusted: true, noReceiveHook: true })
  await expect(runtime.resolveEnvironment(target, f.cwd, signal)).rejects.toThrow()
  await state(f.state, { noReceiveHook: false })
  await state(f.state, { trusted: true, endpoint: 'http://127.0.0.1:19123/other/mcp/' })
  await expect(runtime.resolveEnvironment(target, f.cwd, signal)).rejects.toThrow()
  await state(f.state, { endpoint: 'http://127.0.0.1:19123/mcp/' })
  await writeFile(join(f.home, '.codex-global-state.json'), JSON.stringify({
    'electron-owner-atoms:fixture': { 'permission-selection-by-host-id:local': { kind: 'agent-mode', agentMode: 'read-only' } },
  }))
  await state(f.state, { expectedProfile: ':read-only' })
  const readOnly = await runtime.resolveEnvironment(target, f.cwd, signal)
  expect(readOnly.configurationGeneration).not.toBe(env.configurationGeneration)
  await state(f.state, { restrictions: { allowedPermissionProfiles: { ':workspace': true } } })
  await expect(runtime.resolveEnvironment(target, f.cwd, signal)).rejects.toThrow()
}, 30_000)

it('rejects missing tools and respects installation, disabled, and sandbox outcomes', async () => {
  const f = await fixture()
  const runtime = createCodexDesktopRuntime(f.options)
  await state(f.state, { installed: false })
  expect(await runtime.checkIntegration(target, signal)).toEqual({ state: 'missing', repairable: true })
  expect(await runtime.installIntegration(target, signal)).toEqual({ state: 'needs_user_action', action: 'restart_target' })
  expect((await readFile(join(temp, 'setup-args'), 'utf8')).trim().split('\n')).toEqual([
    'setup', 'codex', '--source', temp, '--server-url', 'http://127.0.0.1:19123', '--json',
  ])
  const defaultSource = createCodexDesktopRuntime({ ...f.options,
    setup: { executable: f.options.setup.executable, serverUrl: 'http://127.0.0.1:19123' } })
  await defaultSource.installIntegration(target, signal)
  expect((await readFile(join(temp, 'setup-args'), 'utf8')).trim().split('\n')).toEqual([
    'setup', 'codex', '--server-url', 'http://127.0.0.1:19123', '--json',
  ])
  await state(f.state, { installed: true, enabled: false })
  expect(await runtime.checkIntegration(target, signal)).toEqual({ state: 'disabled', repairable: false })
  await state(f.state, { enabled: true, tools: false })
  expect(await runtime.checkIntegration(target, signal)).toEqual({ state: 'needs_user_action', action: 'configure_target' })
  await state(f.state, { tools: true, sandbox: false })
  await expect(runtime.resolveEnvironment(target, f.cwd, signal)).rejects.toThrow()
}, 30_000)

it('changes generation when routing or permission configuration changes', async () => {
  const f = await fixture()
  const runtime = createCodexDesktopRuntime(f.options)
  const before = await runtime.resolveEnvironment(target, f.cwd, signal)
  await state(f.state, { reverseOrigins: true })
  const afterRestart = await createCodexDesktopRuntime(f.options).resolveEnvironment(target, f.cwd, signal)
  expect(afterRestart.configurationGeneration).toBe(before.configurationGeneration)
  const otherKey = await createCodexDesktopRuntime({ ...f.options, generationKey: Buffer.alloc(32, 8) }).resolveEnvironment(target, f.cwd, signal)
  expect(otherKey.configurationGeneration).not.toBe(before.configurationGeneration)
  await writeFile(join(f.root, 'scope.json'), '{"scope":"scope-b"}')
  const changedScope = await runtime.resolveEnvironment(target, f.cwd, signal)
  expect(changedScope.scopeId).toBe('scope-b')
  expect(changedScope.configurationGeneration).not.toBe(before.configurationGeneration)
  await state(f.state, { mode: 'read-only' })
  const changedConfig = await runtime.resolveEnvironment(target, f.cwd, signal)
  expect(changedConfig.configurationGeneration).not.toBe(changedScope.configurationGeneration)
}, 30_000)

it('rejects a conflicting helper credential or an MCP override', async () => {
  const f = await fixture()
  const runtime = createCodexDesktopRuntime(f.options)
  const root = await realpath(f.root)
  const helper = ['uv', 'run', '--frozen', '--quiet', '--project', root, 'python', join(root, 'mcp_headers.py'),
    '--credential-file', join(f.home, 'powercontext/credentials.json')].join(' ')
  await writeFile(join(f.root, '.mcp.json'), JSON.stringify({ mcpServers: { powercontext: { http_headers_helper: helper } } }))
  await writeFile(join(f.root, 'scope.json'), JSON.stringify({ scope: 'scope-a', auth: 'Bearer fixture', stored: 'Bearer fixture' }))
  await state(f.state, { helper: true })
  expect((await runtime.resolveEnvironment(target, f.cwd, signal)).scopeId).toBe('scope-a')
  await writeFile(join(f.root, 'scope.json'), JSON.stringify({ scope: 'scope-a', auth: 'Bearer different', stored: 'Bearer fixture' }))
  await expect(runtime.resolveEnvironment(target, f.cwd, signal)).rejects.toThrow()
  await writeFile(join(f.root, 'scope.json'), JSON.stringify({ scope: 'scope-a', auth: 'Bearer fixture', stored: 'Bearer fixture' }))
  await state(f.state, { override: true })
  await expect(runtime.resolveEnvironment(target, f.cwd, signal)).rejects.toThrow()
}, 30_000)

it('checks package sandbox access without waiting for the plugin catalog', async () => {
  const f = await fixture()
  const runtime = createCodexDesktopRuntime(f.options)
  const path = join(f.deliveryRoot, 'package.json'), bytes = Buffer.from('exact package')
  await writeFile(path, bytes, { mode: 0o600 })
  await state(f.state, { ignorePlugin: true })
  const bundle = { packagePath: path, packageSha256: createHash('sha256').update(bytes).digest('hex'),
    binding: { effectiveCwd: f.cwd } } as LaunchBundle
  expect(await runtime.canReadPackage(target, bundle, AbortSignal.timeout(5_000))).toBe(true)
}, 15_000)
