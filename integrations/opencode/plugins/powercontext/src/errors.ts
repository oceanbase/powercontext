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
export const PLUGIN_NAME = 'powercontext-opencode'
export const PLUGIN_VERSION = '0.0.1'
export const PLUGIN_USER_AGENT = `${PLUGIN_NAME}/${PLUGIN_VERSION}`

export class ClientError extends Error {
  readonly requestId: string | undefined

  constructor(message: string, requestId?: string) {
    super(message)
    this.name = new.target.name
    this.requestId = requestId
  }
}

export class UnavailableError extends ClientError {
  readonly path: string

  constructor(path: string, cause?: unknown) {
    super(`request to ${path} failed`)
    this.path = path
    this.cause = cause
  }
}

export class InvalidResponseError extends ClientError {
  constructor(readonly path: string, requestId?: string) {
    super(`response from ${path} violated the API schema`, requestId)
  }
}

export class UnknownOperationError extends ClientError {
  constructor(readonly operationId: string) {
    super(`unknown PowerContext operation: ${operationId}`)
  }
}

export class ServerResponseError extends ClientError {
  readonly statusCode: number
  readonly code: string | undefined
  readonly serverMessage: string | undefined

  constructor(options: { statusCode: number; requestId?: string; code?: string; message?: string }) {
    super(`PowerContext returned HTTP ${options.statusCode}${options.code ? ` (${options.code})` : ''}`, options.requestId)
    this.statusCode = options.statusCode
    this.code = options.code
    this.serverMessage = options.message
  }
}
