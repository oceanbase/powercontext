#!/usr/bin/env node
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

// Registry selection belongs to dependency installation, not tests or publishing.
import { spawn, spawnSync } from 'node:child_process'
import { appendFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

const official = 'https://registry.npmjs.org/'
const mirrorDefault = 'https://registry.npmmirror.com/'
const installCommands = new Set(['install', 'i', 'ci', 'add'])
const valuedOptions = new Set(['--dir', '-C', '--prefix', '--workspace', '-w', '--filter', '--registry', '--userconfig', '--cache'])

function commandIndex(args) {
  for (let index = 0; index < args.length; index++) {
    if (valuedOptions.has(args[index])) index++
    else if (!args[index].startsWith('-')) return index
  }
  return -1
}

function registryOption(args) {
  let value
  for (let index = 0; index < args.length; index++) {
    if (args[index] === '--registry') value = args[++index]
    else if (args[index].startsWith('--registry=')) value = args[index].slice('--registry='.length)
  }
  return value
}

function withRegistry(args, registry) {
  const result = []
  for (let index = 0; index < args.length; index++) {
    if (args[index] === '--registry') index++
    else if (!args[index].startsWith('--registry=')) result.push(args[index])
  }
  return [...result, `--registry=${registry}`]
}

function isOfficial(registry) {
  try {
    const url = new URL(registry)
    return url.protocol === 'https:' && url.hostname === 'registry.npmjs.org' && url.pathname === '/'
  } catch { return false }
}

function configuredRegistry(manager, args, index) {
  const context = args.slice(0, index)
  for (let position = index + 1; position < args.length; position++) {
    if (['--dir', '-C', '--prefix', '--userconfig'].includes(args[position])) {
      context.push(args[position], args[++position])
    } else if (/^--(?:dir|prefix|userconfig)=/.test(args[position])) context.push(args[position])
  }
  const result = spawnSync(manager, [...context, 'config', 'get', 'registry'], { encoding: 'utf8' })
  return result.status === 0 ? result.stdout.trim() : ''
}

function directEnvironment(mirror) {
  const host = new URL(mirror).hostname
  const hosts = [host]
  // npmmirror metadata can point at a separate tarball endpoint.
  if (host === 'registry.npmmirror.com') hosts.push('cdn.npmmirror.com', 'r2.cnpmjs.org')
  const noProxy = [...new Set([process.env.no_proxy || '', process.env.NO_PROXY || '', process.env.npm_config_noproxy || '', ...hosts].join(',').split(',').filter(Boolean))].join(',')
  return { ...process.env, no_proxy: noProxy, NO_PROXY: noProxy, npm_config_noproxy: noProxy }
}

function downloadFailure(manager, output) {
  const plain = output.replace(/\x1b\[[0-9;]*m/g, '')
  // A failed lifecycle script must not run again, even if its output mentions networking.
  if (/\bELIFECYCLE\b|npm (?:ERR!|error) command failed/.test(plain)) return false
  if (manager === 'npm') {
    const codes = [...plain.matchAll(/npm (?:ERR!|error) code ([A-Z_0-9]+)/g)]
    return new Set(['ECONNRESET', 'ECONNREFUSED', 'ETIMEDOUT', 'ESOCKETTIMEDOUT', 'ERR_SOCKET_TIMEOUT', 'EAI_AGAIN', 'ENOTFOUND', 'FETCH_ERROR', 'E404', 'E408', 'E429', 'E500', 'E502', 'E503', 'E504', 'ETARGET', 'EINTEGRITY']).has(codes.at(-1)?.[1])
  }
  const codes = [...plain.matchAll(/\b(ERR_PNPM_[A-Z_0-9]+)\b/g)]
  if (codes.length) {
    return /^ERR_PNPM_(?:FETCH_\d+|META_FETCH_FAIL|TARBALL_INTEGRITY|NO_MATCHING_VERSION)$/.test(codes.at(-1)[1])
  }
  // pnpm's tarball downloader can expose native fetch failures without a PNPM error code.
  return /^\s*(?:\[ERROR\]|ERROR)\s+fetch failed\s*$/m.test(plain) && /^\s*TypeError: fetch failed\s*$/m.test(plain)
}

async function run(manager, args, env) {
  return new Promise((resolve, reject) => {
    const child = spawn(manager, args, { env, stdio: ['inherit', 'pipe', 'pipe'] })
    let output = ''
    const forward = (destination) => (chunk) => {
      destination.write(chunk)
      output = (output + chunk.toString()).slice(-262144)
    }
    child.stdout.on('data', forward(process.stdout))
    child.stderr.on('data', forward(process.stderr))
    const onTerm = () => child.kill('SIGTERM')
    const onInt = () => child.kill('SIGINT')
    process.on('SIGTERM', onTerm)
    process.on('SIGINT', onInt)
    child.on('error', reject)
    child.on('close', (code, signal) => {
      process.off('SIGTERM', onTerm)
      process.off('SIGINT', onInt)
      resolve({ code: code ?? (signal === 'SIGINT' ? 130 : 143), output, signal })
    })
  })
}

async function main() {
  const [manager, ...args] = process.argv.slice(2)
  const internal = process.env.RUNNER_OS === 'Linux' && process.env.RUNNER_ENVIRONMENT === 'self-hosted'
  const mirror = process.env.PC_CI_NPM_MIRROR || (internal ? mirrorDefault : '')
  if (manager === 'configure') {
    if (!process.env.GITHUB_ENV) throw new Error('configure requires GITHUB_ENV')
    if (mirror) appendFileSync(process.env.GITHUB_ENV, `PC_CI_NODE_INSTALLER=${fileURLToPath(import.meta.url)}\n`)
    return 0
  }
  if (!['npm', 'pnpm'].includes(manager)) throw new Error('usage: ci_node_mirror.mjs configure | npm|pnpm <arguments>')
  const index = commandIndex(args)
  const offline = args.some(arg => arg === '--offline' || arg === '--offline=true') || /^(true|1)$/i.test(process.env.npm_config_offline || process.env.NPM_CONFIG_OFFLINE || '')
  if (!mirror || !installCommands.has(args[index]) || offline) return (await run(manager, args, process.env)).code
  const registry = registryOption(args) || configuredRegistry(manager, args, index)
  if (!isOfficial(registry)) return (await run(manager, args, process.env)).code
  const normalizedMirror = new URL(mirror).href.replace(/\/?$/, '/')
  console.log(`[ci-node-mirror] Installing dependencies through ${new URL(normalizedMirror).host}`)
  const result = await run(manager, withRegistry(args, normalizedMirror), directEnvironment(normalizedMirror))
  if (result.code === 0 || result.signal || !downloadFailure(manager, result.output)) return result.code
  console.log('[ci-node-mirror] Mirror download failed; retrying installation from the official registry')
  return (await run(manager, withRegistry(args, official), process.env)).code
}

try { process.exitCode = await main() }
catch (error) { console.error(error.message); process.exitCode = 1 }
