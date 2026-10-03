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
import { combineSignals, PowerContextClient } from './client.ts'
import { registerCommands } from './commands.ts'
import { resolveConfig, type PluginConfig } from './config.ts'
import { createDiagnosticEmitter } from './diagnostics.ts'
import { PLUGIN_NAME } from './errors.ts'
import type { PluginRuntime } from './invoke.ts'
import { registerMcp, registerMcpPolicy, type McpScopeResolutionRequest } from './mcp.ts'
import { loadPeer } from './peers.ts'
import { runRecallPreStep, type PromptMessage } from './recall.ts'
import { resolveScopeId } from './scope.ts'
import { registerGuidance, registerSkill } from './skill.ts'
import { RuntimeStatus } from './status.ts'

export const name = PLUGIN_NAME

export const inject = ['tools', 'agents', 'commands', 'skills', 'systemPrompt']

export interface Config extends PluginConfig {}

export const Config = {
  '~standard': {
    version: 1 as const,
    vendor: 'powercontext-dsh',
    validate(value: unknown) {
      try {
        const input = value && typeof value === 'object' ? value as PluginConfig : {}
        resolveConfig(input)
        // Keep original inputs so runtime resolution can identify defaults and overrides.
        return { value: input }
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error)
        return { issues: [{ message }] }
      }
    },
  },
}

type CreateUserMessage = (input: {
  content: Array<{ type: 'text'; text: string }>
  source: {
    kind: 'plugin'; plugin: string; form: 'snapshot'
    sections: Array<{ name: string; text: string }>
  }
}) => unknown

function createRuntime(ctx: Context, config: PluginConfig): PluginRuntime {
  const resolved = resolveConfig(config)
  const client = new PowerContextClient({
    baseUrl: resolved.baseUrl,
    allowInsecureHttp: resolved.allowInsecureHttp,
    authorization: resolved.authorization,
    requestTimeoutMs: resolved.requestTimeoutMs,
  })
  const emitDiagnostic = createDiagnosticEmitter((line) => ctx.logger.warn(line))
  return {
    status: new RuntimeStatus(),
    client,
    config: resolved,
    resolveScope: (cwd, signal) => resolveScopeId(client, cwd, resolved.scopeId, signal),
    log: (event) => {
      const line = JSON.stringify({ component: 'powercontext.dsh', ...event })
      const quiet = event.outcome === 'ready' || event.outcome === 'ok' || event.outcome === 'empty'
      if (quiet) return ctx.logger.debug?.(line)
      return emitDiagnostic({ component: 'powercontext.dsh', ...event })
    },
  }
}

type SessionScopeResolver = (request: McpScopeResolutionRequest) => Promise<string | undefined>

function createSessionScopeResolver(runtime: PluginRuntime): {
  forPreStep: SessionScopeResolver
  forTool: SessionScopeResolver
} {
  const cache = new Map<string, { cwd?: string; scopeId: string }>()
  const keyFor = (sessionId: string | undefined, cwd: string | undefined) => (
    sessionId ? `session:${sessionId}` : `cwd:${cwd ?? ''}`
  )
  const resolve = async (request: McpScopeResolutionRequest, refresh: boolean): Promise<string | undefined> => {
    const key = keyFor(request.sessionId, request.cwd)
    const cached = cache.get(key)
    if (!refresh && cached && cached.cwd === request.cwd) return cached.scopeId
    // A failed refresh must not leave a previous turn's Scope available to MCP calls.
    cache.delete(key)
    const scopeId = await runtime.resolveScope(request.cwd, request.signal)
    if (scopeId) cache.set(key, { cwd: request.cwd, scopeId })
    return scopeId
  }
  return {
    forPreStep: request => resolve(request, true),
    forTool: request => resolve(request, false),
  }
}

function registerRecall(
  ctx: Context,
  runtime: PluginRuntime,
  createUserMessage: CreateUserMessage,
  resolveSessionScope: SessionScopeResolver,
): void {
  ctx.on('agent/pre-step', (async (payload: {
    agent: { session: { header: { id: string; cwd?: string } } }
    messages: PromptMessage[]
    turn: number
    signal: AbortSignal
  }, next: () => Promise<{ kind: string; messages?: unknown[] }>) => {
    const deadline = AbortSignal.timeout(runtime.config.timeoutMs)
    const signal = combineSignals([payload.signal, deadline])
    return runRecallPreStep({
      messages: payload.messages,
      next,
      cwd: payload.agent.session.header.cwd,
      sessionId: payload.agent.session.header.id,
      turnId: String(payload.turn),
      signal,
      client: runtime.client,
      config: runtime.config,
      resolveScope: (cwd, signal) => resolveSessionScope({
        sessionId: payload.agent.session.header.id,
        cwd,
        signal: signal ?? payload.signal,
      }),
      wrapContent: (text) => createUserMessage({
        content: [{ type: 'text', text }],
        source: {
          kind: 'plugin', plugin: PLUGIN_NAME, form: 'snapshot',
          sections: [{ name: 'PowerContext', text }],
        },
      }),
      wrapScope: (text) => createUserMessage({
        content: [{ type: 'text', text }],
        source: {
          kind: 'plugin', plugin: PLUGIN_NAME, form: 'snapshot',
          sections: [{ name: 'PowerContext Scope routing', text }],
        },
      }),
      log: runtime.log,
      status: runtime.status,
    })
  }) as never)
}

export async function apply(ctx: Context, config: Config): Promise<void> {
  const llmMod = await loadPeer<{ createUserMessage: CreateUserMessage }>('@deepseek-ai/dsh-llm')
  const runtime = createRuntime(ctx, config)
  const sessionScope = createSessionScopeResolver(runtime)
  registerMcpPolicy(ctx, sessionScope.forTool)
  registerGuidance(ctx)
  registerRecall(ctx, runtime, llmMod.createUserMessage, sessionScope.forPreStep)
  registerCommands(ctx, runtime)
  registerSkill(ctx)
  await registerMcp(ctx, runtime.config)
}
