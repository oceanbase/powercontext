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
  readonly code: string | undefined
  readonly serverMessage: string | undefined

  constructor(options: {
    statusCode: number
    requestId?: string
    code?: string
    message?: string
  }) {
    const suffix = options.code ? ` (${options.code})` : ''
    super(`PowerContext Server returned HTTP ${options.statusCode}${suffix}`, options.requestId)
    this.statusCode = options.statusCode
    this.code = options.code
    this.serverMessage = options.message
  }
}
