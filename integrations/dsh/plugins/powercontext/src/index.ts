import type { Context } from '@deepseek-ai/cordis'
import { combineSignals, PowerContextClient } from './client.ts'
import { registerCommands } from './commands.ts'
import { resolveConfig, type PluginConfig } from './config.ts'
import { PLUGIN_NAME } from './errors.ts'
import type { PluginRuntime } from './invoke.ts'
import { loadPeer } from './peers.ts'
import { runRecallPreStep, type PromptMessage } from './recall.ts'
import { deriveScopeId } from './scope.ts'
import { registerGuidance, registerSkill } from './skill.ts'
import { registerTools } from './tools.ts'

export const name = PLUGIN_NAME

export const inject = ['tools', 'agents']

export interface Config extends PluginConfig {}

export const Config = {
  '~standard': {
    version: 1 as const,
    vendor: 'powercontext-dsh',
    validate(value: unknown) {
      try {
        const input = value && typeof value === 'object' ? value as PluginConfig : {}
        return { value: resolveConfig(input) }
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error)
        return { issues: [{ message }] }
      }
    },
  },
}

type CreateUserMessage = (input: {
  content: Array<{ type: 'text'; text: string }>
  source: { kind: 'plugin'; plugin: string }
}) => unknown

type DefineTool = (definition: Record<string, unknown>) => unknown

function createRuntime(ctx: Context, config: PluginConfig): PluginRuntime {
  const resolved = resolveConfig(config)
  const client = new PowerContextClient({
    baseUrl: resolved.baseUrl,
    authorization: resolved.authorization,
    requestTimeoutMs: resolved.requestTimeoutMs,
  })
  return {
    client,
    config: resolved,
    resolveScope: (cwd) => deriveScopeId(cwd, { configuredScopeId: resolved.scopeId }),
    log: (event) => {
      const line = JSON.stringify({ component: 'powercontext.dsh', ...event })
      const quiet = event.outcome === 'ready' || event.outcome === 'ok' || event.outcome === 'empty'
      if (quiet) ctx.logger.debug?.(line)
      else ctx.logger.warn(line)
    },
  }
}

function registerRecall(ctx: Context, runtime: PluginRuntime, createUserMessage: CreateUserMessage): void {
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
      resolveScope: runtime.resolveScope,
      wrapContent: (text) => createUserMessage({
        content: [{ type: 'text', text }],
        source: { kind: 'plugin', plugin: PLUGIN_NAME },
      }),
      log: runtime.log,
    })
  }) as never)
}

export async function apply(ctx: Context, config: Config): Promise<void> {
  const toolsMod = await loadPeer<{ defineTool: DefineTool }>('@deepseek-ai/dsh-tools')
  const llmMod = await loadPeer<{ createUserMessage: CreateUserMessage }>('@deepseek-ai/dsh-llm')
  const runtime = createRuntime(ctx, config)
  registerGuidance(ctx)
  registerTools(ctx, runtime, toolsMod.defineTool)
  registerRecall(ctx, runtime, llmMod.createUserMessage)
  registerCommands(ctx, runtime)
  registerSkill(ctx)
}
