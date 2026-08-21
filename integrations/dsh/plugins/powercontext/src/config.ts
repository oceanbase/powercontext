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

export interface PluginConfig {
  baseUrl?: string
  authorization?: string
  scopeId?: string
  timeoutMs?: number
  requestTimeoutMs?: number
  maxBytes?: number
  capturePrompts?: boolean
  flushOnCapture?: boolean
  flushMaxCalls?: number
}

export interface ResolvedConfig {
  baseUrl: string
  authorization: string | undefined
  scopeId: string | undefined
  timeoutMs: number
  requestTimeoutMs: number
  maxBytes: number
  capturePrompts: boolean
  flushOnCapture: boolean
  flushMaxCalls: number
}

const DEFAULTS: ResolvedConfig = {
  baseUrl: 'http://127.0.0.1:8000',
  authorization: undefined,
  scopeId: undefined,
  timeoutMs: 4000,
  requestTimeoutMs: 1000,
  maxBytes: 8000,
  capturePrompts: true,
  flushOnCapture: false,
  flushMaxCalls: 4,
}

function envString(env: NodeJS.ProcessEnv, name: string): string | undefined {
  const value = env[name]?.trim()
  return value ? value : undefined
}

function envBoolean(env: NodeJS.ProcessEnv, name: string): boolean | undefined {
  const value = env[name]?.trim().toLowerCase()
  if (!value) return undefined
  if (['1', 'true', 'yes', 'on'].includes(value)) return true
  if (['0', 'false', 'no', 'off'].includes(value)) return false
  return undefined
}

function stripSlash(url: string): string {
  return url.replace(/\/+$/, '')
}

function optionalText(value: string | undefined): string | undefined {
  const trimmed = value?.trim()
  return trimmed ? trimmed : undefined
}

export function resolveConfig(
  config: PluginConfig = {},
  env: NodeJS.ProcessEnv = process.env,
): ResolvedConfig {
  const maxBytes = config.maxBytes ?? DEFAULTS.maxBytes
  if (maxBytes < 512 || maxBytes > 32768) {
    throw new Error('maxBytes must be between 512 and 32768')
  }
  return {
    baseUrl: stripSlash(envString(env, 'POWERCONTEXT_DSH_BASE_URL') ?? config.baseUrl ?? DEFAULTS.baseUrl),
    authorization: envString(env, 'POWERCONTEXT_DSH_AUTHORIZATION') ?? optionalText(config.authorization),
    scopeId: envString(env, 'POWERCONTEXT_DSH_SCOPE_ID') ?? optionalText(config.scopeId),
    timeoutMs: config.timeoutMs ?? DEFAULTS.timeoutMs,
    requestTimeoutMs: config.requestTimeoutMs ?? DEFAULTS.requestTimeoutMs,
    maxBytes,
    capturePrompts: envBoolean(env, 'POWERCONTEXT_DSH_CAPTURE_PROMPTS') ?? config.capturePrompts ?? DEFAULTS.capturePrompts,
    flushOnCapture: envBoolean(env, 'POWERCONTEXT_DSH_FLUSH_ON_CAPTURE') ?? config.flushOnCapture ?? DEFAULTS.flushOnCapture,
    flushMaxCalls: config.flushMaxCalls ?? DEFAULTS.flushMaxCalls,
  }
}
