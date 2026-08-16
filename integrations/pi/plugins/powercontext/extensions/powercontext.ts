import type { ExtensionAPI } from '@earendil-works/pi-coding-agent'
import { PowerContextClient } from '../src/client.ts'
import { registerCommands } from '../src/commands.ts'
import { resolveConfig } from '../src/config.ts'
import { createPendingSourceFlusher } from '../src/flush.ts'
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
  const flusher = createPendingSourceFlusher(client, config)
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
