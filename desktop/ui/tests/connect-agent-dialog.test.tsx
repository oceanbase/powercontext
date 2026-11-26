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

import { afterEach, expect, test, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { ConnectAgentDialog } from "../src/app/Overview";
import { desktopApi } from "../src/shared/ipc";
import type { DesktopState, ScopeDescriptor } from "../src/generated/ipc";

vi.mock("@tauri-apps/api/core", () => ({
  isTauri: () => true,
  invoke: vi.fn(),
}));

vi.mock("../src/shared/ipc", () => ({
  desktopApi: {
    defaultScope: vi.fn(),
    scopes: vi.fn(),
    cancelScopes: vi.fn().mockResolvedValue(undefined),
    check: vi.fn(),
  },
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const defaultScope: ScopeDescriptor = {
  scope_id: "default-space",
  title: "默认空间",
  summary: "",
  version: 1,
  context_references: [],
  external_references: [],
};

const emptyFact = { value: null, error: null };
const report = {
  connectionId: "local",
  revision: 1,
  checkedAt: 1,
  liveness: emptyFact,
  readiness: emptyFact,
  identity: emptyFact,
  capabilities: emptyFact,
  compatibilityVerified: true,
  anonymousAccess: true,
  supportedOperations: ["list_scopes", "get_scope"],
};

const state: DesktopState = {
  generation: 1,
  profiles: [
    {
      id: "local",
      revision: 1,
      name: "本机 PowerContext 服务",
      endpoint: "http://127.0.0.1:8000",
      authentication: "unauthenticated_loopback",
      caPem: null,
      compatibility: null,
      credentialState: "not_required",
    },
  ],
  reports: [],
  active: {
    connectionId: "local",
    generation: 1,
    scope: null,
    report,
  },
  compatibilityProfiles: [],
  pendingCredentialCleanup: 0,
  lastWrite: null,
};

test("automatically selects the service default memory space", async () => {
  vi.mocked(desktopApi.defaultScope).mockResolvedValue(defaultScope);
  vi.mocked(desktopApi.scopes).mockResolvedValue({
    items: [],
    next_cursor: null,
  });

  render(
    <ConnectAgentDialog
      state={state}
      language="zh"
      onClose={vi.fn()}
      onConnected={vi.fn()}
    />,
  );

  const spaceSelect = screen.getByLabelText("记忆空间");
  await waitFor(() => expect(spaceSelect.textContent).toContain("默认空间"));
  expect(spaceSelect.textContent).not.toContain("请选择记忆空间");
});
