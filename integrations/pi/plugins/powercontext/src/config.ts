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

export interface ResolvedConfig {
  baseUrl: string
  scopeId: string | undefined
  authorization: string | undefined
  capturePrompts: boolean
  requestTimeoutMs: number
  httpBudgetMs: number
  maxBytes: number
  flushOnCapture: boolean
  flushMaxCalls: number
}

const DEFAULTS: ResolvedConfig = {
  baseUrl: 'http://127.0.0.1:8000',
  scopeId: undefined,
  authorization: undefined,
  capturePrompts: true,
  requestTimeoutMs: 1000,
  httpBudgetMs: 4000,
  maxBytes: 8000,
  flushOnCapture: false,
  flushMaxCalls: 4,
}

function envString(env: NodeJS.ProcessEnv, name: string): string | undefined {
  const value = env[name]?.trim()
  return value || undefined
}

function envBoolean(env: NodeJS.ProcessEnv, name: string): boolean | undefined {
  const value = envString(env, name)?.toLowerCase()
  if (!value) return undefined
  if (['1', 'true', 'yes', 'on'].includes(value)) return true
  if (['0', 'false', 'no', 'off'].includes(value)) return false
  throw new Error(`${name} must be a boolean`)
}

function envInteger(
  env: NodeJS.ProcessEnv,
  name: string,
  defaultValue: number,
  minimum: number,
  maximum: number,
): number {
  const raw = envString(env, name)
  if (!raw) return defaultValue
  const value = Number(raw)
  if (!Number.isInteger(value) || value < minimum || value > maximum) {
    throw new Error(`${name} must be an integer between ${minimum} and ${maximum}`)
  }
  return value
}

function isLoopback(hostname: string): boolean {
  return hostname === 'localhost' || hostname === '127.0.0.1' || hostname === '[::1]'
}

function normalizeBaseUrl(value: string): string {
  let url: URL
  try {
    url = new URL(value)
  } catch {
    throw new Error('POWERCONTEXT_PI_BASE_URL must be a valid HTTP(S) URL')
  }
  if (!['http:', 'https:'].includes(url.protocol)) {
    throw new Error('POWERCONTEXT_PI_BASE_URL must use HTTP or HTTPS')
  }
  if (url.username || url.password || url.search || url.hash) {
    throw new Error('POWERCONTEXT_PI_BASE_URL must not contain credentials, a query, or a fragment')
  }
  if (url.protocol === 'http:' && !isLoopback(url.hostname)) {
    throw new Error('POWERCONTEXT_PI_BASE_URL must use HTTPS outside loopback')
  }
  return url.toString().replace(/\/+$/, '')
}

export function resolveConfig(env: NodeJS.ProcessEnv = process.env): ResolvedConfig {
  const requestTimeoutMs = envInteger(env, 'POWERCONTEXT_PI_REQUEST_TIMEOUT_MS', DEFAULTS.requestTimeoutMs, 50, 30_000)
  const httpBudgetMs = envInteger(env, 'POWERCONTEXT_PI_HTTP_BUDGET_MS', DEFAULTS.httpBudgetMs, 100, 60_000)
  if (requestTimeoutMs > httpBudgetMs) {
    throw new Error('POWERCONTEXT_PI_REQUEST_TIMEOUT_MS must not exceed POWERCONTEXT_PI_HTTP_BUDGET_MS')
  }
  return {
    baseUrl: normalizeBaseUrl(envString(env, 'POWERCONTEXT_PI_BASE_URL') ?? DEFAULTS.baseUrl),
    scopeId: envString(env, 'POWERCONTEXT_PI_SCOPE_ID'),
    authorization: envString(env, 'POWERCONTEXT_PI_AUTHORIZATION'),
    capturePrompts: envBoolean(env, 'POWERCONTEXT_PI_CAPTURE_PROMPTS') ?? DEFAULTS.capturePrompts,
    requestTimeoutMs,
    httpBudgetMs,
    maxBytes: envInteger(env, 'POWERCONTEXT_PI_MAX_BYTES', DEFAULTS.maxBytes, 512, 32_768),
    flushOnCapture: envBoolean(env, 'POWERCONTEXT_PI_FLUSH_ON_CAPTURE') ?? DEFAULTS.flushOnCapture,
    flushMaxCalls: envInteger(env, 'POWERCONTEXT_PI_FLUSH_MAX_CALLS', DEFAULTS.flushMaxCalls, 1, 16),
  }
}
