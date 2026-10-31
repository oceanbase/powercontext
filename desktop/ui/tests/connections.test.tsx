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

import { afterEach, beforeEach, expect, test, vi } from "vitest";
import {
  act,
  cleanup,
  render,
  screen,
  within,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { answerConfirmation } from "./confirmation";
import { App } from "../src/app/App";
import { Connections } from "../src/app/Connections";
import { desktopApi } from "../src/shared/ipc";
import type {
  DesktopState,
  DiscoveryResult,
  ProfileView,
} from "../src/generated/ipc";
import { contractSha256 } from "../src/generated/operations";
import qualifications from "../../src-tauri/src/connections/compatibility.json";
vi.mock("../src/shared/ipc", () => ({
  getFoundationInfo: vi.fn().mockResolvedValue(null),
  desktopApi: {
    remove: vi.fn(),
    invalidate: vi.fn(),
    state: vi.fn(),
    save: vi.fn(),
    check: vi.fn(),
  },
}));
const profile: ProfileView = {
  id: "desktop-test",
  revision: 1,
  name: "Original",
  endpoint: "https://example.test/",
  authentication: "bearer",
  caPem: null,
  compatibility: null,
  credentialState: "stored",
};
function state(p = profile): DesktopState {
  return {
    generation: 0,
    profiles: [p],
    reports: [],
    active: null,
    compatibilityProfiles: [],
    pendingCredentialCleanup: 0,
    lastWrite: null,
  };
}
function show(p = profile) {
  const onState = vi.fn();
  render(
    <Connections
      state={state(p)}
      language="en"
      onState={onState}
      onDirty={vi.fn()}
    />,
  );
  return onState;
}
const historicalCompatibility = qualifications.find(
  (p) => p.contractSha256 !== contractSha256,
)!;
const currentCompatibility = {
  ...historicalCompatibility,
  id: "synthetic-current-contract-ui-fixture",
  serverCommit: "synthetic-ui-fixture",
  contractSha256,
  artifactSha256: "synthetic-ui-fixture",
  evidence: "Synthetic UI fixture; no server qualification evidence.",
};
function qualifiedState(p = profile): DesktopState {
  return {
    ...state(p),
    compatibilityProfiles: [historicalCompatibility, currentCompatibility],
  };
}
function discoveryState(discovery: DiscoveryResult): DesktopState {
  const next = qualifiedState({
    ...profile,
    compatibility: currentCompatibility.id,
  });
  next.reports = [
    {
      connectionId: profile.id,
      revision: 1,
      checkedAt: 1,
      liveness: { value: null, error: null },
      readiness: { value: null, error: null },
      identity: { value: null, error: null },
      capabilities: { value: null, error: null },
      compatibilityVerified: discovery.status === "supported",
      supportedOperations:
        discovery.status === "supported" ? ["list_scopes"] : [],
      anonymousAccess: false,
      discovery,
    },
  ];
  return next;
}
beforeEach(() => {
  vi.clearAllMocks();
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});
const discoveryCases: Array<{
  language: "en" | "zh";
  status: DiscoveryResult["status"];
  reason: DiscoveryResult["reason"];
  expected: string;
}> = [
  {
    language: "en",
    status: "supported",
    reason: null,
    expected: "The discovery contract is supported",
  },
  {
    language: "en",
    status: "missing",
    reason: "discovery_missing",
    expected: "The Server has no discovery endpoint",
  },
  {
    language: "en",
    status: "denied",
    reason: "discovery_denied",
    expected: "Server discovery access was denied",
  },
  {
    language: "en",
    status: "unavailable",
    reason: "discovery_unavailable",
    expected: "Server discovery is temporarily unavailable",
  },
  {
    language: "en",
    status: "invalid",
    reason: "invalid_discovery",
    expected: "The Server discovery response is invalid",
  },
  {
    language: "en",
    status: "incompatible",
    reason: "api_version",
    expected: "The Server API major version is unsupported",
  },
  {
    language: "zh",
    status: "unavailable",
    reason: "discovery_unavailable",
    expected: "Server 发现暂时不可用。请检查服务和网络，然后重新检查连接",
  },
  {
    language: "zh",
    status: "denied",
    reason: "discovery_denied",
    expected: "Server 拒绝访问发现接口。请核对凭据与访问权限",
  },
];
test.each(discoveryCases)(
  "discovery $status has a distinct diagnostic in $language",
  ({ language, status, reason, expected }) => {
    render(
      <Connections
        state={discoveryState({
          status,
          reason,
          error: null,
          server: null,
          operations: {},
        })}
        language={language}
        onState={vi.fn()}
        onDirty={vi.fn()}
        initialProfileId={profile.id}
      />,
    );
    expect(screen.getByText(expected, { exact: false })).toBeTruthy();
  },
);
test("a successful recheck replaces the temporary discovery failure diagnostic", async () => {
  const user = userEvent.setup();
  const unavailable = discoveryState({
    status: "unavailable",
    reason: "discovery_unavailable",
    error: { code: "timeout", requestId: null, dispatched: true },
    server: null,
    operations: {},
  });
  const recovered = discoveryState({
    status: "supported",
    reason: null,
    error: null,
    server: null,
    operations: {},
  });
  const onState = vi.fn();
  const props = {
    language: "en" as const,
    onState,
    onDirty: vi.fn(),
    initialProfileId: profile.id,
  };
  vi.mocked(desktopApi.check).mockResolvedValue(recovered);
  const view = render(<Connections {...props} state={unavailable} />);
  expect(
    screen.getByText(/Server discovery is temporarily unavailable/).textContent,
  ).toContain("The request timed out.");
  await user.click(screen.getByRole("button", { name: "Check connection" }));
  expect(desktopApi.check).toHaveBeenCalledWith(profile.id, false);
  expect(onState).toHaveBeenCalledWith(recovered);
  view.rerender(<Connections {...props} state={recovered} />);
  expect(
    screen.queryByText(/Server discovery is temporarily unavailable/),
  ).toBeNull();
  expect(screen.getByText(/The discovery contract is supported/)).toBeTruthy();
  expect(screen.getByText("Verified")).toBeTruthy();
});
test("new connections offer only current-contract profiles and keep no selection by default", async () => {
  const user = userEvent.setup();
  render(
    <Connections
      state={qualifiedState()}
      language="en"
      onState={vi.fn()}
      onDirty={vi.fn()}
    />,
  );
  const selector = screen.getByLabelText(
    "Verified compatibility profile",
  ) as HTMLSelectElement;
  expect(selector.value).toBe("");
  expect(
    (
      within(selector).getByRole("option", {
        name: (text) => text.includes(historicalCompatibility.id),
      }) as HTMLOptionElement
    ).disabled,
  ).toBe(true);
  const current = within(selector).getByRole("option", {
    name: currentCompatibility.id,
  }) as HTMLOptionElement;
  expect(current.disabled).toBe(false);
  expect(current.closest("optgroup")?.label).toBe(
    "Available for this Desktop build",
  );
  await user.selectOptions(selector, current);
  expect(selector.value).toBe(currentCompatibility.id);
  expect(desktopApi.save).not.toHaveBeenCalled();
  expect(desktopApi.check).not.toHaveBeenCalled();
});
test("a saved historical profile shows the contract mismatch and can be explicitly replaced before rechecking", async () => {
  const user = userEvent.setup();
  const saved = { ...profile, compatibility: historicalCompatibility.id };
  const next = qualifiedState({
    ...saved,
    revision: 2,
    compatibility: currentCompatibility.id,
  });
  const onState = vi.fn();
  vi.mocked(desktopApi.save).mockResolvedValue(next);
  vi.mocked(desktopApi.check).mockResolvedValue(next);
  render(
    <Connections
      state={qualifiedState(saved)}
      language="en"
      onState={onState}
      onDirty={vi.fn()}
    />,
  );
  await user.click(screen.getByRole("button", { name: /Original/ }));
  const selector = screen.getByLabelText(
    "Verified compatibility profile",
  ) as HTMLSelectElement;
  expect(selector.value).toBe(historicalCompatibility.id);
  expect(screen.getByRole("alert").textContent).toMatch(
    /different API contract from this Desktop build/,
  );
  expect(desktopApi.save).not.toHaveBeenCalled();
  expect(desktopApi.check).not.toHaveBeenCalled();
  await user.selectOptions(selector, currentCompatibility.id);
  expect(screen.queryByRole("alert")).toBeNull();
  expect(
    (
      screen.getByRole("button", {
        name: "Check connection",
      }) as HTMLButtonElement
    ).disabled,
  ).toBe(true);
  await user.click(screen.getByRole("button", { name: "Save configuration" }));
  expect(desktopApi.save).toHaveBeenCalledWith(
    expect.objectContaining({
      id: saved.id,
      revision: saved.revision,
      compatibility: currentCompatibility.id,
    }),
  );
  expect(onState).toHaveBeenCalledWith(next);
  await user.click(screen.getByRole("button", { name: "Check connection" }));
  expect(desktopApi.check).toHaveBeenCalledWith(saved.id, false);
});
test("an unrecognized saved profile remains visible and requires explicit replacement", async () => {
  const user = userEvent.setup();
  render(
    <Connections
      state={qualifiedState({ ...profile, compatibility: "removed-profile" })}
      language="zh"
      onState={vi.fn()}
      onDirty={vi.fn()}
    />,
  );
  await user.click(screen.getByRole("button", { name: /Original/ }));
  const selector = screen.getByLabelText("已验证兼容配置") as HTMLSelectElement;
  expect(selector.value).toBe("removed-profile");
  expect(
    (
      within(selector).getByRole("option", {
        name: /removed-profile/,
      }) as HTMLOptionElement
    ).disabled,
  ).toBe(true);
  expect(screen.getByRole("alert").textContent).toMatch(
    /此 Desktop 未包含该兼容配置的验证记录/,
  );
  await user.selectOptions(selector, currentCompatibility.id);
  expect(selector.value).toBe(currentCompatibility.id);
  expect(screen.queryByRole("alert")).toBeNull();
  expect(desktopApi.save).not.toHaveBeenCalled();
});
test("canceling discard leaves the saved connection and its draft intact", async () => {
  const user = userEvent.setup();
  show();
  await user.click(screen.getByRole("button", { name: /Original/ }));
  await user.type(screen.getByLabelText("Connection name"), " draft");

  await user.click(screen.getByRole("button", { name: "Remove connection" }));
  await answerConfirmation(user, false);
  expect(desktopApi.remove).not.toHaveBeenCalled();
  expect(
    (screen.getByLabelText("Connection name") as HTMLInputElement).value,
  ).toBe("Original draft");
});
test("confirmed removal clears the form without asking to discard after deletion", async () => {
  const user = userEvent.setup();
  const onState = show();
  vi.mocked(desktopApi.remove).mockResolvedValue({
    ...state(),
    generation: 1,
    profiles: [],
  });
  await user.click(screen.getByRole("button", { name: /Original/ }));
  await user.type(screen.getByLabelText("Connection name"), " draft");

  await user.click(screen.getByRole("button", { name: "Remove connection" }));
  expect(screen.getByRole("alertdialog").textContent).toContain(
    "Discard unsaved input?",
  );
  await answerConfirmation(user, true);
  expect(screen.getByRole("alertdialog").textContent).toContain(
    "Remove only this Desktop configuration",
  );
  await answerConfirmation(user, true);
  expect(desktopApi.remove).toHaveBeenCalledWith(profile.id, 1);
  expect(onState).toHaveBeenCalled();
  expect(screen.queryByRole("alertdialog")).toBeNull();
  expect(
    (screen.getByLabelText("Connection name") as HTMLInputElement).value,
  ).toBe("");
});
test("missing credentials cannot be retained and entering a new one never activates a connection", async () => {
  const user = userEvent.setup();
  show({ ...profile, credentialState: "missing" });
  await user.click(screen.getByRole("button", { name: /Original/ }));
  expect(screen.queryByLabelText("Keep existing credential")).toBeNull();
  await user.type(
    screen.getByLabelText("New Bearer credential"),
    "synthetic-test-secret",
  );
  expect(desktopApi.check).not.toHaveBeenCalled();

  await user.click(screen.getByRole("button", { name: "Cancel editing" }));
  await answerConfirmation(user, true);
  expect(
    (screen.getByLabelText("New Bearer credential") as HTMLInputElement).value,
  ).toBe("");
});
test("retargeting immediately invalidates verification and prevents retaining the old credential", async () => {
  const user = userEvent.setup();
  show();
  vi.mocked(desktopApi.invalidate).mockResolvedValue({
    ...state(),
    generation: 1,
  });
  await user.click(screen.getByRole("button", { name: /Original/ }));
  await user.type(screen.getByLabelText("Server address"), "new");
  expect(desktopApi.invalidate).toHaveBeenCalledWith(profile.id);
  expect(screen.queryByLabelText("Keep existing credential")).toBeNull();
  expect(
    (screen.getByLabelText("New Bearer credential") as HTMLInputElement).value,
  ).toBe("");
  expect(
    (
      screen.getByRole("button", {
        name: "Use this connection",
      }) as HTMLButtonElement
    ).disabled,
  ).toBe(true);
});

test("a save from the previous connection editor cannot dismiss a new draft", async () => {
  const user = userEvent.setup();
  vi.mocked(desktopApi.state).mockResolvedValue(state());
  let finish!: (next: DesktopState) => void;
  const pendingSave = new Promise<DesktopState>((resolve) => {
    finish = resolve;
  });
  vi.mocked(desktopApi.save).mockReturnValueOnce(pendingSave);

  render(<App />);
  await user.click(screen.getByRole("button", { name: "连接" }));
  // Let navigation finish moving focus before keyboard input begins.
  await waitFor(() => {
    expect(document.activeElement).toBe(
      screen.getByRole("heading", { level: 1, name: "连接" }),
    );
    expect(screen.getByLabelText("连接名称").matches(":enabled")).toBe(true);
  });
  await user.type(screen.getByLabelText("连接名称"), "First");
  await user.type(
    screen.getByLabelText("Server 地址"),
    "http://localhost:8000",
  );
  await user.click(screen.getByRole("button", { name: "保存配置" }));
  expect(desktopApi.save).toHaveBeenCalledWith(
    expect.objectContaining({
      name: "First",
      endpoint: "http://localhost:8000",
    }),
  );
  await user.click(screen.getByRole("button", { name: "总览" }));
  await answerConfirmation(user, true);
  await waitFor(() =>
    expect(document.activeElement).toBe(
      screen.getByRole("heading", { level: 1, name: "总览" }),
    ),
  );
  await user.click(screen.getByRole("button", { name: "连接" }));
  await waitFor(() =>
    expect(document.activeElement).toBe(
      screen.getByRole("heading", { level: 1, name: "连接" }),
    ),
  );
  await user.type(screen.getByLabelText("连接名称"), "New draft");
  await act(async () => {
    finish(state({ ...profile, name: "First" }));
  });
  await user.click(screen.getByRole("button", { name: "总览" }));
  await answerConfirmation(user, false);
  expect((screen.getByLabelText("连接名称") as HTMLInputElement).value).toBe(
    "New draft",
  );
});
