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

const HTTP_METHODS = ['get', 'post', 'put', 'patch', 'delete']

export function resolveRef(doc, ref, seen = new Set()) {
  if (typeof ref !== 'string' || !ref.startsWith('#/')) return undefined
  if (seen.has(ref)) return undefined
  seen.add(ref)
  let current = doc
  for (const raw of ref.slice(2).split('/')) {
    const key = raw.replaceAll('~1', '/').replaceAll('~0', '~')
    current = current?.[key]
  }
  return current
}

export function deref(doc, node, seen = new Set()) {
  if (!node || typeof node !== 'object') return node
  if (typeof node.$ref === 'string') {
    return deref(doc, resolveRef(doc, node.$ref, seen), seen)
  }
  return node
}

export function schemaHasScope(doc, schema, seen = new Set()) {
  const resolved = deref(doc, schema, seen)
  if (!resolved || typeof resolved !== 'object') return false
  if (resolved.properties && Object.hasOwn(resolved.properties, 'scope_id')) return true
  for (const key of ['allOf', 'oneOf', 'anyOf']) {
    const parts = resolved[key]
    if (!Array.isArray(parts)) continue
    if (parts.some((part) => schemaHasScope(doc, part, new Set(seen)))) return true
  }
  return false
}

function jsonBodySchema(doc, operation) {
  const body = deref(doc, operation.requestBody)
  return body?.content?.['application/json']?.schema
}

function operationParameters(doc, pathItem, operation) {
  const listed = [...(pathItem.parameters ?? []), ...(operation.parameters ?? [])]
  return listed.map((item) => deref(doc, item)).filter(Boolean)
}

function requestLocation(bodySchema, parameters) {
  if (bodySchema) return 'body'
  if (parameters.some((parameter) => parameter.in === 'query')) return 'query'
  return null
}

function operationHasScope(doc, bodySchema, parameters) {
  if (bodySchema && schemaHasScope(doc, bodySchema)) return true
  return parameters.some((parameter) => parameter.in === 'query' && parameter.name === 'scope_id')
}

export function parseOperations(doc) {
  const rows = []
  for (const [path, pathItem] of Object.entries(doc.paths ?? {})) {
    if (!pathItem || typeof pathItem !== 'object') continue
    for (const method of HTTP_METHODS) {
      const operation = pathItem[method]
      if (!operation?.operationId) continue
      const parameters = operationParameters(doc, pathItem, operation)
      const bodySchema = jsonBodySchema(doc, operation)
      rows.push({
        operationId: operation.operationId,
        method: method.toUpperCase(),
        path,
        location: requestLocation(bodySchema, parameters),
        scope: operationHasScope(doc, bodySchema, parameters),
      })
    }
  }
  return rows
}

export function renderOperationsSource(rows) {
  const body = rows
    .map((row) => {
      const location = row.location === null ? 'null' : `"${row.location}"`
      return `  ${row.operationId}: { method: '${row.method}', path: '${row.path}', location: ${location}, scope: ${row.scope} },`
    })
    .join('\n')
  return `/*
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

// generated from openapi/powercontext.yaml; do not edit.

export const OPERATIONS = {
${body}
} as const

export type OperationId = keyof typeof OPERATIONS

export type OperationSpec = (typeof OPERATIONS)[OperationId]

export const OPERATION_IDS = Object.keys(OPERATIONS) as OperationId[]
`
}
