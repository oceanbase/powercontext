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

import type { Context } from '@deepseek-ai/cordis'
import type { ResolvedConfig } from './config.ts'
import { logSafely, reportFailure } from './diagnostics.ts'
import { MCP_OPERATIONS } from './mcp-operations.generated.ts'
import { protectMcpEndpoint } from './mcp-transport.ts'
import { OPERATIONS } from './operations.generated.ts'
import { loadPeer } from './peers.ts'
import { formatScopeRouting } from './scope.ts'
import { hasSecretContent } from './secrets.ts'

export const POWERCONTEXT_MCP_SERVER_NAME = 'powercontext'

export interface DshMcpConfig {
  transport: 'streamable-http'
  serverName: string
  url: string
  headers: Record<string, string>
  failOnStartupError: boolean
  toolCallTimeoutMs: number
}

type PeerLoader = <T>(specifier: string) => Promise<T>
type DshMcpClient = { apply: (ctx: Context, config: DshMcpConfig) => Promise<void> }
type PreToolDecision = { kind: 'allow' } | { kind: 'deny'; reason: string } | { kind: 'ask'; reason?: string }

export interface McpScopeResolutionRequest {
  sessionId?: string
  cwd?: string
  signal: AbortSignal
}

export type McpScopeResolver = (request: McpScopeResolutionRequest) => Promise<string | undefined>

const MCP_TOOL_PREFIX = `mcp__${POWERCONTEXT_MCP_SERVER_NAME}__`
const MCP_STARTUP_WAIT_MS = 5000
// MCP ToolAnnotations are advisory; the native DSH MCP client does not turn them into approval prompts.
// Keep the mutation boundary here so tools/pre-execute remains the actual host approval gate.
const MUTATING_MCP_OPERATIONS = new Set(Object.entries(MCP_OPERATIONS)
  .filter(([, metadata]) => !metadata.readOnly).map(([operation]) => operation))

type McpToolExecution = {
  name: string
  arguments: unknown
  signal: AbortSignal
  agent?: { session?: { header?: { id?: string; cwd?: string } } }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function rawOperation(name: string): string | undefined {
  if (!name.startsWith(MCP_TOOL_PREFIX)) return undefined
  const operation = name.slice(MCP_TOOL_PREFIX.length)
  return Object.prototype.hasOwnProperty.call(MCP_OPERATIONS, operation) ? operation : undefined
}

function usesScope(operation: string): 'current' | 'selection' | 'none' {
  const metadata = OPERATIONS[operation as keyof typeof OPERATIONS]
  // scopeMode describes implicit Scope resolution; path scope_id still binds an MCP call to the host Scope.
  if (metadata.pathParameters.some(parameter => parameter === 'scope_id')) return 'current'
  return metadata.scopeMode
}

function matchesScope(argumentsValue: unknown, mode: 'current' | 'selection', scopeId: string): boolean {
  if (!isRecord(argumentsValue)) return false
  if (mode === 'current') return argumentsValue.scope_id === scopeId
  const selection = argumentsValue.selection
  if (!isRecord(selection) || selection.mode !== 'exact' || !Array.isArray(selection.scope_ids)) return false
  return selection.scope_ids.length === 1 && selection.scope_ids[0] === scopeId
}

export function registerMcpPolicy(
  ctx: { on(event: string, handler: (...args: never[]) => unknown): unknown },
  resolveScope: McpScopeResolver,
): void {
  ctx.on('tools/pre-execute', (async (
    exec: McpToolExecution,
    next: () => Promise<PreToolDecision>,
  ): Promise<PreToolDecision> => {
    const operation = rawOperation(exec.name)
    if (!operation) return exec.name.startsWith(MCP_TOOL_PREFIX)
      ? { kind: 'deny', reason: 'This PowerContext MCP operation is unavailable in the plugin catalog. No MCP request was sent; install a matching plugin and Server.' }
      : next()

    if (hasSecretContent(operation, exec.arguments)) {
      return { kind: 'deny', reason: 'secret_rejected: PowerContext refuses to send likely secret content. No MCP request was sent.' }
    }

    const mode = usesScope(operation)
    if (mode !== 'none') {
      const cwd = exec.agent?.session?.header?.cwd
      let scopeId: string | undefined
      try {
        scopeId = await resolveScope({
          sessionId: exec.agent?.session?.header?.id,
          cwd,
          signal: exec.signal,
        })
      } catch {
        // Server authorization cannot establish that a model-supplied Scope belongs to this session.
      }
      if (!scopeId) {
        return {
          kind: 'deny',
          reason: 'PowerContext host Scope could not be resolved. No MCP request was sent. Continue ordinary work and use /pc doctor to diagnose Scope resolution before retrying.',
        }
      }
      if (!matchesScope(exec.arguments, mode, scopeId)) {
        return {
          kind: 'deny',
          reason: `${formatScopeRouting(scopeId, cwd)}\nNo MCP request was sent because the call did not use the host Scope.`,
        }
      }
    }

    if (MUTATING_MCP_OPERATIONS.has(operation)) {
      return {
        kind: 'ask',
        reason: `PowerContext MCP tool "${exec.name}" changes durable project context. Approve it only when the user explicitly requested this operation.`,
      }
    }
    return next()
  }) as never)
}

export function mcpEndpoint(baseUrl: string): string {
  // Use the canonical mount path directly instead of relying on the Server's slash redirect.
  return `${baseUrl.replace(/\/+$/, '')}/mcp/`
}

export function mcpConfig(config: Pick<ResolvedConfig, 'baseUrl' | 'authorization'>): DshMcpConfig {
  return {
    transport: 'streamable-http',
    serverName: POWERCONTEXT_MCP_SERVER_NAME,
    url: mcpEndpoint(config.baseUrl),
    headers: config.authorization ? { Authorization: config.authorization } : {},
    // DSH should keep the host usable while the Server is starting or unavailable.
    failOnStartupError: false,
    toolCallTimeoutMs: 60_000,
  }
}

export async function registerMcp(
  ctx: Context,
  config: Pick<ResolvedConfig, 'baseUrl' | 'authorization'>,
  load: PeerLoader = loadPeer,
): Promise<void> {
  const log = (event: Record<string, unknown>) => ctx.logger.warn(JSON.stringify({ component: 'powercontext.dsh', ...event }))
  const initializing = (async () => {
    const client = await load<DshMcpClient>('@deepseek-ai/dsh-mcp-client')
    const nativeConfig = mcpConfig(config)
    nativeConfig.url = await protectMcpEndpoint(ctx, nativeConfig.url)
    await client.apply(ctx, nativeConfig)
  })().catch(error => reportFailure(log, 'mcp_connect', error))
  let startupTimeout: ReturnType<typeof setTimeout> | undefined
  const deadline = new Promise<void>((resolve) => {
    startupTimeout = setTimeout(() => {
      logSafely(log, { event: 'mcp_connect', outcome: 'pending', recovery: '/pc doctor' })
      resolve()
    }, MCP_STARTUP_WAIT_MS)
  })
  try {
    // The native client owns connection cleanup and may publish its tools after host startup.
    await Promise.race([initializing, deadline])
  } finally {
    clearTimeout(startupTimeout)
  }
}
