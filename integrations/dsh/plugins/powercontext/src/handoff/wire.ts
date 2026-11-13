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

import { createHash } from 'node:crypto'
import { HANDOFF_HTTP_SCHEMAS } from './http.generated.ts'
import { DeliveryError } from './contracts.ts'

type Schemas = typeof HANDOFF_HTTP_SCHEMAS
type SchemaName = keyof Schemas
type Shape<P, R> = { [K in keyof P as K extends R ? K : never]: Value<P[K]> }
  & { [K in keyof P as K extends R ? never : K]?: Value<P[K]> }
type Value<S> = S extends { nullable: true } ? NonNull<S> | null : NonNull<S>
type NonNull<S> =
  S extends { $ref: infer K extends SchemaName } ? Wire<K>
    : S extends { enum: readonly (infer E)[] } ? E
      : S extends { oneOf: readonly (infer O)[] } ? Value<O>
        : S extends { type: 'object'; properties: infer P; required: readonly (infer R)[] } ? Shape<P, R>
          : S extends { type: 'array'; items: infer I } ? Value<I>[]
            : S extends { type: 'string' } ? string
              : S extends { type: 'integer' } ? number
                : never

export type Wire<K extends SchemaName> = Value<Schemas[K]>

type Schema = {
  $ref?: SchemaName
  type?: 'object' | 'array' | 'string' | 'integer'
  nullable?: boolean
  enum?: readonly string[]
  oneOf?: readonly Schema[]
  properties?: Record<string, Schema>
  required?: readonly string[]
  items?: Schema
  minLength?: number
  maxLength?: number
  minItems?: number
  maxItems?: number
  minimum?: number
  pattern?: string
  'x-powercontext-source-line-range'?: boolean
}

export function object(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

export function invalid(): never {
  throw new DeliveryError('payload_invalid')
}

export function trimmed(value: unknown, max: number): value is string {
  return typeof value === 'string' && value.length > 0 && value === value.trim()
    && [...value].length <= max && !/[\uD800-\uDFFF]/u.test(value)
}

function project(schema: Schema, value: unknown, core = false, name?: SchemaName): unknown {
  if (value === null && schema.nullable) return null
  if (schema.$ref) return project(HANDOFF_HTTP_SCHEMAS[schema.$ref], value, core, schema.$ref)
  if (schema.oneOf) {
    for (const option of schema.oneOf) {
      try {
        return project(option, value, core)
      } catch (error) {
        if (!(error instanceof DeliveryError)) throw error
      }
    }
    return invalid()
  }
  if (schema.type === 'object') {
    const properties = schema.properties!
    const required = schema.required!
    if (!object(value) || Object.keys(value).some(key => !Object.hasOwn(properties, key))
      || required.some(key => !Object.hasOwn(value, key))) invalid()
    const entries = []
    for (const [key, field] of Object.entries(properties)) {
      if (!Object.hasOwn(value, key)) continue
      const child = project(field, value[key], core)
      // Core omits null generation, Source locator and line heading (Pydantic exclude_if).
      if (core && child === null && !required.includes(key)) continue
      entries.push([core && name === 'SourceReference' && key === 'name' ? 'source_type' : key, child])
    }
    if (schema['x-powercontext-source-line-range']) {
      const lines = Number(value.end_line) - Number(value.start_line) + 1
      if (lines < 1 || lines > 200) invalid()
    }
    if (name === 'SourceReference' && (!trimmed(value.name, 256) || !trimmed(value.source_id, 256))) invalid()
    return Object.fromEntries(entries)
  }
  if (schema.type === 'array') {
    if (!Array.isArray(value) || value.length < (schema.minItems ?? 0)
      || value.length > (schema.maxItems ?? Infinity)) invalid()
    return Array.from(value, item => project(schema.items!, item, core))
  }
  if (schema.type === 'integer') {
    if (typeof value !== 'number' || !Number.isSafeInteger(value) || value < (schema.minimum ?? 0)) invalid()
    return value
  }
  if (typeof value !== 'string' || /[\uD800-\uDFFF]/u.test(value)) invalid()
  if (schema.maxLength !== undefined && [...value].length > schema.maxLength) invalid()
  if (schema.minLength !== undefined && [...value].length < schema.minLength) invalid()
  if (schema.pattern && !new RegExp(schema.pattern, 'u').test(value)) invalid()
  if (schema.enum && !schema.enum.includes(value)) invalid()
  return value
}

export function wire<K extends SchemaName>(name: K, value: unknown): Wire<K> {
  return project(HANDOFF_HTTP_SCHEMAS[name], value, false, name) as Wire<K>
}

export function sha256(value: string | Uint8Array): string {
  return createHash('sha256').update(value).digest('hex')
}

export function preparedDigest(value: Wire<'PreparedHandoff'>): string {
  // Core's work.content_digest is model-order JSON, not sorted keys / JCS.
  const core = project(HANDOFF_HTTP_SCHEMAS.PreparedHandoff, value, true, 'PreparedHandoff')
  return 'sha256:' + sha256(JSON.stringify(core))
}
