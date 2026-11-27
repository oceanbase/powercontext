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
import { createDiagnosticEmitter, diagnosticWriter, failureEvent } from '../src/diagnostics.ts'
import { recallBeforeAgentStart, type PluginRuntime } from '../src/recall.ts'
import { resolveScopeId } from '../src/scope.ts'
import { registerTools } from '../src/tools.ts'
import { GUIDANCE } from '../src/guidance.ts'

/** The gateway prepares once per user message and captures turns per conversation named by this header. */
const CONVERSATION_HEADER = 'X-PowerContext-Conversation-ID'
// The gateway accepts 1-256 visible ASCII characters and ignores anything else.
const CONVERSATION_ID = /^[\x21-\x7e]{1,256}$/
/** Marks a model request that is not a conversation turn, so the gateway neither prepares, injects nor captures it. */
const TURN_HEADER = 'X-PowerContext-Turn'

function createRuntime(): PluginRuntime {
  const config = resolveConfig()
  const client = new PowerContextClient({
    baseUrl: config.baseUrl,
    allowInsecureHttp: config.allowInsecureHttp,
    authorization: config.authorization,
    requestTimeoutMs: config.requestTimeoutMs,
  })
  const emitDiagnostic = createDiagnosticEmitter(diagnosticWriter(config.diagnostics))
  const diagnostic = (event: string, error: unknown, extra: Record<string, unknown> = {}) => {
    const failure = failureEvent(event, error)
    if (failure) {
      emitDiagnostic({
        component: 'powercontext.pi',
        ...failure,
        ...(extra.lifecycle_event ? { failure_class: failure.outcome } : {}),
        ...extra,
      })
    }
  }
  const flusher = createPendingSourceFlusher(client, config, diagnostic)
  return {
    client,
    config,
    resolveScope(cwd) {
      return resolveScopeId(client, cwd, config.scopeId)
    },
    recordCapture: (scopeId, position) => flusher.record(scopeId, position),
    flushPending: (lifecycleEvent, signal) => flusher.flush(lifecycleEvent, signal),
    diagnostic,
    observe: emitDiagnostic,
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

  // Compaction and branch summaries are standalone model requests inside the Pi session. They are not turns of the
  // gateway conversation, so they carry no conversation header and are marked internal. Pi emits no end event for a
  // cancelled or failed summary, so the next agent turn also ends the summary window.
  let summarizing = false

  pi.on('before_agent_start', async (event, ctx) => {
    summarizing = false
    if (!runtime) return undefined
    const systemPrompt = `${event.systemPrompt}\n\n${GUIDANCE}`
    const recalled = await recallBeforeAgentStart({
      prompt: event.prompt,
      systemPrompt,
      cwd: ctx.cwd,
      sessionId: ctx.sessionManager.getSessionId(),
      branch: ctx.sessionManager.getBranch(),
      signal: ctx.signal,
      runtime,
    })
    return recalled ?? { systemPrompt }
  })

  // Prompt recall is off when the gateway injects context. Only then does the Pi session id name the gateway
  // conversation, so a provider that Pi calls directly does not receive it as a header.
  if (runtime && !runtime.config.promptRecall) {
    pi.on('before_provider_headers', (event, ctx) => {
      if (summarizing) {
        event.headers[TURN_HEADER] = 'internal'
        return
      }
      const conversation = ctx.sessionManager.getSessionId()
      if (CONVERSATION_ID.test(conversation)) event.headers[CONVERSATION_HEADER] = conversation
    })
    pi.on('turn_start', () => {
      summarizing = false
    })
    pi.on('session_compact', () => {
      summarizing = false
    })
    pi.on('session_before_tree', () => {
      summarizing = true
    })
    pi.on('session_tree', () => {
      summarizing = false
    })
  }

  pi.on('agent_end', (_event, ctx) => {
    void runtime?.flushPending?.('agent_end', ctx.signal)
  })

  pi.on('session_before_compact', async (event, _ctx) => {
    summarizing = true
    await runtime?.flushPending?.('session_before_compact', event.signal)
  })

  pi.on('session_before_switch', async (_event, ctx) => {
    await runtime?.flushPending?.('session_before_switch', ctx.signal)
  })

  pi.on('session_shutdown', async (_event, ctx) => {
    await runtime?.flushPending?.('session_shutdown', ctx.signal)
  })
}
