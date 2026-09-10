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
import { readFileSync, statSync } from 'node:fs'
import { homedir } from 'node:os'
import { join } from 'node:path'

export interface PluginConfig {
  contextAssembly?: Record<string, unknown>
  baseUrl?: string
  allowInsecureHttp?: boolean
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
  contextAssembly?: Record<string, unknown>
  sources: { baseUrl: ConfigSource; authorization: ConfigSource; scopeId: ConfigSource }
  baseUrl: string
  allowInsecureHttp: boolean
  authorization: string | undefined
  scopeId: string | undefined
  timeoutMs: number
  requestTimeoutMs: number
  maxBytes: number
  capturePrompts: boolean
  flushOnCapture: boolean
  flushMaxCalls: number
}

export type ConfigSource = 'environment' | 'plugin' | 'saved' | 'default'

const DEFAULTS: ResolvedConfig = {
  sources: { baseUrl: 'default', authorization: 'default', scopeId: 'default' },
  baseUrl: 'http://127.0.0.1:8000',
  allowInsecureHttp: false,
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

function optionalText(value: string | undefined): string | undefined {
  const trimmed = value?.trim()
  return trimmed ? trimmed : undefined
}

function contextAssembly(raw: string | undefined, fallback?: Record<string, unknown>): Record<string, unknown> | undefined {
  let value: unknown
  try {
    value = raw === undefined ? fallback : JSON.parse(raw)
  } catch {
    throw new Error('PowerContext context assembly must be a JSON object')
  }
  if (value === undefined) return undefined
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error('PowerContext context assembly must be a JSON object')
  }
  return structuredClone(value as Record<string, unknown>)
}

function storedAuthorization(env: NodeJS.ProcessEnv, baseUrl: string): string | undefined {
  const root = env.DSH_HOME?.trim() || join(homedir(), '.dsh')
  const path = join(root, 'powercontext', 'credentials.json')
  try {
    if (process.platform !== 'win32' && (statSync(path).mode & 0o077) !== 0) return undefined
    const parsed: unknown = JSON.parse(readFileSync(path, 'utf8'))
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return undefined
    const payload = parsed as Record<string, unknown>
    if (payload.version !== 1 || typeof payload.server_url !== 'string' || stripSlash(payload.server_url) !== baseUrl) return undefined
    if (typeof payload.authorization !== 'string') return undefined
    const authorization = payload.authorization
    return /^Bearer [^\s]+$/.test(authorization) ? authorization : undefined
  } catch {
    return undefined
  }
}

export function resolveConfig(
  config: PluginConfig = {},
  env: NodeJS.ProcessEnv = process.env,
): ResolvedConfig {
  const transport = resolveTransport('dsh', env, config.baseUrl, config.allowInsecureHttp, DEFAULTS.baseUrl)
  const maxBytes = config.maxBytes ?? DEFAULTS.maxBytes
  if (maxBytes < 512 || maxBytes > 32768) {
    throw new Error('maxBytes must be between 512 and 32768')
  }
  return {
    contextAssembly: contextAssembly(envString(env, 'POWERCONTEXT_DSH_CONTEXT_ASSEMBLY'), config.contextAssembly),
    sources: {
      baseUrl: transport.source,
      authorization: envString(env, 'POWERCONTEXT_DSH_AUTHORIZATION') ? 'environment' : optionalText(config.authorization) ? 'plugin' : 'default',
      scopeId: envString(env, 'POWERCONTEXT_DSH_SCOPE_ID') ? 'environment' : optionalText(config.scopeId) ? 'plugin' : 'default',
    },
    baseUrl: transport.baseUrl!,
    allowInsecureHttp: transport.allowInsecureHttp,
    authorization:
      envString(env, 'POWERCONTEXT_DSH_AUTHORIZATION') ??
      optionalText(config.authorization) ??
      storedAuthorization(env, transport.baseUrl!),
    scopeId: envString(env, 'POWERCONTEXT_DSH_SCOPE_ID') ?? optionalText(config.scopeId),
    timeoutMs: config.timeoutMs ?? DEFAULTS.timeoutMs,
    requestTimeoutMs: config.requestTimeoutMs ?? DEFAULTS.requestTimeoutMs,
    maxBytes,
    capturePrompts: envBoolean(env, 'POWERCONTEXT_DSH_CAPTURE_PROMPTS') ?? config.capturePrompts ?? DEFAULTS.capturePrompts,
    flushOnCapture: envBoolean(env, 'POWERCONTEXT_DSH_FLUSH_ON_CAPTURE') ?? config.flushOnCapture ?? DEFAULTS.flushOnCapture,
    flushMaxCalls: config.flushMaxCalls ?? DEFAULTS.flushMaxCalls,
  }
}
