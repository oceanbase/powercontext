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
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Connections } from "../src/app/Connections";
import { desktopApi } from "../src/shared/ipc";
import type { DesktopState, ProfileView } from "../src/generated/ipc";
vi.mock("../src/shared/ipc", () => ({
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
beforeEach(() => {
  vi.clearAllMocks();
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});
test("canceling discard leaves the saved connection and its draft intact", async () => {
  const user = userEvent.setup();
  show();
  await user.click(screen.getByRole("button", { name: "Original" }));
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
  await user.click(screen.getByRole("button", { name: "Original" }));
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
  await user.click(screen.getByRole("button", { name: "Original" }));
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
  await user.click(screen.getByRole("button", { name: "Original" }));
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
