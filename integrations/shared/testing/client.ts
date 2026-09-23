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

/* Host tests substitute operation results; shared-worker.spec.ts covers the real protocol. */

type FixtureOptions = {
  baseUrl: string; authorization?: string; allowInsecureHttp?: boolean;
  requestTimeoutMs: number; fetch?: (url: string, init: RequestInit) => Promise<Response>
}
type Operation = { method: string; path: string }

export function createClientDouble<T>(
  Base: new (options: FixtureOptions) => T, errors: any, operations: Record<string, Operation>,
): new (options: FixtureOptions) => T {
  return class extends (Base as any) {
    private fixture: FixtureOptions
    constructor(options: FixtureOptions) { super(options); this.fixture = options }

    async request(id: string, payload: Record<string, unknown> = {}, signal?: AbortSignal,
      options: { readinessResponse?: boolean } | number = {}): Promise<any> {
      const operation = operations[id]
      if (!operation) throw new errors.UnknownOperationError(id)
      const path = operation.path.replace(/\{([^}]+)\}/g, (_, name) => encodeURIComponent(String(payload[name])))
      const budget = AbortSignal.timeout(typeof options === 'number' ? options : this.fixture.requestTimeoutMs)
      signal = signal ? AbortSignal.any([signal, budget]) : budget
      const init: RequestInit = {
        method: operation.method, redirect: 'manual', signal,
        headers: this.fixture.authorization ? { Authorization: this.fixture.authorization } : {},
        body: operation.method === 'GET' ? undefined : JSON.stringify(payload),
      }
      try {
        const response = await (this.fixture.fetch ?? fetch)(this.fixture.baseUrl.replace(/\/$/, '') + path, init)
        const requestId = response.headers.get('X-PowerContext-Request-ID') ?? undefined
        let value: any
        try { value = await response.json() } catch {
          if (response.ok) throw new errors.InvalidResponseError(path, requestId, response.status)
        }
        if (!response.ok && !(id === 'get_readiness' && typeof options === 'object' && options.readinessResponse)) {
          const failure = new errors.ServerResponseError({
            path, statusCode: response.status, requestId,
            code: value?.error?.code, message: value?.error?.message,
          })
          if (response.status >= 500 && operation.method !== 'GET') failure.outcome = 'unknown'
          throw failure
        }
        return { kind: 'json', value, status: response.status, requestId }
      } catch (error) {
        if (error instanceof errors.ClientError) throw error
        throw new errors.UnavailableError(path, signal?.aborted ? signal.reason : error)
      }
    }

    async readOpenApi(signal?: AbortSignal): Promise<any> {
      const response = await (this.fixture.fetch ?? fetch)(this.fixture.baseUrl + '/openapi.json', { method: 'GET', signal })
      if (!response.ok) throw new errors.ServerResponseError({ statusCode: response.status, path: '/openapi.json' })
      return { kind: 'json', value: await response.json(), status: response.status }
    }
  } as new (options: FixtureOptions) => T
}
