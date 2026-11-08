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
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App } from "../src/app/App";
import { desktopApi } from "../src/shared/ipc";
import { messages, type Language } from "../src/app/messages";
import { connectionMessages } from "../src/app/connection-messages";
import { answerConfirmation } from "./confirmation";
import type { DesktopState, ScopePage } from "../src/generated/ipc";

vi.mock("../src/shared/ipc", () => ({
  getFoundationInfo: vi.fn().mockResolvedValue(null),
  desktopApi: {
    state: vi.fn(),
    check: vi.fn(),
    save: vi.fn(),
    diagnostics: vi.fn(),
    scopes: vi.fn(),
    selectScope: vi.fn(),
    remember: vi.fn(),
    search: vi.fn(),
    atomicEntry: vi.fn(),
    cancelMemory: vi.fn().mockResolvedValue(undefined),
    cancelScopes: vi.fn().mockResolvedValue(undefined),
  },
}));
const profile = {
  id: "first",
  revision: 1,
  name: "Saved Server",
  endpoint: "http://127.0.0.1:8000",
  authentication: "unauthenticated_loopback" as const,
  caPem: null,
  compatibility: null,
  credentialState: "not_required" as const,
};
const second = { ...profile, id: "second", endpoint: "http://127.0.0.1:9000" };
const fact = { value: null, error: null };
const report = {
  connectionId: profile.id,
  revision: 1,
  checkedAt: 1,
  liveness: fact,
  readiness: fact,
  identity: fact,
  capabilities: fact,
  compatibilityVerified: true,
  anonymousAccess: true,
  supportedOperations: [
    "list_scopes",
    "get_scope",
    "remember_memory",
    "search_memory",
    "get_artifact_revision",
  ],
};
const scope = {
  scope_id: "exact-scope-a",
  title: "Same title",
  summary: "",
  version: 1,
  context_references: [],
  external_references: [],
};
const empty: DesktopState = {
  generation: 0,
  profiles: [],
  reports: [],
  active: null,
  compatibilityProfiles: [],
  pendingCredentialCleanup: 0,
  lastWrite: null,
};
const saved: DesktopState = {
  ...empty,
  generation: 1,
  profiles: [profile, second],
};
const connected: DesktopState = {
  ...saved,
  generation: 2,
  reports: [report],
  active: { connectionId: profile.id, generation: 2, scope: null, report },
};
const selected: DesktopState = {
  ...connected,
  generation: 3,
  active: { ...connected.active!, generation: 3, scope },
};
beforeEach(() => {
  vi.mocked(desktopApi.state).mockResolvedValue(empty);
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});
async function showHome(language: Language, state: DesktopState = empty) {
  vi.mocked(desktopApi.state).mockResolvedValue(state);
  const view = render(<App />);
  const user = userEvent.setup();
  await screen.findByRole("region", { name: messages.zh.guideTitle });
  if (language === "en") {
    await user.click(
      screen.getByRole("button", { name: messages.zh.settings }),
    );
    await user.selectOptions(screen.getByLabelText(messages.zh.language), "en");
    await user.click(screen.getByRole("button", { name: messages.en.home }));
  }
  return {
    ...view,
    user,
    t: messages[language],
    ct: connectionMessages[language],
  };
}
function guide(language: Language) {
  return within(
    screen.getByRole("region", { name: messages[language].guideTitle }),
  );
}
function expectNoBusinessOperations() {
  expect(desktopApi.check).not.toHaveBeenCalled();
  expect(desktopApi.save).not.toHaveBeenCalled();
  expect(desktopApi.diagnostics).not.toHaveBeenCalled();
  expect(desktopApi.remember).not.toHaveBeenCalled();
  expect(desktopApi.search).not.toHaveBeenCalled();
  expect(desktopApi.atomicEntry).not.toHaveBeenCalled();
}

test.each(["zh", "en"] as const)(
  "%s Overview offers both preparation paths with honest missing-resource guidance",
  async (language) => {
    const { user, t } = await showHome(language);
    expect(guide(language).getByText(t.guideIntro)).toBeTruthy();
    expect(guide(language).getByText(t.guideLocalUnavailable)).toBeTruthy();
    expect(guide(language).getByText(t.guideInfo)).toBeTruthy();
    await user.click(
      guide(language).getByRole("button", { name: t.guideLocalOpen }),
    );
    const local = within(screen.getByRole("region", { name: t.localServer }));
    expect(document.activeElement).toBe(
      local.getByRole("heading", { name: t.localServer }),
    );
    expect(local.getByText(t.localServerUnavailableHint)).toBeTruthy();
    await user.click(screen.getByRole("button", { name: t.home }));
    await user.click(
      guide(language).getByRole("button", { name: t.guideAddConnection }),
    );
    expect(document.activeElement).toBe(
      screen.getByRole("heading", { level: 1, name: t.connections }),
    );
    expectNoBusinessOperations();
  },
);

test.each(["zh", "en"] as const)(
  "%s saved-profile guidance opens the exact selected editor without activating it",
  async (language) => {
    const { user, t, ct, unmount } = await showHome(language, saved);
    const picker = guide(language).getByRole("combobox", {
      name: t.guideSavedLabel,
    });
    expect(
      within(picker).getByRole("option", {
        name: `${second.name} · ${second.endpoint} · ${second.id}`,
      }),
    ).toBeTruthy();
    await user.selectOptions(picker, second.id);
    await user.click(
      guide(language).getByRole("button", { name: t.guideReviewProfile }),
    );
    await waitFor(() =>
      expect((screen.getByLabelText(ct.name) as HTMLInputElement).value).toBe(
        second.name,
      ),
    );
    expect((screen.getByLabelText(ct.endpoint) as HTMLInputElement).value).toBe(
      second.endpoint,
    );
    expectNoBusinessOperations();
    unmount();
    render(<App />);
    await screen.findByRole("combobox", { name: messages.zh.guideSavedLabel });
    expect(screen.queryByText(scope.scope_id)).toBeNull();
    expectNoBusinessOperations();
  },
);

test.each(["zh", "en"] as const)(
  "%s Overview uses the existing exact Scope dialog and Memory handoff with keyboard focus",
  async (language) => {
    const otherScope = { ...scope, scope_id: "exact-scope-b" };
    vi.mocked(desktopApi.scopes).mockResolvedValue({
      items: [scope, otherScope],
      next_cursor: null,
    });
    vi.mocked(desktopApi.selectScope).mockResolvedValue({
      ...selected,
      active: { ...selected.active!, scope: otherScope },
    });
    const { user, t, ct } = await showHome(language, connected);
    const trigger = guide(language).getByRole("button", {
      name: t.chooseScopeTitle,
    });
    trigger.focus();
    await user.keyboard("{Enter}");
    expect(desktopApi.scopes).not.toHaveBeenCalled();
    let dialog = within(screen.getByRole("dialog", { name: ct.scope }));
    expect(document.activeElement).toBe(
      dialog.getByRole("button", { name: t.closeDialog }),
    );
    await user.keyboard("{Escape}");
    expect(document.activeElement).toBe(trigger);
    await user.keyboard("{Enter}");
    dialog = within(screen.getByRole("dialog", { name: ct.scope }));
    await user.tab();
    expect(document.activeElement).toBe(dialog.getByLabelText(ct.scopeQuery));
    await user.type(dialog.getByLabelText(ct.scopeQuery), scope.title);
    await user.click(dialog.getByRole("button", { name: ct.find }));
    const exact = dialog.getByRole("button", {
      name: otherScope.title,
      description: otherScope.scope_id,
    });
    exact.focus();
    await user.keyboard("{Enter}");
    expect(desktopApi.selectScope).toHaveBeenCalledWith(
      connected.active!.generation,
      otherScope.scope_id,
    );
    expect(document.activeElement).toBe(
      screen.getByRole("heading", { level: 1, name: t.memories }),
    );
    expectNoBusinessOperations();
    await user.click(screen.getByRole("button", { name: t.home }));
    expect(guide(language).getByText(otherScope.scope_id)).toBeTruthy();
    expect(
      guide(language).getByRole("button", { name: t.openMemories }),
    ).toBeTruthy();
  },
);

test.each(["zh", "en"] as const)(
  "%s guide dismissal is session-only and preserves recovery and next actions",
  async (language) => {
    const { user, t, unmount } = await showHome(language, saved);
    guide(language).getByRole("button", { name: t.guideDismiss }).focus();
    await user.keyboard("{Enter}");
    const revisit = guide(language).getByRole("button", {
      name: t.guideRevisit,
    });
    expect(document.activeElement).toBe(revisit);
    expect(revisit.getAttribute("aria-expanded")).toBe("false");
    expect(
      screen.queryByRole("heading", { name: t.guideAfterConnection }),
    ).toBeNull();
    expect(guide(language).getByText(t.guideLocalUnavailable)).toBeTruthy();
    expect(
      guide(language).getByRole("button", { name: t.guideReviewProfile }),
    ).toBeTruthy();
    await user.click(screen.getByRole("button", { name: t.settings }));
    await user.click(screen.getByRole("button", { name: t.home }));
    await user.click(
      guide(language).getByRole("button", { name: t.guideRevisit }),
    );
    expect(
      screen.getByRole("heading", { name: t.guideAfterConnection }),
    ).toBeTruthy();
    await user.click(
      guide(language).getByRole("button", { name: t.guideDismiss }),
    );
    unmount();
    render(<App />);
    expect(
      await screen.findByRole("button", { name: messages.zh.guideDismiss }),
    ).toBeTruthy();
    expectNoBusinessOperations();
  },
);

test.each(["zh", "en"] as const)(
  "%s exact target and per-operation restrictions remain visible after dismissal",
  async (language) => {
    const restricted = { ...report, supportedOperations: ["search_memory"] };
    const { user, t } = await showHome(language, {
      ...selected,
      reports: [restricted],
      active: { ...selected.active!, report: restricted },
    });
    expect(guide(language).getByText(scope.scope_id)).toBeTruthy();
    expect(guide(language).getByText(profile.endpoint)).toBeTruthy();
    await user.click(
      guide(language).getByRole("button", { name: t.guideDismiss }),
    );
    expect(
      guide(language).getByText(new RegExp(`${t.featureSave}:`)),
    ).toBeTruthy();
    expect(
      guide(language).getByText(new RegExp(`${t.featureRead}:`)),
    ).toBeTruthy();
    await user.click(
      guide(language).getByRole("button", { name: t.openMemories }),
    );
    const memory = language === "zh" ? "记忆内容" : "Memory text";
    expect(
      screen.getByRole("textbox", { name: memory }).matches(":disabled"),
    ).toBe(true);
    expectNoBusinessOperations();
  },
);

test.each(["forbidden", "network"])(
  "Overview Scope entry preserves loading and %s recovery in its owning dialog",
  async (code) => {
    let reject!: (error: unknown) => void;
    vi.mocked(desktopApi.scopes).mockImplementationOnce(
      () =>
        new Promise<ScopePage>((_, fail) => {
          reject = fail;
        }),
    );
    const { user, t, ct } = await showHome("en", connected);
    await user.click(
      guide("en").getByRole("button", { name: t.chooseScopeTitle }),
    );
    await user.click(screen.getByRole("button", { name: ct.find }));
    expect(screen.getByText(t.loadingScopes)).toBeTruthy();
    await act(async () => reject({ code }));
    const dialog = within(screen.getByRole("dialog"));
    expect(dialog.getByRole("alert")).toBeTruthy();
    expect(
      dialog.getByRole("button", { name: t.restartScopeSearch }),
    ).toBeTruthy();
    expect(
      dialog.getByText(
        code === "forbidden" ? t.recoverAccess : t.recoverOperation,
      ),
    ).toBeTruthy();
    await user.click(dialog.getByRole("button", { name: t.closeDialog }));
    await user.click(
      guide("en").getByRole("button", { name: t.chooseConnection }),
    );
    expect(document.activeElement).toBe(
      screen.getByRole("heading", { level: 1, name: t.connections }),
    );
  },
);

test("Overview Scope entry preserves empty and unsupported-operation guidance", async () => {
  vi.mocked(desktopApi.scopes).mockResolvedValueOnce({
    items: [],
    next_cursor: null,
  });
  const { user, t, ct, unmount } = await showHome("en", connected);
  await user.click(
    guide("en").getByRole("button", { name: t.chooseScopeTitle }),
  );
  await user.click(screen.getByRole("button", { name: ct.find }));
  expect(await screen.findByText(ct.empty)).toBeTruthy();
  expect(screen.getByText(ct.emptyScopeHint)).toBeTruthy();
  unmount();
  const limited = { ...report, supportedOperations: ["get_scope"] };
  await showHome("en", {
    ...connected,
    reports: [limited],
    active: { ...connected.active!, report: limited },
  });
  await user.click(
    guide("en").getByRole("button", { name: t.chooseScopeTitle }),
  );
  expect(screen.getByText(ct.scopeListUnavailable)).toBeTruthy();
  expect(
    screen.getByRole("button", { name: ct.find }).matches(":disabled"),
  ).toBe(true);
});

test("latest compatibility facts prevent an obsolete usable-Scope promise and recover without probing", async () => {
  const latest = {
    ...report,
    checkedAt: 2,
    compatibilityVerified: false,
    supportedOperations: [],
  };
  const { user, t } = await showHome("en", { ...selected, reports: [latest] });
  expect(guide("en").getByText(t.compatibilityNeeded)).toBeTruthy();
  expect(
    guide("en").queryByRole("button", { name: t.openMemories }),
  ).toBeNull();
  await user.click(guide("en").getByRole("button", { name: t.guideDismiss }));
  expect(guide("en").getByText(t.recoverCompatibility)).toBeTruthy();
  expectNoBusinessOperations();
});

test.each(["zh", "en"] as const)(
  "%s navigating away from a guide-opened Memory draft still requires explicit discard",
  async (language) => {
    const { user, t } = await showHome(language, selected);
    await user.click(
      guide(language).getByRole("button", { name: t.openMemories }),
    );
    const input = screen.getByRole("textbox", {
      name: language === "zh" ? "记忆内容" : "Memory text",
    });
    await user.type(input, "private unsaved draft");
    await user.click(screen.getByRole("button", { name: t.home }));
    await answerConfirmation(user, false);
    expect((input as HTMLTextAreaElement).value).toBe("private unsaved draft");
    await user.click(screen.getByRole("button", { name: t.home }));
    await answerConfirmation(user, true);
    expect(guide(language).getByText(scope.scope_id)).toBeTruthy();
    expect(screen.queryByText("private unsaved draft")).toBeNull();
    expectNoBusinessOperations();
  },
);
