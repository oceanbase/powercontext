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

import type { DesktopState } from "../generated/ipc";
import type { MemorySubmissions } from "./memory-submissions";
import type { Language } from "./messages";
import { ConnectionRequiredGuide } from "./ConnectionRequiredGuide";
import { MemoryWorkspaceContent } from "./MemoryWorkspaceContent";
import {
  previewDataScopeId,
  previewWorkspaceMemoryCount,
} from "./preview-data";

type Props = {
  state: DesktopState | null;
  language: Language;
  onState?: (state: DesktopState) => void;
  submissions?: MemorySubmissions;
  onManageConnections?: () => void;
  onOpenExistingServer?: () => void;
  onOpenLocalServer?: () => void;
  contextPending?: boolean;
  preview?: boolean;
};

/**
 * The memory route has one UI. During frontend development the active scope
 * only supplies fixture data; it never selects a different page implementation.
 */
export function MemoryWorkspace({
  state,
  language,
  onState,
  submissions,
  onManageConnections,
  onOpenExistingServer,
  onOpenLocalServer,
  contextPending = false,
  preview = false,
}: Props) {
  if (!state || contextPending) return null;

  const active = state.active;
  if (!active?.scope) {
    return (
      <ConnectionRequiredGuide
        title={
          language === "zh"
            ? "暂无可访问的记忆空间"
            : "No accessible memory spaces"
        }
        language={language}
        onDeploy={onOpenLocalServer}
        onConnect={onOpenExistingServer ?? onManageConnections ?? (() => {})}
      />
    );
  }

  const activeProfile = state.profiles.find(
    (profile) => profile.id === active.connectionId,
  );
  const fixtureCount =
    preview && active.scope.scope_id === previewDataScopeId
      ? previewWorkspaceMemoryCount
      : 0;

  return (
    <MemoryWorkspaceContent
      key={preview ? "preview" : active.generation}
      language={language}
      scopeId={active.scope.scope_id}
      scopeTitle={active.scope.title}
      serviceName={activeProfile?.name ?? active.connectionId}
      serviceEndpoint={activeProfile?.endpoint ?? "—"}
      initialCount={fixtureCount}
      nativeContext={preview ? undefined : active}
      onState={onState}
      submissions={submissions}
    />
  );
}
