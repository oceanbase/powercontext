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

export class InvalidResponseError extends ClientError {
  readonly path: string

  constructor(path: string, requestId?: string) {
    super(`response from ${path} violated the API schema`, requestId)
    this.path = path
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
  readonly code: string | undefined
  readonly serverMessage: string | undefined

  constructor(options: {
    statusCode: number
    path?: string
    requestId?: string
    code?: string
    message?: string
  }) {
    const suffix = options.code ? ` (${options.code})` : ''
    super(`PowerContext Server returned HTTP ${options.statusCode}${suffix}`, options.requestId)
    this.statusCode = options.statusCode
    this.path = options.path ?? ''
    this.code = options.code
    this.serverMessage = options.message
  }
}
