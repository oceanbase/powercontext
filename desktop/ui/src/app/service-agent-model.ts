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

import type { DesktopState, ProfileView } from "../generated/ipc";
import { connectionCheckReport, connectionVerified } from "./context";

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
      hostname === "localhost" ||
      /^127\./.test(hostname) ||
      hostname === "[::1]"
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
      const report = connectionCheckReport(state, profile.id);
      return {
        profile,
        kind: isLocalProfile(profile) ? "local" : "remote",
        running: runningByServiceId[profile.id] ?? connectionVerified(report),
        simulated,
        agents: [...(agentsByServiceId[profile.id] ?? [])],
      };
    }),
  };
}
