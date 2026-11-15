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

/** Read-only, optional memory access for companions running in the same trusted DSH host. */
import { createHash } from 'node:crypto'
import { basename, resolve } from 'node:path'
import type { PluginRuntime } from './invoke.ts'
import { boundedRecallQuery, validatePreparedContext } from './prepared-context.ts'
import { ServerResponseError } from './errors.ts'
import { scrubSecrets } from './secrets.ts'

export const COMPANION_MEMORY_SERVICE = 'powercontextCompanionMemory'
export const COMPANION_MEMORY_PROTOCOL = 'powercontext.companion-memory.v1'

export interface CompanionMemoryRequest {
  sessionId: string
  /** Omit to inspect the binding without retrieving memory. */
  query?: string
  maxBytes?: number
  signal?: AbortSignal
}

export type CompanionMemoryResult = {
  status: 'connected' | 'ready' | 'empty'
  /** Opaque connection + workspace + Scope identity; safe for local history partitioning. */
  bindingId: string
  scopeId: string
  workspace: string
  content?: string
} | {
  status: 'unavailable'
  reason: 'session-required' | 'workspace-unavailable' | 'scope-unavailable' | 'connection-unavailable'
}

export interface CompanionMemoryService {
  readonly protocol: typeof COMPANION_MEMORY_PROTOCOL
  prepare(request: CompanionMemoryRequest): Promise<CompanionMemoryResult>
}

interface CompanionHost {
  get(name: string): unknown
  provide(name: string, service: CompanionMemoryService): unknown
  effect(callback: () => () => void): unknown
}

/** Keep the running client's credentials and transport policy inside the host plugin. */
export function registerCompanionMemory(ctx: CompanionHost, runtime: PluginRuntime): void {
  if (typeof ctx.provide !== 'function' || typeof ctx.effect !== 'function') return
  const lifetime = new AbortController()
  const cwdFor = (sessionId: string): string | undefined => {
    const sessions = ctx.get('sessions') as { get?(id: string): { header?: { cwd?: unknown } } | undefined } | undefined
    const cwd = sessions?.get?.(sessionId)?.header?.cwd
    return typeof cwd === 'string' && cwd.trim() ? resolve(cwd) : undefined
  }
  const service: CompanionMemoryService = {
    protocol: COMPANION_MEMORY_PROTOCOL,
    async prepare(request) {
      if (typeof request.sessionId !== 'string' || !request.sessionId.trim() || request.sessionId.length > 256) {
        return { status: 'unavailable', reason: 'session-required' }
      }
      const signal = AbortSignal.any([
        lifetime.signal,
        AbortSignal.timeout(Math.max(1, runtime.config.timeoutMs)),
        ...(request.signal ? [request.signal] : []),
      ])
      const unavailable = { status: 'unavailable', reason: 'connection-unavailable' } as const
      const run = async (): Promise<CompanionMemoryResult> => {
        signal.throwIfAborted()
        const cwd = cwdFor(request.sessionId)
        if (!cwd) return { status: 'unavailable', reason: 'workspace-unavailable' }
        const scopeId = await runtime.resolveScope(cwd, signal, false).catch((error: unknown) => {
          if (error instanceof ServerResponseError && [404, 409].includes(error.statusCode)) return undefined
          throw error
        })
        signal.throwIfAborted()
        if (!scopeId) return { status: 'unavailable', reason: 'scope-unavailable' }
        if (cwdFor(request.sessionId) !== cwd) return { status: 'unavailable', reason: 'workspace-unavailable' }
        const binding = {
          bindingId: createHash('sha256').update(JSON.stringify([
            runtime.config.baseUrl, runtime.config.authorization ?? '', cwd, scopeId,
          ])).digest('hex'),
          scopeId,
          workspace: basename(cwd) || cwd,
        }
        if (request.query === undefined) return { status: 'connected', ...binding }
        if (typeof request.query !== 'string') return unavailable
        const query = boundedRecallQuery(scrubSecrets(request.query, runtime.config.authorization))
        if (!query) return { status: 'empty', ...binding }
        const requested = request.maxBytes ?? runtime.config.maxBytes
        if (!Number.isInteger(requested) || requested < 512 || requested > 32768) return unavailable
        const maxBytes = Math.min(requested, runtime.config.maxBytes)
        const result = await runtime.client.request('prepare_context', {
          scope_id: scopeId, query, max_bytes: maxBytes,
          // A companion reads project memories; it does not inherit unrelated context families.
          assembly: { sections: [{ family: 'memory', limit: 3 }] },
        }, signal)
        signal.throwIfAborted()
        if (cwdFor(request.sessionId) !== cwd) return { status: 'unavailable', reason: 'workspace-unavailable' }
        const prepared = validatePreparedContext(result.kind === 'json' ? result.value : undefined,
          '/v1/context/prepare', maxBytes)
        return { status: prepared.status, ...binding, ...(prepared.content ? { content: prepared.content } : {}) }
      }
      // Bound even a buggy dependency which ignores AbortSignal. Remove listeners after every request.
      let onAbort: () => void = () => {}
      const cancelled = new Promise<CompanionMemoryResult>((resolveCancelled) => {
        onAbort = () => resolveCancelled(unavailable)
        if (signal.aborted) onAbort()
        else signal.addEventListener('abort', onAbort, { once: true })
      })
      try { return await Promise.race([run().catch(() => unavailable), cancelled]) }
      finally { signal.removeEventListener('abort', onAbort) }
    },
  }
  ctx.provide(COMPANION_MEMORY_SERVICE, service)
  ctx.effect(() => () => lifetime.abort())
}
