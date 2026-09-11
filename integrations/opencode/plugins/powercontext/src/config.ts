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

import { resolveTransport } from './transport.ts'

export interface ResolvedConfig {
  contextAssembly?: Record<string, unknown>
  baseUrl: string
  allowInsecureHttp: boolean
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
  allowInsecureHttp: false,
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

function contextAssembly(raw: string | undefined): Record<string, unknown> | undefined {
  if (raw === undefined) return undefined
  let value: unknown
  try {
    value = JSON.parse(raw)
  } catch {
    throw new Error('PowerContext context assembly must be a JSON object')
  }
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error('PowerContext context assembly must be a JSON object')
  }
  return value as Record<string, unknown>
}

function envBoolean(env: NodeJS.ProcessEnv, name: string): boolean | undefined {
  const value = envString(env, name)?.toLowerCase()
  if (!value) return undefined
  if (['1', 'true', 'yes', 'on'].includes(value)) return true
  if (['0', 'false', 'no', 'off'].includes(value)) return false
  throw new Error(`${name} must be a boolean`)
}

function envInteger(env: NodeJS.ProcessEnv, name: string, fallback: number, minimum: number, maximum: number): number {
  const raw = envString(env, name)
  if (!raw) return fallback
  const value = Number(raw)
  if (!Number.isInteger(value) || value < minimum || value > maximum) {
    throw new Error(`${name} must be an integer between ${minimum} and ${maximum}`)
  }
  return value
}

export function resolveConfig(env: NodeJS.ProcessEnv = process.env): ResolvedConfig {
  const transport = resolveTransport('opencode', env, undefined, undefined, DEFAULTS.baseUrl)
  const requestTimeoutMs = envInteger(
    env,
    'POWERCONTEXT_OPENCODE_REQUEST_TIMEOUT_MS',
    DEFAULTS.requestTimeoutMs,
    50,
    30_000,
  )
  const httpBudgetMs = envInteger(
    env,
    'POWERCONTEXT_OPENCODE_HTTP_BUDGET_MS',
    DEFAULTS.httpBudgetMs,
    100,
    60_000,
  )
  if (requestTimeoutMs > httpBudgetMs) {
    throw new Error('POWERCONTEXT_OPENCODE_REQUEST_TIMEOUT_MS must not exceed POWERCONTEXT_OPENCODE_HTTP_BUDGET_MS')
  }
  return {
    contextAssembly: contextAssembly(envString(env, 'POWERCONTEXT_OPENCODE_CONTEXT_ASSEMBLY')),
    baseUrl: transport.baseUrl!,
    allowInsecureHttp: transport.allowInsecureHttp,
    scopeId: envString(env, 'POWERCONTEXT_OPENCODE_SCOPE_ID'),
    authorization: envString(env, 'POWERCONTEXT_OPENCODE_AUTHORIZATION'),
    capturePrompts: envBoolean(env, 'POWERCONTEXT_OPENCODE_CAPTURE_PROMPTS') ?? DEFAULTS.capturePrompts,
    requestTimeoutMs,
    httpBudgetMs,
    maxBytes: envInteger(env, 'POWERCONTEXT_OPENCODE_MAX_BYTES', DEFAULTS.maxBytes, 512, 32_768),
    flushOnCapture: envBoolean(env, 'POWERCONTEXT_OPENCODE_FLUSH_ON_CAPTURE') ?? DEFAULTS.flushOnCapture,
    flushMaxCalls: envInteger(env, 'POWERCONTEXT_OPENCODE_FLUSH_MAX_CALLS', DEFAULTS.flushMaxCalls, 1, 16),
  }
}
