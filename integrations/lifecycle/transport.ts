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

import { readFileSync } from 'node:fs'
import { homedir } from 'node:os'
import { join } from 'node:path'

// Each host is an independently distributed package; keep the shared client config
// contract and policy aligned with powercontext.client.transport_policy and powercontext.transport.
type SavedClient = { server_url?: string; allow_insecure_http?: boolean }

function optionalText(value: unknown): string | undefined {
  return typeof value === 'string' ? value.trim() || undefined : undefined
}

function optionalBoolean(value: unknown, name: string): boolean | undefined {
  if (value === undefined) return undefined
  if (typeof value !== 'boolean') throw new Error(`${name} must be a boolean`)
  return value
}

function environmentBoolean(env: NodeJS.ProcessEnv, name: string): boolean | undefined {
  if (env[name] === undefined) return undefined
  const value = env[name]!.trim().toLowerCase()
  if (['true', '1', 'yes', 'on'].includes(value)) return true
  if (['false', '0', 'no', 'off'].includes(value)) return false
  throw new Error(`${name} must be a boolean (true/false, 1/0, yes/no, on/off)`)
}

function readSavedClient(host: string, env: NodeJS.ProcessEnv): SavedClient {
  const home = optionalText(env.HOME) ?? homedir()
  const configuredPath = optionalText(env.POWERCONTEXT_CLIENT_CONFIG_FILE)
  const path = configuredPath?.startsWith('~/')
    ? join(home, configuredPath.slice(2))
    : configuredPath ?? join(home, '.config', 'powercontext', 'clients.json')
  let contents: string
  try {
    contents = readFileSync(path, 'utf8')
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === 'ENOENT') return {}
    throw new Error('Unable to read PowerContext client configuration', { cause: error })
  }
  let document: unknown
  try {
    document = JSON.parse(contents)
  } catch {
    throw new Error('PowerContext client configuration must be valid JSON')
  }
  if (!document || typeof document !== 'object' || Array.isArray(document)
    || (document as { version?: unknown }).version !== 1) {
    throw new Error('PowerContext client configuration must have version 1')
  }
  const hosts = (document as { hosts?: unknown }).hosts
  if (!hosts || typeof hosts !== 'object' || Array.isArray(hosts)) {
    throw new Error('PowerContext client configuration hosts must be an object')
  }
  const value = (hosts as Record<string, unknown>)[host]
  if (value === undefined) return {}
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error('PowerContext saved host configuration must be an object')
  }
  const entry = value as Record<string, unknown>
  if (entry.server_url !== undefined && !optionalText(entry.server_url)) {
    throw new Error('PowerContext saved server_url must be a non-empty string')
  }
  return {
    server_url: optionalText(entry.server_url),
    allow_insecure_http: optionalBoolean(entry.allow_insecure_http, 'allow_insecure_http'),
  }
}

export function normalizeServerUrl(value: string, allowInsecureHttp = false, name = 'PowerContext server URL'): string {
  optionalBoolean(allowInsecureHttp, 'allowInsecureHttp')
  let url: URL
  try {
    url = new URL(value)
  } catch {
    throw new Error(`${name} must be a valid HTTP(S) URL`)
  }
  if (!['http:', 'https:'].includes(url.protocol)) {
    throw new Error(`${name} must use HTTP or HTTPS`)
  }
  if (url.username || url.password || url.search || url.hash) {
    throw new Error(`${name} must not contain credentials, a query, or a fragment`)
  }
  const host = url.hostname.toLowerCase().replace(/^\[/, '').replace(/\]$/, '')
  const octets = host.split('.')
  const loopback = host === 'localhost' || host === '::1'
    || (octets.length === 4 && octets[0] === '127'
      && octets.every((octet) => /^\d{1,3}$/.test(octet) && Number(octet) <= 255))
  if (url.protocol === 'http:' && !loopback && !allowInsecureHttp) {
    throw new Error(`${name} must use HTTPS outside loopback; explicitly enable allow_insecure_http to permit plaintext HTTP`)
  }
  return url.toString().replace(/\/+$/, '').replace(/\/mcp$/, '').replace(/\/+$/, '')
}

export function resolveTransport(
  host: string,
  env: NodeJS.ProcessEnv,
  nativeUrl?: unknown,
  nativeConsent?: unknown,
  defaultUrl?: string,
): { baseUrl: string | undefined; allowInsecureHttp: boolean; source: 'environment' | 'plugin' | 'saved' | 'default' } {
  const prefix = `POWERCONTEXT_${host.toUpperCase()}`
  const saved = readSavedClient(host, env)
  const environmentUrl = optionalText(env[`${prefix}_BASE_URL`])
    ?? optionalText(env[`${prefix}_SERVER_URL`])
    ?? optionalText(env[`${prefix}_ENDPOINT`])
    ?? optionalText(env.POWERCONTEXT_CLIENT_SERVER_URL)
  const pluginUrl = optionalText(nativeUrl)
  const selectedUrl = environmentUrl ?? pluginUrl ?? saved.server_url ?? defaultUrl
  const normalized = selectedUrl === undefined ? undefined : normalizeServerUrl(selectedUrl, true, `${prefix}_BASE_URL`)
  const savedUrl = saved.server_url === undefined ? undefined : normalizeServerUrl(saved.server_url, true)
  const hostConsent = environmentBoolean(env, `${prefix}_ALLOW_INSECURE_HTTP`)
  const commonConsent = environmentBoolean(env, 'POWERCONTEXT_CLIENT_ALLOW_INSECURE_HTTP')
  const pluginConsent = optionalBoolean(nativeConsent, 'allowInsecureHttp')
  const nativeEndpoint = pluginUrl === undefined ? undefined : normalizeServerUrl(pluginUrl, true)
  // Persisted native consent belongs to its endpoint, just like setup-saved consent.
  // An explicit refusal remains effective even when the endpoint is overridden.
  const matchingNativeConsent = pluginConsent === false
    ? false
    : normalized !== undefined && normalized === nativeEndpoint ? pluginConsent : undefined
  const allowInsecureHttp = hostConsent ?? commonConsent ?? matchingNativeConsent
    ?? (normalized !== undefined && normalized === savedUrl ? saved.allow_insecure_http : undefined) ?? false
  return {
    baseUrl: normalized === undefined ? undefined : normalizeServerUrl(normalized, allowInsecureHttp, `${prefix}_BASE_URL`),
    allowInsecureHttp,
    source: environmentUrl ? 'environment' : pluginUrl ? 'plugin' : saved.server_url ? 'saved' : 'default',
  }
}
