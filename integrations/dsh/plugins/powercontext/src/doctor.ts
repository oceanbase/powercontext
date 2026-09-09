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

import type { ClientSuccess } from './client.ts'
import type { ResolvedConfig } from './config.ts'
import { publicErrorCode } from './diagnostics.ts'
import { InvalidResponseError, RESPONSE_ISSUES, ServerResponseError, TransportError } from './errors.ts'
import type { PluginRuntime } from './invoke.ts'
import { OPERATIONS, type OperationId } from './operations.generated.ts'
import { PREPARED_CONTEXT_SCHEMA, validatePreparedContext } from './prepared-context.ts'

export interface DoctorCheck {
  state: 'ok' | 'failed' | 'degraded' | 'skipped'
  code: string
  operation: string
  message: string
  recovery?: string
  http_status?: number
  request_id?: string
  protocol_issue?: keyof typeof RESPONSE_ISSUES
  dependencies?: Record<string, string>
  operations?: string[]
}

const CORE_OPERATIONS: OperationId[] = [
  'get_liveness', 'get_readiness', 'get_capabilities', 'resolve_scope_binding',
  'prepare_context', 'capture_content_source', 'remember_memory', 'search_memory',
]
const DEPENDENCY_RECOVERY: Record<string, string> = {
  runtime: 'Inspect the running Server startup and service logs for Runtime initialization failures.',
  database: 'Check the running Server database URL, database availability and database credentials.',
  'inference.generation': 'Check POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL, its Base URL and provider credentials.',
  'inference.embedding': 'Check the embedding model, Base URL, credentials, profile ID and dimension.',
  'inference.rerank': 'Check the rerank model, Base URL and provider credentials.',
  authentication_provider: 'Check the running Server authentication provider and its token configuration.',
  access_provider: 'Check the running Server access-control provider configuration and readiness.',
}
const CONFIGURATION_CODES = new Set([
  'dependency', 'model-instance', 'instructions', 'schema', 'input-type', 'serialize',
  'embedder-instance', 'embedding-model', 'embedding-batch-size', 'dimension-positive',
  'profile-identifiers', 'provider-rejected', 'pydantic-rejected',
])

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function check(operation: string, code: string, message: string, recovery?: string,
  state: DoctorCheck['state'] = 'failed'): DoctorCheck {
  return { state, code, operation, message, ...(recovery ? { recovery } : {}) }
}

function observed(operation: string, response: ClientSuccess, code: string, message: string): DoctorCheck {
  return { ...check(operation, code, message, undefined, 'ok'), http_status: response.status,
    ...requestId(response.requestId) }
}

function requestId(value: string | undefined): { request_id?: string } {
  return value && /^[a-zA-Z0-9._:-]{1,128}$/.test(value) ? { request_id: value } : {}
}

function invalid(operation: string, response: ClientSuccess, issue?: keyof typeof RESPONSE_ISSUES): never {
  throw new InvalidResponseError(operation, response.requestId, response.status, issue)
}

function transportFailure(error: unknown): [string, string, string] {
  const cause = error instanceof TransportError ? error.cause : error
  if (cause instanceof Error && cause.name === 'TimeoutError') {
    return ['request_timeout', 'The request exceeded its deadline.',
      'Check the effective requestTimeoutMs and the running Server latency; inspect the failing dependency before increasing the timeout.']
  }
  if (cause instanceof Error && cause.name === 'AbortError') {
    return ['cancelled', 'The diagnostic request was cancelled.', 'Run /pc doctor again when the current cancellation has completed.']
  }
  const detail = record(cause) && record(cause.cause) ? cause.cause : cause
  const code = record(detail) ? detail.code : undefined
  if (code === 'ECONNREFUSED') return ['connection_refused', 'The configured endpoint refused the connection.',
    'Start the intended Server and verify its listening host and port against the running plugin configuration.']
  if (code === 'ENOTFOUND' || code === 'EAI_AGAIN') return ['dns_lookup_failed', 'The configured endpoint hostname could not be resolved.',
    'Check the hostname in POWERCONTEXT_DSH_BASE_URL or the plugin baseUrl and the host DNS configuration.']
  if (typeof code === 'string' && ['CERT_HAS_EXPIRED', 'DEPTH_ZERO_SELF_SIGNED_CERT', 'UNABLE_TO_VERIFY_LEAF_SIGNATURE'].includes(code)) {
    return ['tls_verification_failed', 'TLS certificate verification failed.',
      'Correct the Server certificate chain, trust configuration or hostname; do not disable certificate verification.']
  }
  return ['connection_failed', 'The HTTP transport failed before a usable response was received.',
    'Check the effective endpoint host/port, proxy, network and Server service logs. The transport did not identify a narrower cause.']
}

function failure(operation: string, error: unknown): DoctorCheck {
  if (error instanceof ServerResponseError) {
    const code = publicErrorCode(error.code)
    let result: DoctorCheck
    if (error.statusCode === 401) result = check(operation, 'authentication_failed', 'The Server rejected authentication for this operation.',
      'Set POWERCONTEXT_DSH_AUTHORIZATION to the intended Server credential and restart the DSH process so it receives the override.')
    else if (error.statusCode === 403) result = check(operation, 'authorization_failed', 'The authenticated principal is not allowed to perform this operation.',
      'Check the principal permissions for this operation and selected Scope on the running Server.')
    else if (error.statusCode === 404 && error.code === undefined) result = check(operation, 'required_route_missing', 'The required operation returned HTTP 404 without a domain error code.',
      'Check this operation in the Server API and proxy route table, the plugin base-path setting, and the installed Server/plugin refs. A 404 alone cannot identify which configuration is wrong.')
    else if (error.statusCode === 404 && code === 'scope_not_found') result = check(operation, code, 'The Server could not find the requested Scope.',
      'Check POWERCONTEXT_DSH_SCOPE_ID first, then the session workspace binding and Server default Scope. Select an existing Scope explicitly; Doctor does not change bindings.')
    else if (error.statusCode === 404) result = code
      ? check(operation, code, 'The Server returned a recognized domain-level HTTP 404 for this operation.',
        'Inspect the selected resource and Scope in the Server. This domain response does not establish a missing HTTP route.')
      : check(operation, 'unclassified_not_found', 'The operation returned HTTP 404 with an unrecognized error code.',
        'Use the operation and request ID in the Server logs. This response cannot distinguish a missing resource from a missing route; inspect the contract check separately.')
    else if (error.statusCode === 503) result = check(operation, code ?? 'service_unavailable', 'The Server returned HTTP 503 for this operation.',
      'Inspect the separate readiness dependency results and the running Server logs for this operation.')
    else result = check(operation, code ?? 'http_error', 'The Server rejected this diagnostic operation.',
      'Use this operation, HTTP status and request ID to locate the request in the Server logs.')
    return { ...result, http_status: error.statusCode, ...requestId(error.requestId) }
  }
  if (error instanceof InvalidResponseError) return {
    ...check(operation, 'invalid_response', error.issue ? RESPONSE_ISSUES[error.issue] : 'The response does not satisfy this operation protocol.',
      'Verify the effective endpoint and proxy target serve PowerContext, and use matching Server/plugin refs. Inspect Server logs using the request ID.'),
    ...requestId(error.requestId),
    ...(error.issue ? { protocol_issue: error.issue } : {}),
    ...(error.statusCode === undefined ? {} : { http_status: error.statusCode }),
  }
  if (error instanceof TransportError || (error instanceof Error && ['AbortError', 'TimeoutError'].includes(error.name))) {
    const [code, message, recovery] = transportFailure(error)
    return check(operation, code, message, recovery)
  }
  return check(operation, 'diagnostic_error', 'A local diagnostic operation failed before its result could be validated.',
    'Inspect the DSH plugin logs for this operation and report the installed plugin commit. No Server root cause was established.')
}

function configuration(config: ResolvedConfig, cwd?: string) {
  let origin: string | undefined
  let pathPrefix = false
  let valid = false
  try {
    const url = new URL(config.baseUrl)
    valid = ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password && !url.search && !url.hash
    if (valid) {
      origin = url.origin
      pathPrefix = url.pathname !== '/'
    }
  } catch { /* An invalid URL may contain credentials; never echo it. */ }
  return {
    valid,
    timeoutValid: Number.isInteger(config.requestTimeoutMs) && config.requestTimeoutMs > 0 && config.requestTimeoutMs <= 4_294_967_295,
    summary: {
      observation: 'running_plugin',
      endpoint: { ...(origin ? { origin } : {}), source: config.sources.baseUrl, path_prefix: pathPrefix },
      authorization: { configured: Boolean(config.authorization), source: config.sources.authorization },
      scope: { source: config.sources.scopeId, selection: config.scopeId ? 'explicit' : cwd?.trim() ? 'workspace_then_default' : 'server_default' },
      request_timeout_ms: config.requestTimeoutMs,
    },
  }
}

function dependencyStatus(value: string): string {
  if (['ready', 'not_ready', 'disabled', 'unavailable', 'timeout', 'misconfigured'].includes(value)) return value
  const configured = /^misconfigured: ([a-z-]+)(?: \(HTTP ([45][0-9]{2})\))?$/.exec(value)
  if (configured && CONFIGURATION_CODES.has(configured[1])) return value
  return value.startsWith('misconfigured:') ? 'misconfigured' : 'unrecognized'
}

function readiness(response: ClientSuccess): DoctorCheck {
  const operation = 'get_readiness'
  const body = response.value
  if (!record(body) || !record(body.checks) || !['ready', 'degraded', 'not_ready'].includes(String(body.status))
    || Object.values(body.checks).some(value => typeof value !== 'string')
    || (response.status === 503) !== (body.status === 'not_ready')
    || ![200, 503].includes(response.status)) invalid(operation, response, 'readiness')
  const dependencies: Record<string, string> = {}
  for (const name of Object.keys(DEPENDENCY_RECOVERY)) {
    const value = body.checks[name]
    if (typeof value === 'string') dependencies[name] = dependencyStatus(value)
  }
  const failed = Object.keys(dependencies).filter(name => !['ready', 'disabled'].includes(dependencies[name]))
  const result = observed(operation, response, String(body.status), 'Validated the Server readiness response.')
  if (body.status !== 'ready' || failed.length) {
    result.state = body.status === 'degraded' ? 'degraded' : 'failed'
    result.code = body.status === 'ready' ? 'inconsistent_readiness' : String(body.status)
    result.message = failed.length ? 'Readiness dependency checks failed: ' + failed.join(', ') + '.'
      : 'The Server reports ' + String(body.status) + '; no recognized failing dependency was provided.'
    result.recovery = failed.length ? failed.map(name => DEPENDENCY_RECOVERY[name]).join(' ')
      : 'Inspect the running Server readiness and service logs; unrecognized dependency details are withheld.'
  }
  return { ...result, dependencies }
}

function capabilities(response: ClientSuccess): DoctorCheck {
  const operation = 'get_capabilities'
  const body = response.value
  if (response.status !== 200 || !record(body) || typeof body.memory_extraction !== 'boolean'
    || typeof body.handoff_generation !== 'boolean'
    || !['source_types', 'artifact_families', 'search_modes', 'context_versions'].every(key =>
      Array.isArray(body[key]) && (body[key] as unknown[]).every(value => typeof value === 'string'))) invalid(operation, response, 'capabilities')
  if (!(body.context_versions as string[]).includes(PREPARED_CONTEXT_SCHEMA)) return { ...check(operation, 'unsupported_context_schema',
    'The Server does not advertise the PreparedContext schema required by this plugin.',
    'Install Server and plugin from the same supported release tag or checkout commit.'),
    http_status: response.status, ...requestId(response.requestId) }
  return observed(operation, response, body.memory_extraction ? 'extraction_enabled' : 'extraction_disabled',
    body.memory_extraction
      ? 'Memory extraction is configured. A processing/recall acceptance check is still required to prove the complete loop.'
      : 'Automatic Memory extraction is disabled. Source acceptance and a healthy Server can legitimately coexist with empty recall.')
}

function routes(response: ClientSuccess, flush: boolean): DoctorCheck {
  const operation = 'openapi_document'
  const body = response.value
  if (response.status !== 200 || !record(body) || typeof body.openapi !== 'string' || !body.openapi.startsWith('3.')
    || !record(body.paths)) invalid(operation, response, 'openapi')
  const required = [...CORE_OPERATIONS, ...flush ? ['flush_memory' as const] : []]
  const paths = body.paths
  const missing = required.filter(id => {
    const spec = OPERATIONS[id]
    const path = paths[spec.path]
    if (!record(path)) return true
    const declaration = path[spec.method.toLowerCase()]
    return !record(declaration) || declaration.operationId !== id
  })
  return missing.length
    ? { ...check(operation, 'required_route_undeclared', 'The Server contract is missing required operation declarations.',
      'Compare the listed operations with the Server release and proxy contract endpoint; install matching Server/plugin refs.'),
      operations: missing, http_status: response.status, ...requestId(response.requestId) }
    : { ...observed(operation, response, 'routes_declared', 'The Server contract declares the listed core Memory operations. Write routes were not executed.'),
      operations: required }
}

export async function diagnoseServer(runtime: PluginRuntime, cwd?: string, signal?: AbortSignal) {
  const config = configuration(runtime.config, cwd)
  const checks: Record<string, DoctorCheck> = {
    configuration: !config.timeoutValid
      ? check('configuration', 'invalid_timeout', 'The plugin requestTimeoutMs is not a positive supported millisecond duration.',
        'Set requestTimeoutMs in the plugin patch to an integer between 1 and 4294967295, then restart DSH.')
      : config.valid
      ? check('configuration', 'effective_configuration', 'Using the running plugin resolved configuration.', undefined, 'ok')
      : check('configuration', 'invalid_endpoint', 'The plugin base URL is not an HTTP(S) base URL without userinfo, query or fragment.',
        'Correct POWERCONTEXT_DSH_BASE_URL or plugin baseUrl. Put credentials in POWERCONTEXT_DSH_AUTHORIZATION and restart DSH.'),
  }
  async function probe(operation: string, run: () => Promise<DoctorCheck>): Promise<DoctorCheck> {
    if (!config.valid || !config.timeoutValid) return check(operation, 'invalid_configuration', 'Not checked because the plugin configuration is invalid.',
      'Correct the configuration check first.', 'skipped')
    try {
      signal?.throwIfAborted()
      const result = await run()
      signal?.throwIfAborted()
      return result
    } catch (error) {
      if (signal?.aborted) {
        const reason = signal.reason instanceof Error && signal.reason.name === 'TimeoutError'
          ? signal.reason : new DOMException('Diagnostic cancelled', 'AbortError')
        return failure(operation, reason)
      }
      return failure(operation, error)
    }
  }
  checks.liveness = await probe('get_liveness', async () => {
    const response = await runtime.client.request('get_liveness', {}, signal)
    if (response.status !== 200 || !record(response.value) || response.value.status !== 'ok') invalid('get_liveness', response, 'liveness')
    return observed('get_liveness', response, 'live', 'The PowerContext liveness response is valid.')
  })
  checks.readiness = await probe('get_readiness', async () => readiness(
    await runtime.client.request('get_readiness', {}, signal, { readinessResponse: true })))
  checks.capabilities = await probe('get_capabilities', async () => capabilities(
    await runtime.client.request('get_capabilities', {}, signal)))
  checks.routes = await probe('openapi_document', async () => {
    try { return routes(await runtime.client.readOpenApi(signal), runtime.config.flushOnCapture) } catch (error) {
      if (error instanceof ServerResponseError && error.statusCode === 404) return {
        ...check('openapi_document', 'contract_unavailable', 'The Server contract endpoint returned HTTP 404; declared route support is unverified.',
          'Expose the Server /openapi.json through the configured base path or verify the installed contract separately.', 'skipped'),
        http_status: 404, ...requestId(error.requestId),
      }
      throw error
    }
  })
  let scopeId: string | undefined
  checks.scope = await probe('resolve_scope_binding', async () => {
    scopeId = await runtime.resolveScope(cwd, signal)
    return scopeId ? check('resolve_scope_binding', 'scope_resolved', 'The Server resolved the current session Scope.', undefined, 'ok')
      : check('resolve_scope_binding', 'unscoped', 'Scope resolution returned no usable Scope.',
        'Check the explicit Scope override, workspace binding and Server default Scope. Doctor does not create bindings.')
  })
  checks.prepare = scopeId ? await probe('prepare_context', async () => {
    const response = await runtime.client.request('prepare_context', {
      scope_id: scopeId, query: 'PowerContext diagnostic recall check', max_bytes: 512,
    }, signal)
    let prepared: ReturnType<typeof validatePreparedContext>
    try {
      prepared = validatePreparedContext(response.value, '/v1/context/prepare', 512)
      if (response.status !== 200) invalid('prepare_context', response, 'prepare_status')
    } catch (error) {
      if (error instanceof InvalidResponseError) invalid('prepare_context', response, error.issue)
      throw error
    }
    return observed('prepare_context', response, prepared.status,
      prepared.status === 'empty' ? 'The prepare route returned a valid empty result.'
        : 'The prepare route returned valid context; Doctor discarded the content without injecting it.')
  }) : check('prepare_context', 'scope_unavailable', 'Not checked because the current Scope could not be resolved.',
    'Resolve the Scope check first.', 'skipped')
  return {
    ok: Object.values(checks).every(value => value.state === 'ok'),
    configuration: config.summary, checks,
    coverage: 'Read-only checks of the current configuration. Write routes are declared by the contract but not executed; processing, capture and injection are not verified by Doctor.',
  }
}
