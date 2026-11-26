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

import type {
  CheckReport,
  DesktopState,
  ProfileView,
  ScopeDescriptor,
} from "../generated/ipc";
import type { Language } from "./messages";
import type { ServiceAgent } from "./service-agent-model";

export const previewLocalConnectionId = "preview-local-service";
export const previewRemoteConnectionId = "preview-remote-service";
export const previewDataScopeId = "preview-default-space";
export const previewEmptyScopeId = "preview-empty-space";
export const previewWorkspaceMemoryCount = import.meta.env.DEV ? 277 : 0;

export function previewServiceAgents(
  language: Language,
): Record<string, ServiceAgent[]> {
  if (!import.meta.env.DEV) return {};
  const zh = language === "zh";
  return {
    [previewLocalConnectionId]: [
      {
        id: "preview-agent-codex",
        name: "Codex",
        project: "PowerContext Desktop",
        space: zh ? "OCP产品研发" : "OCP Product Development",
        lastWrite: zh ? "最近写入 10 分钟前" : "Last write 10m ago",
      },
      {
        id: "preview-agent-cursor",
        name: "Cursor",
        project: zh ? "OCP 产品研发" : "OCP Product Development",
        space: zh ? "OCP产品研发" : "OCP Product Development",
        lastWrite: zh ? "最近写入 1 小时前" : "Last write 1h ago",
      },
    ],
  };
}

export function previewDisconnectedState(
  state: DesktopState | null,
): DesktopState {
  if (!import.meta.env.DEV)
    throw new Error("Development preview is unavailable in production.");
  return {
    generation: Math.max(1, state?.generation ?? 0),
    profiles: [],
    reports: [],
    active: null,
    compatibilityProfiles: [],
    pendingCredentialCleanup: 0,
    lastWrite: null,
  };
}

export function previewProfiles(language: Language): ProfileView[] {
  if (!import.meta.env.DEV) return [];
  const zh = language === "zh";
  return [
    {
      id: previewLocalConnectionId,
      revision: 1,
      name: zh ? "本机服务" : "Local service",
      endpoint: "http://127.0.0.1:8000",
      authentication: "unauthenticated_loopback",
      caPem: null,
      compatibility: null,
      credentialState: "not_required",
    },
    {
      id: previewRemoteConnectionId,
      revision: 1,
      name: zh ? "团队远程服务" : "Team remote service",
      endpoint: "https://powercontext.example.com",
      authentication: "bearer",
      caPem: null,
      compatibility: null,
      credentialState: "stored",
    },
  ];
}

export function previewScopes(language: Language): ScopeDescriptor[] {
  if (!import.meta.env.DEV) return [];
  const zh = language === "zh";
  return [
    {
      scope_id: previewEmptyScopeId,
      title: zh ? "默认空间" : "Default space",
      summary: zh ? "暂无数据" : "No data yet",
      parent_scope_id: null,
      context_references: [],
      external_references: [],
      version: 1,
    },
    {
      scope_id: previewDataScopeId,
      title: zh ? "OCP产品研发" : "OCP Product Development",
      summary: zh
        ? `已有 ${previewWorkspaceMemoryCount} 条记忆`
        : `Contains ${previewWorkspaceMemoryCount} memories`,
      parent_scope_id: null,
      context_references: [],
      external_references: [],
      version: 1,
    },
  ];
}

function previewReport(profile: ProfileView): CheckReport {
  return {
    connectionId: profile.id,
    revision: profile.revision,
    checkedAt: Date.now(),
    liveness: { value: { status: "ok" }, error: null },
    readiness: { value: { status: "ready", checks: {} }, error: null },
    identity: { value: null, error: null },
    capabilities: { value: null, error: null },
    compatibilityVerified: true,
    anonymousAccess: profile.authentication === "unauthenticated_loopback",
    supportedOperations: [],
  };
}

export function previewDesktopState(
  state: DesktopState | null,
  language: Language,
  connectionId = previewLocalConnectionId,
  scopeId = previewEmptyScopeId,
  connectionIds?: readonly string[],
): DesktopState {
  if (!import.meta.env.DEV)
    throw new Error("Development preview is unavailable in production.");
  const profiles = previewProfiles(language).filter(
    (profile) => !connectionIds || connectionIds.includes(profile.id),
  );
  if (profiles.length === 0) return previewDisconnectedState(state);
  const reports = profiles.map(previewReport);
  const activeProfile =
    profiles.find((profile) => profile.id === connectionId) ?? profiles[0];
  const report = reports.find(
    (candidate) => candidate.connectionId === activeProfile.id,
  )!;
  // Every connection has a default Space. Preview connections must follow the
  // same invariant so switching services never falls back to the legacy scope
  // selection guide.
  const scope =
    previewScopes(language).find((item) => item.scope_id === scopeId) ??
    previewScopes(language)[0];
  const generation = Math.max(1, state?.generation ?? 0);
  return {
    generation,
    profiles,
    reports,
    active: {
      connectionId: activeProfile.id,
      generation,
      report,
      scope,
    },
    compatibilityProfiles: [],
    pendingCredentialCleanup: 0,
    lastWrite: state?.lastWrite ?? null,
  };
}
