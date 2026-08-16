import { createHash } from 'node:crypto'
import type { PowerContextClient } from './client.ts'
import type { ResolvedConfig } from './config.ts'

export const MAX_SOURCE_BYTES = 200_000

const SECRET_MARKERS = ['sk-', 'api_key', 'api-key', 'authorization: bearer', 'github_pat_', 'ghp_', 'xoxb-']
const PRIVATE_KEY_PATTERN = /-----BEGIN [A-Z ]*PRIVATE KEY-----/i

export interface CaptureInput {
  client: PowerContextClient
  config: ResolvedConfig
  scopeId: string
  prompt: string
  cwd: string
  sessionId: string
  turnId: string
  signal?: AbortSignal
}

export function containsSecret(text: string): boolean {
  const lowercase = text.toLowerCase()
  return SECRET_MARKERS.some((marker) => lowercase.includes(marker)) || PRIVATE_KEY_PATTERN.test(text)
}

export function buildSourceId(scopeId: string, sessionId: string, turnId: string, prompt: string): string {
  const identity = [scopeId, sessionId, turnId, prompt].join('\0')
  return `pi-user-prompt:${createHash('sha256').update(identity).digest('hex')}`
}

function sourcePosition(value: unknown): number | undefined {
  if (!value || typeof value !== 'object') return undefined
  const position = (value as { position?: unknown }).position
  if (typeof position !== 'number' || !Number.isInteger(position) || position < 1) return undefined
  return position
}

async function flushThrough(input: CaptureInput, position: number): Promise<void> {
  for (let index = 0; index < input.config.flushMaxCalls; index += 1) {
    const result = await input.client.request('flush_memory', { scope_id: input.scopeId }, input.signal)
    const cursor = result.kind === 'json' && result.value && typeof result.value === 'object'
      ? (result.value as { current_cursor?: unknown }).current_cursor
      : undefined
    if (typeof cursor === 'number' && cursor >= position) return
  }
}

export async function captureUserPrompt(input: CaptureInput): Promise<number | undefined> {
  if (
    !input.config.capturePrompts
    || Buffer.byteLength(input.prompt, 'utf8') > MAX_SOURCE_BYTES
    || containsSecret(input.prompt)
  ) {
    return undefined
  }
  try {
    const result = await input.client.request('capture_content_source', {
      scope_id: input.scopeId,
      source_id: buildSourceId(input.scopeId, input.sessionId, input.turnId, input.prompt),
      content: input.prompt,
      metadata: {
        origin: 'pi',
        event: 'user_prompt_submit',
        cwd: input.cwd,
        session_id: input.sessionId,
        turn_id: input.turnId,
      },
    }, input.signal)
    const position = result.kind === 'json' ? sourcePosition(result.value) : undefined
    if (input.config.flushOnCapture && position !== undefined) await flushThrough(input, position)
    return position
  } catch {
    // Source persistence is auxiliary; it must not delay or break the Pi turn.
    return undefined
  }
}
