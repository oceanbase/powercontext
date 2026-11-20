/* Copyright (c) 2026 OceanBase. Licensed under the Apache License, Version 2.0. */

import type { DesktopState } from "../generated/ipc";
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
  onManageConnections?: () => void;
  onOpenExistingServer?: () => void;
  onOpenLocalServer?: () => void;
  contextPending?: boolean;
};

/**
 * The memory route has one UI. During frontend development the active scope
 * only supplies fixture data; it never selects a different page implementation.
 */
export function MemoryWorkspace({
  state,
  language,
  onManageConnections,
  onOpenExistingServer,
  onOpenLocalServer,
  contextPending = false,
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
    import.meta.env.DEV && active.scope.scope_id === previewDataScopeId
      ? previewWorkspaceMemoryCount
      : 0;

  return (
    <MemoryWorkspaceContent
      language={language}
      scopeId={active.scope.scope_id}
      scopeTitle={active.scope.title}
      serviceName={activeProfile?.name ?? active.connectionId}
      serviceEndpoint={activeProfile?.endpoint ?? "—"}
      initialCount={fixtureCount}
    />
  );
}
