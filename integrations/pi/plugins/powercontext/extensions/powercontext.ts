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

import type { ExtensionAPI } from '@earendil-works/pi-coding-agent'
import { PowerContextClient } from '../src/client.ts'
import { registerCommands } from '../src/commands.ts'
import { resolveConfig } from '../src/config.ts'
import { createPendingSourceFlusher } from '../src/flush.ts'
import { createDiagnosticEmitter, failureEvent } from '../src/diagnostics.ts'
import { recallBeforeAgentStart, type PluginRuntime } from '../src/recall.ts'
import { deriveScopeId } from '../src/scope.ts'
import { registerTools } from '../src/tools.ts'

function createRuntime(): PluginRuntime {
  const config = resolveConfig()
  const client = new PowerContextClient({
    baseUrl: config.baseUrl,
    authorization: config.authorization,
    requestTimeoutMs: config.requestTimeoutMs,
  })
  const emitDiagnostic = createDiagnosticEmitter((line) => console.warn(line))
  const diagnostic = (event: string, error: unknown) => {
    const failure = failureEvent(event, error)
    if (failure) emitDiagnostic({ component: 'powercontext.pi', ...failure })
  }
  const flusher = createPendingSourceFlusher(client, config, diagnostic)
  const scopes = new Map<string, Promise<string>>()
  return {
    client,
    config,
    resolveScope(cwd) {
      const existing = scopes.get(cwd)
      if (existing) return existing
      const derived = deriveScopeId(cwd, { configuredScopeId: config.scopeId })
      scopes.set(cwd, derived)
      void derived.catch(() => {
        if (scopes.get(cwd) === derived) scopes.delete(cwd)
      })
      return derived
    },
    recordCapture: (scopeId, position) => flusher.record(scopeId, position),
    flushPending: (signal) => flusher.flush(signal),
    diagnostic,
  }
}

export default function powercontextPi(pi: ExtensionAPI): void {
  let runtime: PluginRuntime | undefined
  try {
    runtime = createRuntime()
  } catch {
    // Invalid local configuration must not prevent Pi from starting.
  }

  if (runtime) {
    registerTools(pi, runtime)
    registerCommands(pi, runtime)
  }

  pi.on('before_agent_start', async (event, ctx) => {
    if (!runtime) return undefined
    return recallBeforeAgentStart({
      prompt: event.prompt,
      systemPrompt: event.systemPrompt,
      cwd: ctx.cwd,
      sessionId: ctx.sessionManager.getSessionId(),
      branch: ctx.sessionManager.getBranch(),
      signal: ctx.signal,
      runtime,
    })
  })

  pi.on('agent_end', (_event, ctx) => {
    void runtime?.flushPending?.(ctx.signal)
  })

  pi.on('session_before_compact', async (event, _ctx) => {
    await runtime?.flushPending?.(event.signal)
  })

  pi.on('session_before_switch', async (_event, ctx) => {
    await runtime?.flushPending?.(ctx.signal)
  })

  pi.on('session_shutdown', async (_event, ctx) => {
    await runtime?.flushPending?.(ctx.signal)
  })
}
