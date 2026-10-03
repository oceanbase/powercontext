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
'use strict';

const { createHash } = require('node:crypto');
const { isAbsolute, resolve } = require('node:path');
const { createInterface } = require('node:readline');
const manifest = require('../ghost.json');

const MAX_RESPONSE_BYTES = 1_048_576;
const MAX_LINE_BYTES = 1_048_576;
const OPERATIONS = {
  status: ['GET', '/health/ready'],
  list_scopes: ['GET', '/v1/scopes'],
  create_scope: ['POST', '/v1/scopes'],
  resolve_scope: ['POST', '/v1/scope-bindings/resolve'],
  bind_scope: ['PUT', '/v1/scope-bindings'],
  prepare_context: ['POST', '/v1/context/prepare'],
  search_memory: ['POST', '/v1/memory/search'],
  remember_memory: ['POST', '/v1/memory/remember'],
  capture_content_source: ['POST', '/v1/sources/content'],
};
const SCOPED = new Set(['prepare_context', 'search_memory', 'remember_memory', 'capture_content_source']);
const WRITES = new Set(['create_scope', 'bind_scope', 'remember_memory', 'capture_content_source']);
const isObject = (value) => value !== null && typeof value === 'object' && !Array.isArray(value);

class OperationError extends Error {
  constructor(code, status) {
    super('PowerContext operation failed. Check plugin settings and continue the task.');
    this.code = code;
    this.status = status;
  }
}

function reject(code = 'invalid_request') { throw new OperationError(code); }

function normalizeUrl(value, allowInsecureHttp = false) {
  if (typeof value !== 'string' || typeof allowInsecureHttp !== 'boolean') reject('invalid_configuration');
  let url;
  try { url = new URL(value); } catch { reject('invalid_configuration'); }
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.search || url.hash) {
    reject('invalid_configuration');
  }
  const host = url.hostname.replace(/^\[|\]$/g, '');
  const loopback = host === 'localhost' || host === '::1' || /^127(?:\.\d{1,3}){3}$/.test(host);
  if (url.protocol === 'http:' && !loopback && !allowInsecureHttp) reject('insecure_http_not_allowed');
  return url.href.replace(/\/+$/, '').replace(/\/mcp$/, '').replace(/\/+$/, '');
}

// Validate the deliberately small JSON Schema subset used by the tool manifest.
// The Server remains authoritative for domain validation and authorization.
function validate(value, schema) {
  if (schema.type === 'object') {
    if (!isObject(value)) reject();
    for (const key of schema.required ?? []) if (!Object.hasOwn(value, key)) reject();
    for (const [key, item] of Object.entries(value)) {
      if (Object.hasOwn(schema.properties ?? {}, key)) validate(item, schema.properties[key]);
      else if (schema.additionalProperties === false) reject();
    }
  } else if (schema.type === 'array') {
    if (!Array.isArray(value) || value.length > (schema.maxItems ?? Infinity)) reject();
    if (schema.uniqueItems && new Set(value.map((item) => JSON.stringify(item))).size !== value.length) reject();
    for (const item of value) validate(item, schema.items);
  } else if (schema.type === 'string') {
    if (typeof value !== 'string') reject();
    const length = [...value].length;
    if (length < (schema.minLength ?? 0) || length > (schema.maxLength ?? Infinity)) reject();
    if (schema.minLength && !value.trim()) reject();
  } else if (schema.type === 'integer') {
    if (!Number.isInteger(value) || value < (schema.minimum ?? -Infinity) || value > (schema.maximum ?? Infinity)) reject();
  } else if (schema.type === 'boolean' && typeof value !== 'boolean') reject();
  if (schema.enum && !schema.enum.includes(value)) reject();
}

function bindingKeys(context) {
  if (!isObject(context)) return [];
  const keys = [];
  if (typeof context.session_id === 'string' && context.session_id.trim()) {
    keys.push({ integration: 'cindy', kind: 'session', external_id: context.session_id });
  }
  if (context.workdir_is_local === true && typeof context.workdir === 'string' && isAbsolute(context.workdir)) {
    keys.push({
      integration: 'cindy', kind: 'workspace',
      external_id: createHash('sha256').update(resolve(context.workdir)).digest('hex'),
    });
  }
  return keys;
}

function authorizationFor(request, baseUrl) {
  const authenticated = request.params.config.auth_mode === 'bearer';
  if (authenticated !== (request.method === 'powercontext/authenticated')) reject('invalid_configuration');
  if (!authenticated) return undefined;
  let record;
  try { record = JSON.parse(request.cindy?.secrets?.server_credential); } catch { reject('authentication_failed'); }
  if (!isObject(record) || record.server_url !== baseUrl) reject('credential_url_mismatch');
  if (typeof record.authorization !== 'string') reject('authentication_failed');
  const token = record.authorization.trim().replace(/^Bearer /i, '');
  if (!/^[\x21-\x7e]{1,8192}$/.test(token) || token.toLowerCase() === 'bearer') reject('authentication_failed');
  return `Bearer ${token}`;
}

async function decodeResponse(response) {
  if (Number(response.headers.get('content-length')) > MAX_RESPONSE_BYTES) {
    await response.body?.cancel();
    reject('invalid_response');
  }
  const reader = response.body?.getReader();
  if (!reader) reject('invalid_response');
  const chunks = [];
  let bytes = 0;
  while (true) {
    const chunk = await reader.read();
    if (chunk.done) break;
    bytes += chunk.value.byteLength;
    if (bytes > MAX_RESPONSE_BYTES) {
      await reader.cancel();
      reject('invalid_response');
    }
    chunks.push(chunk.value);
  }
  try {
    const text = new TextDecoder('utf-8', { fatal: true }).decode(Buffer.concat(chunks));
    const value = JSON.parse(text);
    if (!isObject(value)) reject('invalid_response');
    return value;
  } catch { reject('invalid_response'); }
}

async function http(baseUrl, authorization, signal, method, path, body) {
  const headers = { Accept: 'application/json', 'User-Agent': `powercontext-cindy/${manifest.version}` };
  if (authorization) headers.Authorization = authorization;
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  const response = await fetch(baseUrl + path, {
    method, headers, signal, redirect: 'manual',
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  if (!response.ok) {
    await response.body?.cancel();
    const compatibility = path === '/health/ready' || path === '/v1/capabilities';
    const codes = { 401: 'authentication_failed', 403: 'forbidden', 404: compatibility ? 'version_mismatch' : 'not_found',
      409: 'conflict', 422: 'invalid_request', 503: 'server_unavailable' };
    throw new OperationError(codes[response.status] ?? 'invalid_response', response.status);
  }
  return decodeResponse(response);
}

function preparedContext(value, maxBytes) {
  const fields = ['schema', 'status', 'content', 'content_bytes'];
  if (Object.keys(value).length !== 4 || fields.some((key) => !Object.hasOwn(value, key))
    || value.schema !== 'powercontext.prepared-context.v1' || !Number.isInteger(value.content_bytes)) {
    reject('invalid_response');
  }
  if (value.status === 'empty') {
    if (value.content !== null || value.content_bytes !== 0) reject('invalid_response');
  } else if (value.status !== 'ready' || typeof value.content !== 'string' || !value.content.trim()
    || Buffer.byteLength(value.content, 'utf8') !== value.content_bytes || value.content_bytes > maxBytes) {
    reject('invalid_response');
  }
  return value;
}

async function execute(request) {
  if (!isObject(request) || !isObject(request.params)) reject();
  if (!['powercontext/request', 'powercontext/authenticated'].includes(request.method)) reject('unknown_method');
  const { config, tool, args = {}, session_context: context } = request.params ?? {};
  const definition = manifest.tools.find((entry) => entry.name === tool);
  if (!definition || !Object.hasOwn(OPERATIONS, tool)) reject('unknown_tool');
  validate(args, definition.parameters);
  if (!isObject(config) || !['none', 'bearer'].includes(config.auth_mode)) reject('invalid_configuration');
  const baseUrl = normalizeUrl(config.server_url, config.allow_insecure_http ?? false);
  const authorization = authorizationFor(request, baseUrl);
  if (config.scope_id !== undefined && (typeof config.scope_id !== 'string' || !config.scope_id.trim())) {
    reject('invalid_configuration');
  }
  const timeoutMs = config.timeout_ms ?? 10000;
  if (!Number.isInteger(timeoutMs) || timeoutMs < 1000 || timeoutMs > 30000) reject('invalid_configuration');
  const signal = AbortSignal.timeout(timeoutMs);
  const call = (method, path, body) => http(baseUrl, authorization, signal, method, path, body);
  const resolveScope = async () => {
    const scope = await call('POST', OPERATIONS.resolve_scope[1], {
      binding_keys: bindingKeys(context),
      allow_default: false,
      ...(config.scope_id ? { explicit_scope_id: config.scope_id } : {}),
    });
    if (typeof scope.scope_id !== 'string' || !scope.scope_id.trim()) reject('invalid_response');
    return scope;
  };
  if (tool === 'resolve_scope') return resolveScope();
  let payload = { ...args };
  if (tool === 'bind_scope') {
    const key = bindingKeys(context).find((entry) => entry.kind === (args.kind ?? 'session'));
    if (!key) reject('session_context_unavailable');
    payload = { key, scope_id: args.scope_id };
  }
  if (SCOPED.has(tool)) payload.scope_id = (await resolveScope()).scope_id;
  if (tool === 'remember_memory' && Buffer.byteLength(args.text, 'utf8') > 8192) reject();
  const [method, path] = OPERATIONS[tool];
  const query = tool === 'list_scopes' ? new URLSearchParams(args).toString() : '';
  const value = await call(method, query ? `${path}?${query}` : path, method === 'GET' ? undefined : payload);
  if (tool === 'status') {
    if (!['ready', 'degraded'].includes(value.status) || !isObject(value.checks)) reject('invalid_response');
    const capabilities = await call('GET', '/v1/capabilities');
    if (!Array.isArray(capabilities.source_types) || !Array.isArray(capabilities.artifact_families)
      || !Array.isArray(capabilities.search_modes) || !Array.isArray(capabilities.context_versions)
      || typeof capabilities.memory_extraction !== 'boolean' || typeof capabilities.handoff_generation !== 'boolean') {
      reject('invalid_response');
    }
    return { readiness: value, capabilities };
  }
  if (tool === 'prepare_context') return preparedContext(value, args.max_bytes ?? 8000);
  if (tool === 'list_scopes' && !Array.isArray(value.items)) reject('invalid_response');
  if (tool === 'create_scope' && (typeof value.scope_id !== 'string' || !value.scope_id.trim())) reject('invalid_response');
  if (tool === 'bind_scope' && (value.scope_id !== args.scope_id || !isObject(value.key))) reject('invalid_response');
  if (tool === 'search_memory' && !Array.isArray(value.hits)) reject('invalid_response');
  if (tool === 'remember_memory' && !isObject(value.memory)) reject('invalid_response');
  if (tool === 'capture_content_source' && (value.status !== 'accepted' || !isObject(value.source)
    || !Number.isInteger(value.position) || value.position < 1)) reject('invalid_response');
  return value;
}

function failure(error, tool) {
  const code = error instanceof OperationError ? error.code : 'server_unavailable';
  const status = error instanceof OperationError ? error.status : undefined;
  return {
    ok: false, code, message: 'PowerContext operation failed. Check plugin settings and continue the task.',
    ...(status === undefined ? {} : { http_status: status }),
    ...(code === 'server_unavailable' ? { recovery: 'powercontext doctor' } : {}),
    // A lost HTTP response cannot establish whether a write committed. Never retry it automatically.
    ...(WRITES.has(tool) && ['server_unavailable', 'invalid_response'].includes(code) ? { write_status: 'unknown' } : {}),
  };
}

async function handleRequest(request) {
  try { return { ok: true, data: await execute(request) }; }
  catch (error) { return failure(error, request?.params?.tool); }
}

function startWorker() {
  const lines = createInterface({ input: process.stdin, crlfDelay: Infinity });
  let pending = 0;
  const reply = (value) => process.stdout.write(`${JSON.stringify(value)}\n`);
  lines.on('line', async (line) => {
    let request;
    try {
      if (Buffer.byteLength(line) > MAX_LINE_BYTES) throw new Error('request too large');
      request = JSON.parse(line);
    } catch {
      reply({ jsonrpc: '2.0', id: null, error: { code: -32700, message: 'Invalid JSON request' } });
      return;
    }
    if (!isObject(request) || request.jsonrpc !== '2.0' || typeof request.method !== 'string') {
      reply({ jsonrpc: '2.0', id: null, error: { code: -32600, message: 'Invalid request' } });
      return;
    }
    // Notifications have no response and must not trigger durable operations.
    if (!Object.hasOwn(request, 'id')) return;
    if (typeof request.id !== 'string' && typeof request.id !== 'number') {
      reply({ jsonrpc: '2.0', id: null, error: { code: -32600, message: 'Invalid request ID' } });
      return;
    }
    if (pending >= 4) {
      reply({ jsonrpc: '2.0', id: request.id, result: failure(new OperationError('busy')) });
      return;
    }
    pending += 1;
    try { reply({ jsonrpc: '2.0', id: request.id, result: await handleRequest(request) }); }
    finally { pending -= 1; }
  });
}

module.exports = { handleRequest };
if (require.main === module) startWorker();
