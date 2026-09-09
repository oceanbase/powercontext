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

export const REQUEST_ID_HEADER = 'X-PowerContext-Request-ID'
export const MAX_RESPONSE_BYTES = 1_048_576
export const MAX_CONTEXT_BYTES = 32_768
export const MAX_SOURCE_LENGTH = 200_000
export const PLUGIN_NAME = 'powercontext-dsh'
export const PLUGIN_VERSION = '0.0.2'
export const PLUGIN_USER_AGENT = `${PLUGIN_NAME}/${PLUGIN_VERSION}`

export class ClientError extends Error {
  readonly requestId: string | undefined

  constructor(message: string, requestId?: string) {
    super(message)
    this.name = new.target.name
    this.requestId = requestId
  }
}

export class TransportError extends ClientError {
  readonly path: string

  constructor(path: string, cause?: unknown) {
    super(`request to ${path} failed`)
    this.path = path
    this.cause = cause
  }
}

export class UnavailableError extends TransportError {}

export const RESPONSE_ISSUES = {
  invalid_json: 'The response body is not valid JSON.',
  redirect: 'The operation returned a redirect; the client does not follow redirects.',
  response_too_large: 'The response body exceeds the 1 MiB client limit.',
  unexpected_body: 'This status requires an empty response body.',
  prepared_object: 'PreparedContext must be a JSON object.',
  prepared_fields: 'PreparedContext must contain exactly schema, status, content and content_bytes.',
  prepared_schema: 'PreparedContext.schema must be powercontext.prepared-context.v1.',
  prepared_size: 'PreparedContext.content_bytes must be a non-negative integer.',
  prepared_empty: 'An empty PreparedContext must have null content and content_bytes equal to zero.',
  prepared_content: 'A ready PreparedContext must contain non-empty text.',
  prepared_bytes: 'PreparedContext.content_bytes must match the UTF-8 content size and stay within the requested budget.',
  liveness: 'Liveness requires HTTP 200 and a JSON object with status equal to ok.',
  readiness: 'Readiness requires status ready/degraded with HTTP 200, or not_ready with HTTP 503, and a checks object of string values.',
  capabilities: 'Capabilities requires HTTP 200, boolean memory_extraction/handoff_generation, and string arrays for source_types/artifact_families/search_modes/context_versions.',
  openapi: 'API discovery requires HTTP 200, an OpenAPI 3.x version and a paths object.',
  prepare_status: 'PreparedContext requires HTTP 200.',
} as const

export class InvalidResponseError extends ClientError {
  readonly path: string
  readonly statusCode: number | undefined
  readonly issue: keyof typeof RESPONSE_ISSUES | undefined

  constructor(path: string, requestId?: string, statusCode?: number, issue?: keyof typeof RESPONSE_ISSUES) {
    super(`response from ${path} violated the API schema`, requestId)
    this.path = path
    this.statusCode = statusCode
    this.issue = issue
  }
}

export class UnknownOperationError extends ClientError {
  readonly operationId: string

  constructor(operationId: string) {
    super(`unknown PowerContext operation: ${operationId}`)
    this.operationId = operationId
  }
}

export class SecretRejectedError extends ClientError {
  constructor() {
    super('refused to send secret-like content to PowerContext')
  }
}

export class ServerResponseError extends ClientError {
  readonly statusCode: number
  readonly path: string
  readonly code: unknown
  readonly serverMessage: string | undefined

  constructor(options: {
    statusCode: number
    path?: string
    requestId?: string
    code?: unknown
    message?: string
  }) {
    const suffix = typeof options.code === 'string' ? ` (${options.code})` : ''
    super(`PowerContext Server returned HTTP ${options.statusCode}${suffix}`, options.requestId)
    this.statusCode = options.statusCode
    this.path = options.path ?? ''
    this.code = options.code
    this.serverMessage = options.message
  }
}
