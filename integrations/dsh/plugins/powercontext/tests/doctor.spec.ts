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

import { describe, expect, it } from 'vitest'
import { PowerContextClient, type FetchFn } from '../src/client.ts'
import { handlePcCommand } from '../src/commands.ts'
import { resolveConfig, type PluginConfig } from '../src/config.ts'
import { OPERATIONS } from '../src/operations.generated.ts'
import { resolveScopeId } from '../src/scope.ts'

const PRIVATE = 'private-response-marker'
const EMPTY = { schema: 'powercontext.prepared-context.v1', status: 'empty', content: null, content_bytes: 0 }
const CAPABILITIES = {
  source_types: ['content'], artifact_families: ['memory'], memory_extraction: false,
  handoff_generation: false, search_modes: ['auto', 'fts'], context_versions: [EMPTY.schema],
}
function contract() {
  const paths: Record<string, Record<string, unknown>> = {}
  for (const [operationId, spec] of Object.entries(OPERATIONS)) {
    (paths[spec.path] ??= {})[spec.method.toLowerCase()] = { operationId }
  }
  return { openapi: '3.1.0', paths }
}
function healthy(path: string) {
  if (path === '/health/live') return Response.json({ status: 'ok' })
  if (path === '/health/ready') return Response.json({ status: 'ready', checks: { runtime: 'ready', database: 'ready' } })
  if (path === '/openapi.json') return Response.json(contract())
  if (path === '/v1/capabilities') return Response.json(CAPABILITIES)
  if (path === '/v1/scope-bindings/resolve') return Response.json({ scope_id: 'scp_fixture' })
  if (path === '/v1/context/prepare') return Response.json(EMPTY)
  throw new Error('Doctor attempted an unexpected operation: ' + path)
}
function fixture(override?: FetchFn, config: PluginConfig = {}, env: NodeJS.ProcessEnv = {}) {
  const calls: Array<{ path: string; init: RequestInit }> = []
  const resolved = resolveConfig(config, env)
  const client = new PowerContextClient({ ...resolved, fetch: async (url, init) => {
    const path = new URL(url).pathname
    calls.push({ path, init })
    return override ? override(url, init) : healthy(path)
  } })
  const runtime = {
    config: resolved, client,
    resolveScope: (cwd?: string, signal?: AbortSignal) => resolveScopeId(client, cwd, resolved.scopeId, signal),
    log: () => {},
  }
  return {
    calls, runtime,
    async doctor(signal?: AbortSignal) {
      const result = await handlePcCommand('doctor', runtime, '/fixture/workspace', signal)
      return { kind: result.kind, ...JSON.parse(result.text) }
    },
  }
}

describe('read-only DSH Doctor', () => {
  it('separates model-disabled capabilities and empty prepare from failed infrastructure', async () => {
    const h = fixture()
    const result = await h.doctor()
    expect(result).toMatchObject({
      ok: true, kind: 'success',
      checks: {
        liveness: { state: 'ok', code: 'live' }, readiness: { state: 'ok', code: 'ready' },
        routes: { state: 'ok', code: 'routes_declared' }, scope: { state: 'ok', code: 'scope_resolved' },
        capabilities: { state: 'ok', code: 'extraction_disabled' },
        prepare: { state: 'ok', code: 'empty' },
      },
    })
    expect(result.coverage).toContain('not executed')
    expect(h.calls.every(({ path }) => [
      '/health/live', '/health/ready', '/openapi.json', '/v1/capabilities',
      '/v1/scope-bindings/resolve', '/v1/context/prepare',
    ].includes(path))).toBe(true)
    const payload = JSON.parse(String(h.calls.find(c => c.path.endsWith('/prepare'))!.init.body))
    expect(payload.scope_id).toBe('scp_fixture')
    expect(payload.query).not.toContain('/fixture/workspace')
  })

  it.each([
    [401, 'unauthorized', 'authentication_failed'],
    [403, 'forbidden', 'authorization_failed'],
    [404, 'scope_not_found', 'scope_not_found'],
    [404, undefined, 'required_route_missing'],
    [404, PRIVATE, 'unclassified_not_found'],
  ])('identifies Scope resolution HTTP %s / %s without hiding health', async (status, code, expected) => {
    const h = fixture(async url => new URL(url).pathname.endsWith('/resolve')
      ? Response.json({ error: { code, message: PRIVATE } }, { status: status as number, headers: { 'X-PowerContext-Request-ID': 'req-scope' } })
      : healthy(new URL(url).pathname))
    const result = await h.doctor()
    expect(result).toMatchObject({
      ok: false, kind: 'error', checks: {
        liveness: { state: 'ok' }, readiness: { state: 'ok' },
        scope: { state: 'failed', code: expected, operation: 'resolve_scope_binding', http_status: status, request_id: 'req-scope' },
        prepare: { state: 'skipped', code: 'scope_unavailable' },
      },
    })
    expect(result.checks.scope.recovery.length).toBeGreaterThan(0)
    expect(JSON.stringify(result)).not.toContain(PRIVATE)
    expect(h.calls.some(c => c.path.endsWith('/prepare'))).toBe(false)
  })

  it('locates a missing prepare route even when health and the published contract pass', async () => {
    const h = fixture(async url => new URL(url).pathname.endsWith('/prepare')
      ? Response.json({ detail: PRIVATE }, { status: 404 }) : healthy(new URL(url).pathname))
    expect(await h.doctor()).toMatchObject({
      ok: false, checks: { routes: { state: 'ok' }, prepare: { code: 'required_route_missing', operation: 'prepare_context' } },
    })
  })

  it('identifies a missing declared write route without executing it', async () => {
    const document = contract()
    delete document.paths['/v1/sources/content']
    const h = fixture(async url => new URL(url).pathname === '/openapi.json'
      ? Response.json(document) : healthy(new URL(url).pathname))
    const result = await h.doctor()
    expect(result).toMatchObject({ ok: false, checks: { routes: { code: 'required_route_undeclared' } } })
    expect(result.checks.routes.operations).toContain('capture_content_source')
    expect(h.calls.some(c => c.path === '/v1/sources/content')).toBe(false)
  })

  it('keeps missing discovery evidence unchecked, rather than claiming compatibility', async () => {
    const h = fixture(async url => new URL(url).pathname === '/openapi.json'
      ? Response.json({ detail: PRIVATE }, { status: 404 }) : healthy(new URL(url).pathname))
    expect(await h.doctor()).toMatchObject({
      ok: false, checks: { routes: { state: 'skipped', code: 'contract_unavailable' }, prepare: { state: 'ok' } },
    })
  })

  it.each([
    [503, 'not_ready', 'unavailable', 'failed'],
    [200, 'degraded', 'timeout', 'degraded'],
  ])('preserves readiness %s dependency failures', async (status, readiness, dependency, state) => {
    const h = fixture(async url => new URL(url).pathname === '/health/ready'
      ? Response.json({ status: readiness, checks: { runtime: 'ready', database: 'ready', 'inference.generation': dependency,
        [PRIVATE]: PRIVATE } }, { status: status as number }) : healthy(new URL(url).pathname))
    const result = await h.doctor()
    expect(result).toMatchObject({ ok: false, checks: {
      liveness: { state: 'ok' }, readiness: { state, code: readiness, dependencies: { 'inference.generation': dependency } },
    } })
    expect(result.checks.readiness.recovery).toContain('POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL')
    expect(JSON.stringify(result)).not.toContain(PRIVATE)
  })

  it.each([{ service: PRIVATE }, { status: 'not_ready', checks: {} }])('rejects HTTP 200 with invalid health semantics', async body => {
    const h = fixture(async url => new URL(url).pathname.startsWith('/health/')
      ? Response.json(body) : healthy(new URL(url).pathname))
    expect(await h.doctor()).toMatchObject({ ok: false, checks: {
      liveness: { code: 'invalid_response' }, readiness: { code: 'invalid_response' },
    } })
  })

  it('uses the effective environment configuration without revealing credentials or path prefixes', async () => {
    const h = fixture(async (url, init) => {
      expect(new URL(url).origin).toBe('http://127.0.0.1:9009')
      expect(new Headers(init.headers).get('Authorization')).toBe('Bearer ' + PRIVATE)
      return healthy(new URL(url).pathname.replace('/' + PRIVATE, ''))
    }, { baseUrl: 'http://127.0.0.1:8000' }, {
      POWERCONTEXT_DSH_BASE_URL: 'http://127.0.0.1:9009/' + PRIVATE,
      POWERCONTEXT_DSH_AUTHORIZATION: 'Bearer ' + PRIVATE,
    })
    const result = await h.doctor()
    expect(result.configuration).toMatchObject({
      observation: 'running_plugin',
      endpoint: { origin: 'http://127.0.0.1:9009', source: 'environment', path_prefix: true },
      authorization: { configured: true, source: 'environment' },
    })
    expect(JSON.stringify(result)).not.toContain(PRIVATE)
  })

  it('rejects malformed endpoint configuration without making requests', async () => {
    const h = fixture()
    h.runtime.config.baseUrl = 'http://user:' + PRIVATE + '@localhost/?token=' + PRIVATE
    const result = await h.doctor()
    expect(result).toMatchObject({ ok: false, checks: { configuration: { code: 'invalid_endpoint' } } })
    expect(JSON.stringify(result)).not.toContain(PRIVATE)
    expect(h.calls).toEqual([])
  })

  it.each([
    [() => new TypeError(PRIVATE), 'connection_failed'],
    [() => new DOMException(PRIVATE, 'TimeoutError'), 'request_timeout'],
    [() => new TypeError(PRIVATE, { cause: { code: 'ECONNREFUSED' } }), 'connection_refused'],
    [() => new TypeError(PRIVATE, { cause: { code: 'ENOTFOUND' } }), 'dns_lookup_failed'],
    [() => new TypeError(PRIVATE, { cause: { code: 'CERT_HAS_EXPIRED' } }), 'tls_verification_failed'],
  ])('identifies transport failures without returning their raw messages', async (error, code) => {
    const h = fixture(async () => { throw error() })
    const result = await h.doctor()
    expect(result).toMatchObject({ ok: false, checks: { liveness: { code, operation: 'get_liveness' } } })
    expect(JSON.stringify(result)).not.toContain(PRIVATE)
  })

  it('honors cancellation without starting later requests', async () => {
    const h = fixture()
    const controller = new AbortController()
    controller.abort()
    const result = await h.doctor(controller.signal)
    expect(result).toMatchObject({ ok: false, checks: { liveness: { code: 'cancelled' } } })
    expect(h.calls).toEqual([])
  })

  it('stops after cancellation during an active request, including a non-Error abort reason', async () => {
    const controller = new AbortController()
    const h = fixture(async () => {
      controller.abort(PRIVATE)
      return Response.json({ status: 'ok' })
    })
    const result = await h.doctor(controller.signal)
    expect(result).toMatchObject({ ok: false, checks: { liveness: { code: 'cancelled' } } })
    expect(h.calls.map(c => c.path)).toEqual(['/health/live'])
    expect(JSON.stringify(result)).not.toContain(PRIVATE)
  })

  it.each([
    ['/health/live', 'liveness', '<html>private-response-marker</html>', 'invalid_json'],
    ['/v1/context/prepare', 'prepare', JSON.stringify({ ...EMPTY, content_bytes: 100 }), 'prepared_empty'],
  ])('retains request identity when %s returns malformed data', async (path, layer, body, issue) => {
    const h = fixture(async url => new URL(url).pathname === path
      ? new Response(body, { status: 200, headers: { 'X-PowerContext-Request-ID': 'req-malformed' } })
      : healthy(new URL(url).pathname))
    const result = await h.doctor()
    expect(result.checks[layer]).toMatchObject({ code: 'invalid_response', protocol_issue: issue, http_status: 200, request_id: 'req-malformed' })
    expect(JSON.stringify(result)).not.toContain(PRIVATE)
  })

  it('does not turn ordinary readiness tool failures into successful results', async () => {
    const h = fixture(async () => Response.json({ status: 'not_ready', checks: { database: 'unavailable' } }, { status: 503 }))
    await expect(h.runtime.client.request('get_readiness')).rejects.toMatchObject({ statusCode: 503 })
  })

  it('identifies a Server context schema that the plugin cannot consume', async () => {
    const h = fixture(async url => new URL(url).pathname === '/v1/capabilities'
      ? Response.json({ ...CAPABILITIES, context_versions: ['unsupported'] }) : healthy(new URL(url).pathname))
    expect(await h.doctor()).toMatchObject({ ok: false, checks: { capabilities: { code: 'unsupported_context_schema', http_status: 200 } } })
  })

  it('never returns recalled content or injects a snapshot from a diagnostic probe', async () => {
    const h = fixture(async url => new URL(url).pathname.endsWith('/prepare')
      ? Response.json({ ...EMPTY, status: 'ready', content: PRIVATE, content_bytes: Buffer.byteLength(PRIVATE) })
      : healthy(new URL(url).pathname))
    const result = await h.doctor()
    expect(result.checks.prepare).toMatchObject({ state: 'ok', code: 'ready' })
    expect(JSON.stringify(result)).not.toContain(PRIVATE)
  })
})
