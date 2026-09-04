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

import { createHash } from "node:crypto";
import type { PowerContextConfig } from "./config.js";
import type { PowerContextClient } from "./http.js";

export type OpenClawScopeIdentity = {
  agentId: string;
  sessionKey?: string;
  activeProjectKeys?: readonly string[];
};

export type ScopeBindingKey = {
  integration: "openclaw";
  kind: "session" | "project" | "agent";
  external_id: string;
};

type ScopeDescriptor = {
  scope_id: string;
};

function normalized(value: string | undefined): string | undefined {
  const result = value?.trim();
  return result || undefined;
}

function opaqueExternalId(value: string): string {
  return createHash("sha256").update(value).digest("hex");
}

export function scopeBindingKeys(identity: OpenClawScopeIdentity): ScopeBindingKey[] {
  const keys: ScopeBindingKey[] = [];
  const sessionKey = normalized(identity.sessionKey);
  if (sessionKey) {
    keys.push({ integration: "openclaw", kind: "session", external_id: opaqueExternalId(sessionKey) });
  }

  const projectIds = new Set<string>();
  for (const projectKey of identity.activeProjectKeys ?? []) {
    const project = normalized(projectKey);
    if (!project) {
      continue;
    }
    const externalId = opaqueExternalId(project);
    if (!projectIds.has(externalId)) {
      projectIds.add(externalId);
      keys.push({ integration: "openclaw", kind: "project", external_id: externalId });
    }
  }

  const agentId = normalized(identity.agentId);
  if (agentId) {
    keys.push({ integration: "openclaw", kind: "agent", external_id: agentId });
  }
  return keys;
}

export async function resolvePowerContextScope(
  client: PowerContextClient,
  config: PowerContextConfig,
  identity: OpenClawScopeIdentity,
  signal?: AbortSignal,
): Promise<string> {
  const descriptor = await client.post<ScopeDescriptor>(
    "/v1/scope-bindings/resolve",
    {
      ...(config.scopeId ? { explicit_scope_id: config.scopeId } : {}),
      binding_keys: scopeBindingKeys(identity),
    },
    signal,
  );
  const scopeId = normalized(descriptor?.scope_id);
  if (!scopeId) {
    throw new Error("PowerContext returned an invalid Scope binding");
  }
  return scopeId;
}
