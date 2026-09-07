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

import type { JsonObject } from './client.ts'
import { requireService } from './dsh-service.ts'
import { invokeOperation, reportDirectFailure, type PluginRuntime, type ToolResult } from './invoke.ts'
import { UNSCOPED_MESSAGE } from './scope.ts'

export interface CommandResult {
  kind: 'success' | 'error'
  text: string
}

function formatResult(result: ToolResult): string {
  return JSON.stringify(result, null, 2)
}

function asResult(result: ToolResult): CommandResult {
  return { kind: result.ok ? 'success' : 'error', text: formatResult(result) }
}

async function call(
  runtime: PluginRuntime,
  cwd: string | undefined,
  operationId: string,
  payload: JsonObject,
  signal?: AbortSignal,
): Promise<CommandResult> {
  try {
    const scopeId = await runtime.resolveScope(cwd, signal)
    if (!scopeId) return asResult({ ok: false, code: 'unscoped', message: UNSCOPED_MESSAGE })
    return asResult(await invokeOperation(runtime.client, operationId, payload, scopeId, signal,
      error => reportDirectFailure(runtime, 'command', error)))
  } catch (error) {
    return asResult(await reportDirectFailure(runtime, 'command', error))
  }
}

async function handleReview(
  tokens: string[],
  runtime: PluginRuntime,
  cwd: string | undefined,
  signal?: AbortSignal,
): Promise<CommandResult> {
  const action = tokens[1]
  if (!action) return call(runtime, cwd, 'list_artifact_candidates', { status: 'pending' }, signal)
  if (action === 'approve') {
    const candidateId = tokens[2]
    const version = Number(tokens[3])
    if (!candidateId || !Number.isInteger(version)) {
      return { kind: 'error', text: 'Usage: /pc review approve <candidate_id> <expected_version>' }
    }
    return call(runtime, cwd, 'approve_artifact_candidate', { candidate_id: candidateId, expected_version: version }, signal)
  }
  if (action === 'reject') {
    const candidateId = tokens[2]
    const version = Number(tokens[3])
    const reason = tokens.slice(4).join(' ')
    if (!candidateId || !Number.isInteger(version) || !reason) {
      return { kind: 'error', text: 'Usage: /pc review reject <candidate_id> <expected_version> <reason>' }
    }
    return call(runtime, cwd, 'reject_artifact_candidate', {
      candidate_id: candidateId, expected_version: version, reason,
    }, signal)
  }
  return { kind: 'error', text: 'Usage: /pc review [approve|reject] ...' }
}

async function handleDoctor(runtime: PluginRuntime, signal?: AbortSignal): Promise<CommandResult> {
  const onFailure = (error: unknown) => reportDirectFailure(runtime, 'command', error)
  const live = await invokeOperation(runtime.client, 'get_liveness', {}, '', signal, onFailure)
  const ready = await invokeOperation(runtime.client, 'get_readiness', {}, '', signal, onFailure)
  return { kind: live.ok && ready.ok ? 'success' : 'error', text: formatResult({ ok: live.ok && ready.ok, data: { live, ready } }) }
}

function statusResult(runtime: PluginRuntime, scopeId?: string, failure?: ToolResult): CommandResult {
  let endpoint = '(invalid URL)'
  try {
    // Display the origin only: credentials, paths, query strings and fragments can contain secrets.
    endpoint = new URL(runtime.config.baseUrl).origin
  } catch { /* Do not echo an invalid configuration value. */ }
  return {
    kind: failure ? 'error' : 'success',
    text: `scope=${scopeId ?? 'unresolved'}\nbaseUrl=${endpoint}\nUse /pc doctor to check Server readiness.`
      + (failure ? `\n${formatResult(failure)}` : ''),
  }
}

export async function handlePcCommand(
  rawInput: string,
  runtime: PluginRuntime,
  cwd?: string,
  signal?: AbortSignal,
): Promise<CommandResult> {
  const tokens = rawInput.trim().split(/\s+/).filter(Boolean)
  const command = tokens[0]
  if (!command) {
    try {
      const scopeId = await runtime.resolveScope(cwd, signal)
      return statusResult(runtime, scopeId,
        scopeId ? undefined : { ok: false, code: 'unscoped', message: UNSCOPED_MESSAGE })
    } catch (error) {
      return statusResult(runtime, undefined, await reportDirectFailure(runtime, 'command', error))
    }
  }
  if (command === 'doctor') return handleDoctor(runtime, signal)
  if (command === 'search') {
    const query = tokens.slice(1).join(' ')
    if (!query) return { kind: 'error', text: 'Usage: /pc search <query>' }
    return call(runtime, cwd, 'search_memory', { query, limit: 8, mode: 'auto' }, signal)
  }
  if (command === 'remember') {
    const text = tokens.slice(1).join(' ')
    if (!text) return { kind: 'error', text: 'Usage: /pc remember <text>' }
    return call(runtime, cwd, 'remember_memory', { kind: 'agent-note', text }, signal)
  }
  if (command === 'flush') return call(runtime, cwd, 'flush_memory', {}, signal)
  if (command === 'review') return handleReview(tokens, runtime, cwd, signal)
  if (command === 'skills') {
    if (tokens[1] === 'scan') return call(runtime, cwd, 'scan_external_skills', {}, signal)
    return { kind: 'error', text: 'Usage: /pc skills scan' }
  }
  if (command === 'stats') return call(runtime, cwd, 'get_stats', {}, signal)
  if (command === 'capabilities') {
    return asResult(await invokeOperation(runtime.client, 'get_capabilities', {}, '', signal,
      error => reportDirectFailure(runtime, 'command', error)))
  }
  return { kind: 'error', text: 'Unknown /pc subcommand. Try doctor, search, remember, flush, review, stats, capabilities, skills scan.' }
}

export function registerCommands(
  ctx: { get: (name: string) => unknown },
  runtime: PluginRuntime,
): void {
  const commands = requireService<{
    register: (definition: {
      name: string
      description: string
      handler: (invocation: { rawInput: string; signal: AbortSignal; agent: { session: { header: { cwd?: string } } } }) => Promise<CommandResult>
    }) => unknown
  }>(ctx, 'commands')
  commands.register({
    name: 'pc',
    description: 'PowerContext status, search, review, and diagnostics',
    handler: async (invocation) => handlePcCommand(
      invocation.rawInput, runtime, invocation.agent.session.header.cwd, invocation.signal,
    ),
  })
}
