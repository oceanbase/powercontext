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

import type { JsonObject, PowerContextClient } from './client.ts'
import type { ResolvedConfig } from './config.ts'
import { invokeOperation, type ToolResult } from './invoke.ts'
import type { OperationId } from './operations.generated.ts'

export const PC_COMMAND_USAGE = 'doctor | search <query> | remember <text> | flush | review | stats | capabilities | skills scan'

export interface PcCommandRuntime {
  client: PowerContextClient
  config: ResolvedConfig
}

export interface PcCommandResult {
  kind: 'success' | 'error'
  text: string
}

function formatResult(result: ToolResult): string {
  return JSON.stringify(result, null, 2)
}

function asResult(result: ToolResult): PcCommandResult {
  return { kind: result.ok ? 'success' : 'error', text: formatResult(result) }
}

async function call(
  runtime: PcCommandRuntime,
  scopeId: string,
  operationId: OperationId,
  payload: JsonObject,
  signal?: AbortSignal,
): Promise<PcCommandResult> {
  return asResult(await invokeOperation(runtime.client, operationId, payload, scopeId, signal))
}

async function handleReview(
  tokens: string[],
  runtime: PcCommandRuntime,
  scopeId: string,
  signal?: AbortSignal,
): Promise<PcCommandResult> {
  const action = tokens[1]
  if (!action) return call(runtime, scopeId, 'list_artifact_candidates', { status: 'pending' }, signal)
  if (action === 'approve') {
    const candidateId = tokens[2]
    const version = Number(tokens[3])
    if (!candidateId || !Number.isInteger(version)) {
      return { kind: 'error', text: 'Usage: /pc review approve <candidate_id> <expected_version>' }
    }
    return call(runtime, scopeId, 'approve_artifact_candidate', {
      candidate_id: candidateId,
      expected_version: version,
    }, signal)
  }
  if (action === 'reject') {
    const candidateId = tokens[2]
    const version = Number(tokens[3])
    const reason = tokens.slice(4).join(' ')
    if (!candidateId || !Number.isInteger(version) || !reason) {
      return { kind: 'error', text: 'Usage: /pc review reject <candidate_id> <expected_version> <reason>' }
    }
    return call(runtime, scopeId, 'reject_artifact_candidate', {
      candidate_id: candidateId,
      expected_version: version,
      reason,
    }, signal)
  }
  return { kind: 'error', text: 'Usage: /pc review [approve|reject] ...' }
}

async function handleDoctor(
  runtime: PcCommandRuntime,
  scopeId: string,
  signal?: AbortSignal,
): Promise<PcCommandResult> {
  const live = await invokeOperation(runtime.client, 'get_liveness', {}, scopeId, signal)
  const ready = await invokeOperation(runtime.client, 'get_readiness', {}, scopeId, signal)
  const ok = live.ok && ready.ok
  return { kind: ok ? 'success' : 'error', text: formatResult({ ok, data: { live, ready } }) }
}

export async function handlePcCommand(
  rawInput: string,
  runtime: PcCommandRuntime,
  scopeId: string,
  signal?: AbortSignal,
): Promise<PcCommandResult> {
  const tokens = rawInput.trim().split(/\s+/).filter(Boolean)
  const command = tokens[0]
  if (!command) {
    return {
      kind: 'success',
      text: `scope=${scopeId}\nbaseUrl=${runtime.config.baseUrl}\nUse /pc doctor to check Server readiness.`,
    }
  }
  if (command === 'doctor') return handleDoctor(runtime, scopeId, signal)
  if (command === 'search') {
    const query = tokens.slice(1).join(' ')
    if (!query) return { kind: 'error', text: 'Usage: /pc search <query>' }
    return call(runtime, scopeId, 'search_memory', { query, limit: 8, mode: 'auto' }, signal)
  }
  if (command === 'remember') {
    const text = tokens.slice(1).join(' ')
    if (!text) return { kind: 'error', text: 'Usage: /pc remember <text>' }
    return call(runtime, scopeId, 'remember_memory', { kind: 'agent-note', text }, signal)
  }
  if (command === 'flush') return call(runtime, scopeId, 'flush_memory', {}, signal)
  if (command === 'review') return handleReview(tokens, runtime, scopeId, signal)
  if (command === 'stats') return call(runtime, scopeId, 'get_stats', {}, signal)
  if (command === 'capabilities') return call(runtime, scopeId, 'get_capabilities', {}, signal)
  if (command === 'skills') {
    if (tokens[1] === 'scan') return call(runtime, scopeId, 'scan_external_skills', {}, signal)
    return { kind: 'error', text: 'Usage: /pc skills scan' }
  }
  return { kind: 'error', text: `Unknown /pc subcommand. Try ${PC_COMMAND_USAGE}.` }
}
