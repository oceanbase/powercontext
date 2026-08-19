import { createHash } from 'node:crypto'
import type { PowerContextClient } from './client.ts'
import type { ResolvedConfig } from './config.ts'
import { MAX_SOURCE_LENGTH } from './errors.ts'
import { containsSecret } from './secrets.ts'

export interface CaptureInput {
  client: PowerContextClient
  config: ResolvedConfig
  scopeId: string
  prompt: string
  cwd?: string
  sessionId: string
  turnId: string
  signal?: AbortSignal
  log: (event: Record<string, unknown>) => void
}

export function buildSourceId(scopeId: string, sessionId: string, turnId: string, prompt: string): string {
  const identity = [scopeId, sessionId, turnId, prompt].join('\0')
  return `dsh-user-prompt:${createHash('sha256').update(identity).digest('hex')}`
}

async function flushThrough(
  client: PowerContextClient,
  config: ResolvedConfig,
  scopeId: string,
  position: number,
  signal?: AbortSignal,
): Promise<void> {
  for (let i = 0; i < config.flushMaxCalls; i += 1) {
    const result = await client.request('flush_memory', { scope_id: scopeId }, signal)
    const cursor = result.kind === 'json' && result.value && typeof result.value === 'object'
      ? (result.value as { current_cursor?: unknown }).current_cursor
      : undefined
    if (typeof cursor === 'number' && cursor >= position) return
  }
}

function sourcePosition(value: unknown): number | undefined {
  if (!value || typeof value !== 'object') return undefined
  const position = (value as { position?: unknown }).position
  if (typeof position !== 'number' || !Number.isInteger(position) || position < 1) return undefined
  return position
}

export async function captureUserPrompt(input: CaptureInput): Promise<void> {
  if (!input.config.capturePrompts) return
  if (input.prompt.length > MAX_SOURCE_LENGTH || containsSecret(input.prompt)) {
    input.log({ event: 'capture_content_source', outcome: 'skipped' })
    return
  }
  try {
    const result = await input.client.request('capture_content_source', {
      scope_id: input.scopeId,
      source_id: buildSourceId(input.scopeId, input.sessionId, input.turnId, input.prompt),
      content: input.prompt,
      metadata: {
        origin: 'dsh',
        event: 'user_prompt_submit',
        ...input.cwd ? { cwd: input.cwd } : {},
        session_id: input.sessionId,
        turn_id: input.turnId,
      },
    }, input.signal)
    const position = result.kind === 'json' ? sourcePosition(result.value) : undefined
    if (input.config.flushOnCapture && position !== undefined) {
      await flushThrough(input.client, input.config, input.scopeId, position, input.signal)
    }
    input.log({ event: 'capture_content_source', outcome: 'ok', status: result.status })
  } catch {
    input.log({ event: 'capture_content_source', outcome: 'failed' })
  }
}
