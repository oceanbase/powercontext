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

/** Optional propagation from a trusted, in-process companion. Never accept arbitrary headers or baggage. */
export interface TraceContext {
  traceparent: string
}

/** Invalid observation metadata is ignored without changing memory behavior. */
export function traceContextHeaders(value: unknown): Record<string, string> {
  const parent = (value as TraceContext | undefined)?.traceparent
  if (typeof parent !== 'string') return {}
  const match = /^00-([a-f0-9]{32})-([a-f0-9]{16})-([a-f0-9]{2})$/.exec(parent)
  if (!match || /^0+$/.test(match[1]) || /^0+$/.test(match[2])) return {}
  return { traceparent: parent }
}
