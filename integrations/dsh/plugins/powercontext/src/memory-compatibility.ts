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

// A semantic contract, independent of the plugin package's own version.
export const MEMORY_PROTOCOL_RECOVERY = "Update the PowerContext CLI/SDK and plugin to a release compatible with the Server; run powercontext setup dsh for the affected profile, then fully restart the host and rerun Doctor. Search again for an Atomic Memory artifact reference before retrying. Do not drop collection CAS preconditions or automatically replay writes."

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

export function memoryProtocolCheck(contracts: unknown, projection: 'openapi' | 'server-info' = 'server-info') {
  const memory = record(contracts) ? contracts['memory.explicit'] : undefined
  const version = record(memory) ? projection === 'openapi' ? memory : memory.version : undefined
  const compatible = record(version) && version.major === 1
    && Number.isInteger(version.minor) && Number(version.minor) >= 0
  return compatible
    ? { ok: true, code: 'memory_protocol_compatible', message: 'The Server declares Atomic Memory protocol v1. Write behavior was not tested.' }
    : { ok: false, code: 'memory_protocol_mismatch',
      message: 'The Server does not declare the Atomic Memory protocol required by this plugin. Health checks alone do not establish compatibility.',
      recovery: MEMORY_PROTOCOL_RECOVERY }
}
