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
import { App } from "../src/app/App";
import { Connections } from "../src/app/Connections";
import { desktopApi } from "../src/shared/ipc";
import type { DesktopState, ProfileView } from "../src/generated/ipc";
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
const currentCompatibility = qualifications.find(
  (p) => p.contractSha256 === contractSha256,
)!;
const historicalCompatibility = qualifications.find(
  (p) => p.contractSha256 !== contractSha256,
)!;
function qualifiedState(p = profile): DesktopState {
  return {
    ...state(p),
    compatibilityProfiles: [historicalCompatibility, currentCompatibility],
  };
}
beforeEach(() => {
  vi.clearAllMocks();
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
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
  vi.spyOn(window, "confirm").mockReturnValue(false);
  await user.click(screen.getByRole("button", { name: "Remove connection" }));
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
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
  await user.click(screen.getByRole("button", { name: "Remove connection" }));
  expect(desktopApi.remove).toHaveBeenCalledWith(profile.id, 1);
  expect(onState).toHaveBeenCalled();
  expect(confirm.mock.calls.map(([message]) => message)).toEqual([
    "Discard unsaved input?",
    "Remove only this Desktop configuration and its credentials, without stopping the service or deleting business data?",
  ]);
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
  vi.spyOn(window, "confirm").mockReturnValue(true);
  await user.click(screen.getByRole("button", { name: "Cancel editing" }));
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
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
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
  confirm.mockClear().mockReturnValue(false);
  await user.click(screen.getByRole("button", { name: "总览" }));
  expect(confirm).toHaveBeenCalled();
  expect((screen.getByLabelText("连接名称") as HTMLInputElement).value).toBe(
    "New draft",
  );
});
