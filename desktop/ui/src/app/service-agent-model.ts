/* Copyright (c) 2026 OceanBase. Licensed under the Apache License, Version 2.0. */

import type { DesktopState, ProfileView } from "../generated/ipc";

export type ServiceAgent = {
  id: string;
  name: string;
  project: string;
  space: string;
  lastWrite: string;
};

export type ServiceAgentService = {
  profile: ProfileView;
  kind: "local" | "remote";
  running: boolean;
  simulated: boolean;
  agents: ServiceAgent[];
};

export type ServiceAgentWorkspaceModel = {
  simulated: boolean;
  services: ServiceAgentService[];
};

type ModelOptions = {
  simulated?: boolean;
  agentsByServiceId?: Readonly<Record<string, readonly ServiceAgent[]>>;
  runningByServiceId?: Readonly<Record<string, boolean>>;
};

function isLocalProfile(profile: ProfileView) {
  try {
    const hostname = new URL(profile.endpoint).hostname;
    return (
      hostname === "localhost" || hostname === "127.0.0.1" || hostname === "::1"
    );
  } catch {
    return false;
  }
}

export function buildServiceAgentWorkspaceModel(
  state: DesktopState | null,
  {
    simulated = false,
    agentsByServiceId = {},
    runningByServiceId = {},
  }: ModelOptions = {},
): ServiceAgentWorkspaceModel {
  return {
    simulated,
    services: (state?.profiles ?? []).map((profile) => {
      const report = state?.reports.find(
        (candidate) => candidate.connectionId === profile.id,
      );
      const reportReady =
        !report?.liveness.error &&
        !report?.readiness.error &&
        report?.readiness.value?.status === "ready";
      return {
        profile,
        kind: isLocalProfile(profile) ? "local" : "remote",
        running: runningByServiceId[profile.id] ?? reportReady,
        simulated,
        agents: [...(agentsByServiceId[profile.id] ?? [])],
      };
    }),
  };
}
