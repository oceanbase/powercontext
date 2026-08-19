import type { JsonObject } from './client.ts'
import { invokeOperation, type PluginRuntime, type ToolResult } from './invoke.ts'
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
  scopeId: string,
  operationId: string,
  payload: JsonObject,
  signal?: AbortSignal,
): Promise<CommandResult> {
  return asResult(await invokeOperation(runtime.client, operationId, payload, scopeId, signal))
}

async function handleReview(
  tokens: string[],
  runtime: PluginRuntime,
  scopeId: string,
  signal?: AbortSignal,
): Promise<CommandResult> {
  const action = tokens[1]
  if (!action) return call(runtime, scopeId, 'list_artifact_candidates', { status: 'pending' }, signal)
  if (action === 'approve') {
    const candidateId = tokens[2]
    const version = Number(tokens[3])
    if (!candidateId || !Number.isInteger(version)) {
      return { kind: 'error', text: 'Usage: /pc review approve <candidate_id> <expected_version>' }
    }
    return call(runtime, scopeId, 'approve_artifact_candidate', { candidate_id: candidateId, expected_version: version }, signal)
  }
  if (action === 'reject') {
    const candidateId = tokens[2]
    const version = Number(tokens[3])
    const reason = tokens.slice(4).join(' ')
    if (!candidateId || !Number.isInteger(version) || !reason) {
      return { kind: 'error', text: 'Usage: /pc review reject <candidate_id> <expected_version> <reason>' }
    }
    return call(runtime, scopeId, 'reject_artifact_candidate', {
      candidate_id: candidateId, expected_version: version, reason,
    }, signal)
  }
  return { kind: 'error', text: 'Usage: /pc review [approve|reject] ...' }
}

async function handleDoctor(runtime: PluginRuntime, signal?: AbortSignal): Promise<CommandResult> {
  const live = await invokeOperation(runtime.client, 'get_liveness', {}, runtime.config.scopeId ?? 'local:unknown', signal)
  const ready = await invokeOperation(runtime.client, 'get_readiness', {}, runtime.config.scopeId ?? 'local:unknown', signal)
  return { kind: live.ok && ready.ok ? 'success' : 'error', text: formatResult({ ok: live.ok && ready.ok, data: { live, ready } }) }
}

export async function handlePcCommand(
  rawInput: string,
  runtime: PluginRuntime,
  scopeId: string,
  signal?: AbortSignal,
): Promise<CommandResult> {
  const tokens = rawInput.trim().split(/\s+/).filter(Boolean)
  const command = tokens[0]
  if (!command) {
    return {
      kind: 'success',
      text: `scope=${scopeId}\nbaseUrl=${runtime.config.baseUrl}\nUse /pc doctor to check Server readiness.`,
    }
  }
  if (command === 'doctor') return handleDoctor(runtime, signal)
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
  if (command === 'skills') {
    if (tokens[1] === 'scan') return call(runtime, scopeId, 'scan_external_skills', {}, signal)
    return { kind: 'error', text: 'Usage: /pc skills scan' }
  }
  if (command === 'stats') return call(runtime, scopeId, 'get_stats', {}, signal)
  if (command === 'capabilities') return call(runtime, scopeId, 'get_capabilities', {}, signal)
  return { kind: 'error', text: 'Unknown /pc subcommand. Try doctor, search, remember, flush, review, stats, capabilities, skills scan.' }
}

export function registerCommands(
  ctx: { get: (name: string) => unknown },
  runtime: PluginRuntime,
): void {
  const commands = ctx.get('commands') as {
    register: (definition: {
      name: string
      description: string
      handler: (invocation: { rawInput: string; signal: AbortSignal; agent: { session: { header: { cwd?: string } } } }) => Promise<CommandResult>
    }) => unknown
  } | undefined
  if (!commands) return
  commands.register({
    name: 'pc',
    description: 'PowerContext status, search, review, and diagnostics',
    handler: async (invocation) => {
      const scopeId = await runtime.resolveScope(invocation.agent.session.header.cwd)
      if (!scopeId) return { kind: 'error', text: UNSCOPED_MESSAGE }
      return handlePcCommand(invocation.rawInput, runtime, scopeId, invocation.signal)
    },
  })
}
