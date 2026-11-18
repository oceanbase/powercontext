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
import { spawn } from 'node:child_process'
import { mkdirSync, mkdtempSync, realpathSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, relative } from 'node:path'
import { expect, it } from 'vitest'
import { PowerContextClient } from '../../src/client.ts'
import { LocalDeliveryService } from '../../src/handoff/delivery.ts'
import { LocalReceiptReader } from '../../src/handoff/receipt.ts'
import { buildReceiverPrompt } from '../../src/handoff/receiver-prompt.ts'
import { startPowerContextServer } from '../../scripts/e2e-server.mjs'

const RECEIVE = String.raw`
import asyncio, json, os, subprocess, sys
from pathlib import Path
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
root, endpoint, cwd, home, scope = sys.argv[1:]
prompt = sys.stdin.read()
env = {**os.environ, 'HOME': home, 'CLAUDE_CONFIG_DIR': home,
       'POWERCONTEXT_CLAUDE_SERVER_URL': endpoint, 'POWERCONTEXT_CLAUDE_CAPTURE_PROMPTS': 'false',
       'POWERCONTEXT_CLAUDE_SCOPE_ID': scope}
env.pop('POWERCONTEXT_CLAUDE_AUTHORIZATION', None)
hook = Path(root) / 'integrations/claude-code/plugins/powercontext/hooks/user_prompt_submit.py'
result = subprocess.run([sys.executable, str(hook)], input=json.dumps({
    'hook_event_name': 'UserPromptSubmit', 'session_id': 'test-receiver', 'cwd': cwd, 'prompt': prompt,
}), text=True, capture_output=True, cwd=cwd, env=env, check=True)
receive = json.loads(result.stdout)['hookSpecificOutput']['additionalContext']
assert receive.startswith('PowerContext DSH handoff receive procedure:'), receive
delivery = json.loads(receive.splitlines()[-1])
prepared = json.loads(Path(delivery['path']).read_bytes())['carrier']['prepared']
async def run():
    async with Client(StreamableHttpTransport(endpoint + '/mcp/')) as client:
        connection = (await client.call_tool('get_connection_identity', {})).structured_content
        assert connection['server_id'] == delivery['server_id'] and connection['access_mode'] == 'disabled'
        exact = {'scope_id': delivery['scope_id'], 'selection': 'prepared', 'prepared': prepared}
        resolved = (await client.call_tool('continue_handoff', exact)).structured_content
        assert resolved['status'] == 'resolved' and resolved['selection'] == 'prepared'
        assert resolved['content']['objective'] == 'Inspect the failure'
        # The test harness exercises the tools; a receiving model must make these checks itself.
        checks = dict.fromkeys(('live_state', 'capability', 'authorization'), 'confirmed')
        await client.call_tool('acknowledge_handoff', {**exact, 'source_id': delivery['receipt_source_id'],
            'receiver': delivery['target_id'], 'status': 'accepted', 'receiver_checks': checks})
asyncio.run(run())
`

it('receives an edited short draft through the real Claude hook and verifies the exact MCP acknowledgement', async () => {
  const server = await startPowerContextServer({ env: { POWERCONTEXT_SERVER_ACCESS_MODE: 'disabled' } })
  const parent = realpathSync(mkdtempSync(join(tmpdir(), 'dsh-receipt-http-')))
  let delivery: LocalDeliveryService | undefined
  let now = Date.now()
  try {
    const client = new PowerContextClient({ baseUrl: server.baseUrl, requestTimeoutMs: 5000 })
    const [scope, identity] = await Promise.all([client.request('get_default_scope'), client.request('get_connection_identity')])
    if (scope.kind !== 'json' || identity.kind !== 'json') throw new Error('missing scope or Server identity')
    const scopeId = (scope.value as { scope_id: string }).scope_id
    const serverIdentity = (identity.value as { server_id: string }).server_id
    const context = { ownerId: 'owner', signal: new AbortController().signal }
    const source = { sessionId: 's', messageId: 'm', throughSeq: 1, cwd: parent, workspaceObservedAt: new Date().toISOString() }
    const root = join(parent, '.powercontext-dsh-handoff')
    mkdirSync(root, { mode: 0o700 })
    const profile = join(root, 'a'.repeat(64))
    mkdirSync(profile, { mode: 0o700 })
    delivery = new LocalDeliveryService({ directory: join(profile, 'deliveries'), now: () => now,
      preflightGate: { validateForPrepare: async (_ctx, input) => ({ source: input.source, preflightId: input.preflightId,
        revision: input.expectedRevision, targetId: 'claude-code-cli', targetVersion: '1', adapterVersion: '1', effectiveCwd: parent,
        scopeId, serverIdentity, connectionRef: 'isolated', connectionGeneration: 1 }) },
      resolveConnection: async () => ({ client }) })
    const ready = await delivery.prepare(context, { source, preflightId: 'p', expectedRevision: 1, requestId: 'r', workInput: {
      schema: 'powercontext.current-work-handoff.v1', trust: 'untrusted_input', objective: 'Inspect the failure',
      state: [{ text: 'An assertion failed.', basis: 'declared', evidence: [] }], disposition: 'continuable',
      next_action: null, omissions: [] } })
    const bundle = await delivery.getForLaunch(context, ready.deliveryId)
    const reader = new LocalReceiptReader({ directory: join(parent, 'receipts'), now: () => now,
      resolveConnection: async () => ({ client, accessMode: 'disabled' }) })
    expect(await reader.read(context, bundle)).toMatchObject({ state: 'awaiting', readState: 'not_found' })
    now += 10 * 86_400_000
    await delivery.prune(context, candidate => reader.confirmedAt(context, candidate))
    expect(await delivery.get(context, ready.deliveryId)).toMatchObject({ expired: false })
    const draft = buildReceiverPrompt(bundle, 'zh-CN')
    expect(draft).not.toContain(bundle.receiptAttempt.sourceId)
    expect(draft).not.toContain(bundle.packageSha256)
    // Simulate rewriting the instruction and retaining the package link in the receiver composer.
    const edited = '请先检查失败日志，不修改代码。\n' + JSON.stringify('~/' + relative(parent, bundle.packagePath))
    await new Promise<void>((resolve, reject) => {
      const process = spawn('uv', ['run', '--no-sync', 'python', '-c', RECEIVE, server.root, server.baseUrl, parent, parent, scopeId],
        { cwd: server.root, signal: AbortSignal.timeout(16000), stdio: ['pipe', 'ignore', 'pipe'] })
      let stderr = ''
      process.stderr.on('data', bytes => { stderr += String(bytes) })
      process.on('error', reject)
      process.on('close', code => code === 0 ? resolve() : reject(new Error(stderr || 'Receiving fixture failed')))
      process.stdin.end(edited)
    })
    expect(await reader.read(context, bundle)).toMatchObject({ state: 'accepted', readState: 'verified', attribution: { kind: 'self_reported', receiver: 'claude-code-cli' } })
    now += 8 * 86_400_000
    await delivery.prune(context, candidate => reader.confirmedAt(context, candidate))
    const expired = await delivery.get(context, ready.deliveryId)
    expect(expired).toMatchObject({ expired: true })
    expect(await reader.saved(context, expired)).toMatchObject({ state: 'accepted', readState: 'stale',
      attribution: { kind: 'self_reported', receiver: 'claude-code-cli' } })
  } finally {
    await delivery?.close()
    await server.stop()
    rmSync(parent, { recursive: true, force: true })
    rmSync(server.home, { recursive: true, force: true })
  }
}, 60_000)
