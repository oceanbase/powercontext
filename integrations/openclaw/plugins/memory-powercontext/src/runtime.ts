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


import type { MemoryPluginRuntime } from "openclaw/plugin-sdk/memory-core-host-runtime-core";
import type { PowerContextConfig } from "./config.js";
import type { PowerContextClient } from "./http.js";
import { PowerContextMemoryManager } from "./manager.js";

export function createPowerContextMemoryRuntime(params: {
  getConfig: () => PowerContextConfig;
  client: PowerContextClient;
  isPrivateSession: (agentId: string, sessionKey: string | undefined) => boolean;
  managerFor?: (agentId: string) => PowerContextMemoryManager;
  removeManager?: (agentId: string) => void;
  clearManagers?: () => void;
}): MemoryPluginRuntime {
  const managers = new Map<string, PowerContextMemoryManager>();
  const managerFor = (agentId: string) => {
    if (params.managerFor) {
      return params.managerFor(agentId);
    }
    let manager = managers.get(agentId);
    if (!manager) {
      manager = new PowerContextMemoryManager(
        agentId,
        params.getConfig,
        params.client,
        params.isPrivateSession,
      );
      managers.set(agentId, manager);
    }
    return manager;
  };
  return {
    async getMemorySearchManager({ agentId, purpose }) {
      if (!params.getConfig().endpoint) {
        return { manager: null, error: "PowerContext endpoint is not configured" };
      }
      const manager = managerFor(agentId);
      return {
        manager,
        debug: { backend: "builtin", purpose: purpose ?? "default", managerMs: 0 },
      };
    },
    resolveMemoryBackendConfig() {
      return { backend: "builtin" };
    },
    async authorizeSearchHits({ agentId, hits, requesterSessionKey }) {
      if (!params.isPrivateSession(agentId, requesterSessionKey)) {
        return [];
      }
      return hits.filter((hit) => hit.source !== "sessions");
    },
    async closeMemorySearchManager({ agentId }) {
      if (params.managerFor) {
        params.removeManager?.(agentId);
        return;
      }
      managers.delete(agentId);
    },
    async closeAllMemorySearchManagers() {
      if (params.clearManagers) {
        params.clearManagers();
      } else {
        managers.clear();
      }
    },
  };
}
