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
import {
  cleanup,
  render,
  screen,
  fireEvent,
  act,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {
  NoteForm,
  MemoryWorkspace,
  textBytes,
} from "../src/app/MemoryWorkspace";
import { answerConfirmation } from "./confirmation";
import { App } from "../src/app/App";
import { Overview } from "../src/app/Overview";
import { desktopApi } from "../src/shared/ipc";
import type {
  ArtifactReference,
  CompatibilityReason,
  DesktopState,
  MemorySearchResponse,
  WriteOutcome,
} from "../src/generated/ipc";
vi.mock("../src/shared/ipc", () => ({
  getFoundationInfo: vi.fn().mockResolvedValue(null),
  desktopApi: {
    scopes: vi.fn(),
    selectScope: vi.fn(),
    cancelScopes: vi.fn().mockResolvedValue(undefined),
    remember: vi.fn(),
    state: vi.fn(),
    search: vi.fn(),
    atomicEntry: vi.fn(),
    cancelMemory: vi.fn().mockResolvedValue(undefined),
  },
}));
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  vi.restoreAllMocks();
});
const scope = {
  scope_id: "scope-a",
  title: "Test scope",
  summary: "",
  context_references: [],
  external_references: [],
  version: 1,
};
const fact = { value: null, error: null };
const state: DesktopState = {
  generation: 1,
  profiles: [],
  reports: [],
  active: {
    connectionId: "connection-a",
    generation: 1,
    scope,
    report: {
      connectionId: "connection-a",
      revision: 1,
      checkedAt: 1,
      liveness: fact,
      readiness: fact,
      identity: fact,
      capabilities: fact,
      compatibilityVerified: true,
      anonymousAccess: true,
      supportedOperations: [
        "remember_memory",
        "search_memory",
        "get_artifact_revision",
        "list_scopes",
        "get_scope",
      ],
    },
  },
  compatibilityProfiles: [],
  pendingCredentialCleanup: 0,
  lastWrite: null,
};
const artifact: ArtifactReference = {
  family: "atomic-memory",
  artifact_id: "atomic-a",
  revision: 7,
};
function restrictedState(
  operation: string,
  reason: CompatibilityReason,
): DesktopState {
  const active = state.active!;
  return {
    ...state,
    active: {
      ...active,
      report: {
        ...active.report,
        supportedOperations: active.report.supportedOperations.filter(
          (item) => item !== operation,
        ),
        discovery: {
          status: "supported",
          server: null,
          error: null,
          reason: null,
          operations: {
            [operation]: {
              status:
                reason === "qualification_required"
                  ? "unverified"
                  : "unsupported",
              reason,
              qualificationId: null,
            },
          },
        },
      },
    },
  };
}
test.each(["en", "zh"] as const)(
  "unsupported saving preserves independently supported search and exact reads (%s)",
  async (language) => {
    const user = userEvent.setup();
    const restricted = restrictedState("remember_memory", "operation_missing");
    vi.mocked(desktopApi.search).mockResolvedValue({
      hits: [
        { artifact, text: "supported search", score: 1, matched_by: ["text"] },
      ],
    });
    vi.mocked(desktopApi.atomicEntry).mockResolvedValue({
      artifact,
      kind: "note",
      text: "exact content",
      source_refs: [],
    });
    render(
      <MemoryWorkspace
        state={restricted}
        language={language}
        onState={vi.fn()}
        onDirty={vi.fn()}
      />,
    );
    const input = screen.getByLabelText(
      language === "en" ? "Memory text" : "记忆内容",
    ) as HTMLTextAreaElement;
    expect(input.disabled).toBe(true);
    const save = screen.getByRole("button", {
      name: language === "en" ? "Save" : "保存",
    }) as HTMLButtonElement;
    expect(save.disabled).toBe(true);
    expect(
      screen.getByText(
        language === "en"
          ? /Server does not declare support for this operation/
          : /Server 未声明支持此操作/,
      ),
    ).toBeTruthy();
    await user.click(save);
    expect(desktopApi.remember).not.toHaveBeenCalled();
    await user.type(
      screen.getByLabelText(
        language === "en" ? "Keyword search" : "关键词搜索",
      ),
      "supported",
    );
    await user.click(
      screen.getByRole("button", {
        name: language === "en" ? "Search" : "搜索",
      }),
    );
    await user.click(
      screen.getByRole("button", {
        name: language === "en" ? "Read exact version" : "阅读精确版本",
      }),
    );
    expect(desktopApi.search).toHaveBeenCalledWith(1, "supported");
    expect(desktopApi.atomicEntry).toHaveBeenCalledWith(1, artifact);
    expect(screen.getByText("exact content")).toBeTruthy();
  },
);
test("unqualified exact reads leave search results and saving usable", async () => {
  const user = userEvent.setup();
  vi.mocked(desktopApi.search).mockResolvedValue({
    hits: [{ artifact, text: "searchable", score: 1, matched_by: ["text"] }],
  });
  render(
    <MemoryWorkspace
      state={restrictedState("get_artifact_revision", "qualification_required")}
      language="en"
      onState={vi.fn()}
      onDirty={vi.fn()}
    />,
  );
  expect(
    (screen.getByLabelText("Memory text") as HTMLTextAreaElement).disabled,
  ).toBe(false);
  await user.type(screen.getByLabelText("Keyword search"), "query");
  await user.click(screen.getByRole("button", { name: "Search" }));
  expect(screen.getAllByText("searchable").length).toBeGreaterThan(0);
  const read = screen.getByRole("button", {
    name: "Read exact version",
  }) as HTMLButtonElement;
  expect(read.disabled).toBe(true);
  expect(
    screen.getByText(
      /selected profile has no independent qualification for this operation/,
    ),
  ).toBeTruthy();
  await user.click(read);
  expect(desktopApi.atomicEntry).not.toHaveBeenCalled();
});
test("an unavailable search does not disable saving", async () => {
  const user = userEvent.setup();
  const restricted = restrictedState("search_memory", "feature_missing");
  vi.mocked(desktopApi.state).mockResolvedValue(restricted);
  vi.mocked(desktopApi.remember).mockResolvedValue(outcome("succeeded"));
  render(
    <MemoryWorkspace
      state={restricted}
      language="en"
      onState={vi.fn()}
      onDirty={vi.fn()}
    />,
  );
  expect(
    (screen.getByLabelText("Keyword search") as HTMLInputElement).disabled,
  ).toBe(true);
  expect(
    screen.getByText(
      /Server does not declare the feature required for this operation/,
    ),
  ).toBeTruthy();
  await user.type(screen.getByLabelText("Memory text"), "supported save");
  await user.click(screen.getByRole("button", { name: "Save" }));
  expect(desktopApi.remember).toHaveBeenCalledWith(1, "supported save");
  expect(desktopApi.search).not.toHaveBeenCalled();
});
test("an aggregate compatibility verdict cannot enable an unlisted save operation", () => {
  const active = state.active!;
  render(
    <NoteForm
      state={{
        ...state,
        active: {
          ...active,
          report: { ...active.report, supportedOperations: [] },
        },
      }}
      language="en"
      onState={vi.fn()}
      onDirty={vi.fn()}
      onReadSaved={vi.fn()}
      invalidateSaved={0}
    />,
  );
  expect(
    (screen.getByLabelText("Memory text") as HTMLTextAreaElement).disabled,
  ).toBe(true);
  expect(
    screen.getByText(/This operation is unverified for this connection/),
  ).toBeTruthy();
});

test("saved records cannot open an unqualified exact read while saving remains available", async () => {
  const user = userEvent.setup();
  const restricted = restrictedState(
    "get_artifact_revision",
    "qualification_required",
  );
  const saved = makeSaved(1);
  vi.mocked(desktopApi.remember).mockResolvedValue(saved.outcome);
  vi.mocked(desktopApi.state).mockResolvedValue(restricted);
  render(
    <MemoryWorkspace
      state={restricted}
      language="en"
      onState={vi.fn()}
      onDirty={vi.fn()}
    />,
  );
  await user.type(screen.getByLabelText("Memory text"), "supported save");
  await user.click(screen.getByRole("button", { name: "Save" }));
  expect(desktopApi.remember).toHaveBeenCalledWith(1, "supported save");
  const open = screen.getByRole("button", {
    name: "Open saved record",
  }) as HTMLButtonElement;
  expect(open.disabled).toBe(true);
  expect(
    screen.getAllByText(
      /selected profile has no independent qualification for this operation/,
    ).length,
  ).toBeGreaterThan(0);
  await user.click(open);
  expect(desktopApi.atomicEntry).not.toHaveBeenCalled();
});
test("Overview shows availability and restriction for each feature", () => {
  render(
    <Overview
      state={restrictedState("remember_memory", "operation_missing")}
      language="en"
      onNavigate={vi.fn()}
    />,
  );
  const save = within(screen.getByRole("group", { name: "Save memories" }));
  expect(save.getByText("Unavailable")).toBeTruthy();
  expect(
    save.getByText(/Server does not declare support for this operation/),
  ).toBeTruthy();
  expect(
    within(screen.getByRole("group", { name: "Full-text search" })).getByText(
      "Available",
    ),
  ).toBeTruthy();
  expect(
    within(screen.getByRole("group", { name: "Exact details" })).getByText(
      "Available",
    ),
  ).toBeTruthy();
});
function outcome(status: "succeeded" | "unknown" | "failed"): WriteOutcome {
  return {
    record: {
      operationId: "operation-a",
      context: {
        connectionId: "connection-a",
        endpoint: "http://127.0.0.1:8000",
        principal: null,
        generation: 1,
        scopeId: "scope-a",
      },
      status,
      references: [],
      error: null,
    },
    result: status === "succeeded" ? { changed: false, records: [] } : null,
  };
}
function form() {
  vi.mocked(desktopApi.state).mockResolvedValue(state);
  render(
    <NoteForm
      state={state}
      language="en"
      onState={vi.fn()}
      onDirty={vi.fn()}
      onReadSaved={vi.fn()}
      invalidateSaved={0}
    />,
  );
}
function makeSaved(count: number): {
  outcome: ReturnType<typeof outcome>;
  records: NonNullable<ReturnType<typeof outcome>["result"]>["records"];
} {
  const saved = outcome("succeeded");
  saved.result = {
    changed: true,
    records: Array.from({ length: count }, (_, index) => ({
      artifact: { ...artifact, artifact_id: `atomic-${index}` },
      kind: "note",
      text: `Atomic note ${index}`,
      state: "active",
      state_version: 0,
      merged_into_id: null,
    })),
  };
  saved.record.references = saved.result.records.map(
    (record) => record.artifact,
  );
  return { outcome: saved, records: saved.result.records };
}
test("a save without records succeeds without inventing a memory or submitting on Enter", async () => {
  const user = userEvent.setup();
  form();
  vi.mocked(desktopApi.remember).mockResolvedValue(outcome("succeeded"));
  await user.type(screen.getByLabelText("Memory text"), "Café 中文{enter}note");
  expect(desktopApi.remember).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "Save" }));
  expect(desktopApi.remember).toHaveBeenCalledWith(1, "Café 中文\nnote");
  expect(screen.getByRole("status").textContent).toMatch(/Saved successfully/);
  expect(screen.getByRole("status").textContent).toMatch(
    /Server returned no memory/,
  );
  expect(
    (screen.getByLabelText("Memory text") as HTMLTextAreaElement).value,
  ).toBe("");
});
test.each([1, 2])(
  "an Atomic save reports %i returned records as saved",
  async (count) => {
    const user = userEvent.setup();
    form();
    const { outcome: saved } = makeSaved(count);
    vi.mocked(desktopApi.remember).mockResolvedValue(saved);
    await user.type(screen.getByLabelText("Memory text"), "Atomic note");
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(screen.getByRole("status").textContent).toBe(
      `Saved successfully. ${count} record${count === 1 ? "" : "s"} returned. Each can open its exact revision directly.`,
    );
    expect(
      screen.getAllByRole("button", { name: "Open saved record" }),
    ).toHaveLength(count);
    expect(
      (screen.getByLabelText("Memory text") as HTMLTextAreaElement).value,
    ).toBe("");
  },
);
test("Chinese save success text keeps the harness-expected 保存成功。 prefix", async () => {
  // Mirrors desktop/tests/installed_*.py wait_text("保存成功。"), which
  // asserts document.body.innerText.includes("保存成功。") after a save.
  const user = userEvent.setup();
  vi.mocked(desktopApi.state).mockResolvedValue(state);
  const { outcome: saved } = makeSaved(1);
  vi.mocked(desktopApi.remember).mockResolvedValue(saved);
  render(
    <NoteForm
      state={state}
      language="zh"
      onState={vi.fn()}
      onDirty={vi.fn()}
      onReadSaved={vi.fn()}
      invalidateSaved={0}
    />,
  );
  await user.type(screen.getByLabelText("记忆内容"), "契约检查");
  await user.click(screen.getByRole("button", { name: "保存" }));
  // jsdom has no innerText; textContent matches the harness's
  // document.body.innerText.includes("保存成功。") semantics in WebView.
  expect(document.body.textContent).toContain("保存成功。");
});
test("saved records never use the memory-hits class the search harness counts", async () => {
  // installed_boundaries.exercise_search_limit counts
  // document.querySelectorAll(".memory-hits li") to assert search result
  // counts. The saved-record list must not contribute to that selector.
  const user = userEvent.setup();
  vi.mocked(desktopApi.state).mockResolvedValue(state);
  const { outcome: saved } = makeSaved(1);
  vi.mocked(desktopApi.remember).mockResolvedValue(saved);
  render(
    <NoteForm
      state={state}
      language="en"
      onState={vi.fn()}
      onDirty={vi.fn()}
      onReadSaved={vi.fn()}
      invalidateSaved={0}
    />,
  );
  await user.type(screen.getByLabelText("Memory text"), "contract");
  await user.click(screen.getByRole("button", { name: "Save" }));
  expect(
    screen.getByRole("button", { name: "Open saved record" }),
  ).toBeTruthy();
  expect(document.querySelectorAll(".memory-hits li")).toHaveLength(0);
});
test("an Atomic save can be searched, opened and copied by exact Artifact reference", async () => {
  const user = userEvent.setup();
  const { outcome: saved } = makeSaved(1);
  vi.mocked(desktopApi.remember).mockResolvedValue(saved);
  vi.mocked(desktopApi.search).mockResolvedValue({
    hits: [
      {
        artifact,
        text: "Atomic match",
        score: 1,
        matched_by: ["text"],
      },
    ],
  });
  vi.mocked(desktopApi.atomicEntry).mockResolvedValue({
    artifact,
    kind: "note",
    text: "<img src=x onerror=unsafe()>",
    source_refs: [{ source_type: "note", source_id: "source-a" }],
  });
  render(
    <MemoryWorkspace
      state={state}
      language="en"
      onState={vi.fn()}
      onDirty={vi.fn()}
    />,
  );
  await user.type(screen.getByLabelText("Memory text"), "Atomic note");
  await user.click(screen.getByRole("button", { name: "Save" }));
  expect(screen.getByRole("status").textContent).toBe(
    "Saved successfully. 1 record returned. Each can open its exact revision directly.",
  );
  await user.type(screen.getByLabelText("Keyword search"), "Atomic");
  await user.click(screen.getByRole("button", { name: "Search" }));
  await user.click(screen.getByRole("button", { name: "Read exact version" }));
  expect(desktopApi.atomicEntry).toHaveBeenCalledWith(1, artifact);
  expect(screen.getByText("<img src=x onerror=unsafe()>")).toBeTruthy();
  expect(document.querySelector(".reader-body img")).toBeNull();
  expect(screen.getByText("source-a")).toBeTruthy();
  const clipboard = vi
    .spyOn(navigator.clipboard, "writeText")
    .mockResolvedValue();
  await user.click(
    screen.getByRole("button", { name: "Copy exact reference" }),
  );
  expect(clipboard).toHaveBeenLastCalledWith(JSON.stringify(artifact, null, 2));
});
test("UTF-8 overflow and IME composition cannot accidentally submit", async () => {
  const user = userEvent.setup();
  form();
  const input = screen.getByLabelText("Memory text");
  const text = "中".repeat(2731);
  expect(textBytes(text)).toBe(8193);
  fireEvent.change(input, { target: { value: text } });
  expect(
    (screen.getByRole("button", { name: "Save" }) as HTMLButtonElement)
      .disabled,
  ).toBe(true);
  expect((input as HTMLTextAreaElement).value).toBe(text);
  fireEvent.change(input, { target: { value: "中文" } });
  fireEvent.compositionStart(input);
  await user.click(screen.getByRole("button", { name: "Save" }));
  expect(desktopApi.remember).not.toHaveBeenCalled();
});
test("a saved record opens its exact revision through the existing reader", async () => {
  const user = userEvent.setup();
  const { outcome: saved } = makeSaved(1);
  saved.result = {
    ...saved.result!,
    records: [
      { ...saved.result!.records[0], artifact, text: "Just saved note" },
    ],
  };
  vi.mocked(desktopApi.remember).mockResolvedValue(saved);
  vi.mocked(desktopApi.atomicEntry).mockResolvedValue({
    artifact,
    kind: "note",
    text: "Just saved note",
    source_refs: [],
  });
  render(
    <MemoryWorkspace
      state={state}
      language="en"
      onState={vi.fn()}
      onDirty={vi.fn()}
    />,
  );
  await user.type(screen.getByLabelText("Memory text"), "Just saved note");
  await user.click(screen.getByRole("button", { name: "Save" }));
  const openSaved = screen.getByRole("button", { name: "Open saved record" });
  await user.click(openSaved);
  expect(desktopApi.atomicEntry).toHaveBeenCalledWith(1, artifact);
  expect(screen.getByRole("heading", { name: "Memory details" })).toBeTruthy();
  expect(screen.getByText(/Records returned by this save/)).toBeTruthy();
  // Closing the reader returns keyboard focus to the saved-record trigger.
  await user.click(screen.getByRole("button", { name: "Close reader" }));
  await waitFor(() => expect(document.activeElement).toBe(openSaved));
});

test.each([
  ["en", "forbidden"],
  ["en", "network"],
  ["zh", "forbidden"],
  ["zh", "network"],
] as const)(
  "a failed saved-record read in %s (%s) clears returned bodies but keeps the write outcome",
  async (language, code) => {
    const user = userEvent.setup();
    const { outcome: saved } = makeSaved(1);
    vi.mocked(desktopApi.remember).mockResolvedValue(saved);
    vi.mocked(desktopApi.atomicEntry).mockRejectedValue({ code });
    render(
      <MemoryWorkspace
        state={state}
        language={language}
        onState={vi.fn()}
        onDirty={vi.fn()}
      />,
    );
    const note = language === "en" ? "Memory text" : "记忆内容";
    const save = language === "en" ? "Save" : "保存";
    const open = language === "en" ? "Open saved record" : "打开保存的记录";
    const openingHint =
      language === "en"
        ? "Each can open its exact revision directly."
        : "每条都可以直接打开其精确版本。";
    await user.type(screen.getByLabelText(note), "private body");
    await user.click(screen.getByRole("button", { name: save }));
    expect(screen.getByRole("status").textContent).toContain(openingHint);
    expect(screen.getAllByText("Atomic note 0").length).toBeGreaterThan(0);
    screen.getByRole("button", { name: open }).focus();
    await user.keyboard("{Enter}");
    // The saved-record list no longer exposes the returned body after the
    // failed read, while the write outcome itself remains visible.
    expect(screen.getByRole("alert")).toBeTruthy();
    await waitFor(() => {
      expect(screen.queryAllByText("Atomic note 0")).toHaveLength(0);
      expect(screen.queryAllByRole("button", { name: open })).toHaveLength(0);
    });
    expect(document.activeElement).toBe(
      screen.getByLabelText(
        language === "en" ? "Keyword search" : "关键词搜索",
      ),
    );
    // The save receipt keeps the real returned count; a failed read must not
    // rewrite it into a zero-record result.
    expect(screen.getByRole("status").textContent).toBe(
      language === "en"
        ? "Saved successfully. 1 record returned."
        : "保存成功。Server 返回 1 条记录。",
    );
  },
);

test.each(["forbidden", "network"])(
  "a failed saved-record read does not steal focus moved during the request (%s)",
  async (code) => {
    const user = userEvent.setup();
    let fail!: (error: unknown) => void;
    vi.mocked(desktopApi.remember).mockResolvedValue(makeSaved(1).outcome);
    vi.mocked(desktopApi.state).mockResolvedValue(state);
    vi.mocked(desktopApi.atomicEntry).mockImplementationOnce(
      () =>
        new Promise((_resolve, reject) => {
          fail = reject;
        }),
    );
    render(
      <MemoryWorkspace
        state={state}
        language="en"
        onState={vi.fn()}
        onDirty={vi.fn()}
      />,
    );
    const note = screen.getByLabelText("Memory text");
    await user.type(note, "private body");
    await user.click(screen.getByRole("button", { name: "Save" }));
    screen.getByRole("button", { name: "Open saved record" }).focus();
    await user.keyboard("{Enter}");
    await user.click(note);
    await act(async () => fail({ code }));
    await waitFor(() =>
      expect(
        screen.queryByRole("button", { name: "Open saved record" }),
      ).toBeNull(),
    );
    expect(document.activeElement).toBe(note);
    expect(screen.queryAllByText("Atomic note 0")).toHaveLength(0);
  },
);

test("closing a saved reader after another save restores focus within the workspace", async () => {
  const user = userEvent.setup();
  vi.mocked(desktopApi.remember).mockResolvedValue(makeSaved(1).outcome);
  vi.mocked(desktopApi.state).mockResolvedValue(state);
  vi.mocked(desktopApi.atomicEntry).mockResolvedValue({
    artifact,
    kind: "note",
    text: "First saved body",
    source_refs: [],
  });
  render(
    <MemoryWorkspace
      state={state}
      language="en"
      onState={vi.fn()}
      onDirty={vi.fn()}
    />,
  );
  await user.type(screen.getByLabelText("Memory text"), "first note");
  await user.click(screen.getByRole("button", { name: "Save" }));
  const trigger = screen.getByRole("button", { name: "Open saved record" });
  await user.click(trigger);
  await screen.findByRole("heading", { name: "Memory details" });
  await user.type(screen.getByLabelText("Memory text"), "second note");
  await user.click(screen.getByRole("button", { name: "Save" }));
  expect(trigger.isConnected).toBe(false);
  await user.click(screen.getByRole("button", { name: "Close reader" }));
  await waitFor(() =>
    expect(document.activeElement).toBe(
      screen.getByLabelText("Keyword search"),
    ),
  );
});

test.each(["en", "zh"] as const)(
  "a failed save outcome explains permission recovery and retains its draft (%s)",
  async (language) => {
    const user = userEvent.setup();
    const failed = outcome("failed");
    failed.record.error = {
      code: "forbidden",
      requestId: null,
      dispatched: true,
    };
    vi.mocked(desktopApi.state).mockResolvedValue(state);
    vi.mocked(desktopApi.remember).mockResolvedValue(failed);
    const onDirty = vi.fn();
    render(
      <NoteForm
        onReadSaved={vi.fn()}
        invalidateSaved={0}
        state={state}
        language={language}
        onState={vi.fn()}
        onDirty={onDirty}
      />,
    );
    const input = screen.getByRole("textbox");
    await user.type(input, "preserve denied draft");
    onDirty.mockClear();
    await user.click(
      screen.getByRole("button", { name: language === "zh" ? "保存" : "Save" }),
    );
    const alert = screen.getByRole("alert");
    expect(alert.textContent).toContain(
      language === "zh"
        ? "当前身份无权执行此操作"
        : "This identity cannot perform this operation",
    );
    expect(alert.textContent).toContain(
      language === "zh"
        ? "申请此操作所需的权限"
        : "Request permission for this operation",
    );
    expect(alert.textContent).toContain(
      language === "zh" ? "精确 Scope" : "exact Scope",
    );
    expect((input as HTMLTextAreaElement).value).toBe("preserve denied draft");
    expect(onDirty).not.toHaveBeenCalledWith(false);
    expect(desktopApi.remember).toHaveBeenCalledTimes(1);
  },
);

test("unknown save retains the draft, names its original target, and does not replay automatically", async () => {
  const user = userEvent.setup();
  form();
  vi.mocked(desktopApi.remember).mockResolvedValue(outcome("unknown"));
  await user.type(screen.getByLabelText("Memory text"), "preserve me");
  await user.click(screen.getByRole("button", { name: "Save" }));
  expect(screen.getByRole("alert").textContent).toContain("outcome is unknown");
  expect(screen.getByRole("alert").textContent).toContain(
    "http://127.0.0.1:8000 · scope-a",
  );
  expect(
    (screen.getByLabelText("Memory text") as HTMLTextAreaElement).value,
  ).toBe("preserve me");
  expect(desktopApi.remember).toHaveBeenCalledTimes(1);

  await user.click(screen.getByRole("button", { name: "Save" }));
  await answerConfirmation(user, false);
  expect(desktopApi.remember).toHaveBeenCalledTimes(1);
  expect(
    (screen.getByLabelText("Memory text") as HTMLTextAreaElement).value,
  ).toBe("preserve me");
  await user.click(screen.getByRole("button", { name: "Save" }));
  await answerConfirmation(user, true);
  expect(desktopApi.remember).toHaveBeenCalledTimes(2);
});
test.each(["forbidden", "network"])(
  "a save completing after a %s search failure keeps its receipt without restoring private bodies",
  async (code) => {
    const user = userEvent.setup();
    let finish!: (value: WriteOutcome) => void;
    vi.mocked(desktopApi.remember).mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    vi.mocked(desktopApi.search).mockRejectedValueOnce({ code });
    vi.mocked(desktopApi.state).mockResolvedValue(state);
    render(
      <MemoryWorkspace
        state={state}
        language="en"
        onState={vi.fn()}
        onDirty={vi.fn()}
      />,
    );
    await user.type(screen.getByLabelText("Memory text"), "private body");
    await user.click(screen.getByRole("button", { name: "Save" }));
    await user.type(screen.getByLabelText("Keyword search"), "private");
    await user.click(screen.getByRole("button", { name: "Search" }));
    expect(await screen.findByRole("alert")).toBeTruthy();
    await act(async () => {
      finish(makeSaved(1).outcome);
    });
    expect(screen.queryAllByText("Atomic note 0")).toHaveLength(0);
    expect(
      screen.queryAllByRole("button", { name: "Open saved record" }),
    ).toHaveLength(0);
    expect(screen.getByRole("status").textContent).toContain("1 record");
    expect(screen.getByRole("status").textContent).not.toContain(
      "Each can open",
    );
    expect(
      (screen.getByLabelText("Memory text") as HTMLTextAreaElement).value,
    ).toBe("");
    // A later explicit save starts after invalidation and can show fresh content.
    vi.mocked(desktopApi.remember).mockResolvedValueOnce(makeSaved(1).outcome);
    await user.type(screen.getByLabelText("Memory text"), "fresh submission");
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(
      await screen.findByRole("button", { name: "Open saved record" }),
    ).toBeTruthy();
    expect(screen.getAllByText("Atomic note 0").length).toBeGreaterThan(0);
  },
);
test("search opens only the exact reference and renders hostile text literally", async () => {
  const user = userEvent.setup();
  vi.mocked(desktopApi.search).mockResolvedValue({
    hits: [
      {
        artifact,
        text: "<script>unsafe()</script>",
        score: 1,
        matched_by: ["text"],
      },
    ],
  });
  vi.mocked(desktopApi.atomicEntry).mockResolvedValue({
    artifact,
    kind: "note",
    text: "<img src=x onerror=unsafe()>",
    source_refs: [],
  });
  render(
    <MemoryWorkspace
      state={state}
      language="en"
      onState={vi.fn()}
      onDirty={vi.fn()}
    />,
  );
  await user.type(screen.getByLabelText("Keyword search"), "synthetic");
  await user.click(screen.getByRole("button", { name: "Search" }));
  await user.click(screen.getByRole("button", { name: "Read exact version" }));
  expect(desktopApi.atomicEntry).toHaveBeenCalledWith(1, artifact);
  expect(screen.getByText("<img src=x onerror=unsafe()>")).toBeTruthy();
  expect(document.querySelector(".hit-main img")).toBeNull();
  expect(document.querySelector(".reader-body img")).toBeNull();
  const clipboard = vi
    .spyOn(navigator.clipboard, "writeText")
    .mockResolvedValue();
  await user.click(screen.getByRole("button", { name: "Copy text" }));
  expect(clipboard).toHaveBeenLastCalledWith("<img src=x onerror=unsafe()>");
  await user.click(
    screen.getByRole("button", { name: "Copy exact reference" }),
  );
  expect(clipboard).toHaveBeenLastCalledWith(JSON.stringify(artifact, null, 2));

  expect(
    screen.getByText("The Server returned no source references."),
  ).toBeTruthy();
});
test("changing query discards a late response instead of showing old matches", async () => {
  const user = userEvent.setup();
  let finish!: (result: MemorySearchResponse) => void;
  vi.mocked(desktopApi.search).mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  render(
    <MemoryWorkspace
      state={state}
      language="en"
      onState={vi.fn()}
      onDirty={vi.fn()}
    />,
  );
  const query = screen.getByLabelText("Keyword search");
  await user.type(query, "old");
  await user.click(screen.getByRole("button", { name: "Search" }));
  await user.type(query, " new");
  await act(async () => {
    finish({
      hits: [{ artifact, text: "old secret", score: 1, matched_by: ["text"] }],
    });
  });
  expect(screen.queryByText("old secret")).toBeNull();
  expect(desktopApi.cancelMemory).toHaveBeenCalledWith(1);
});

test.each(["forbidden", "not_found", "network"])(
  "a %s detail failure clears private results and never reads latest",
  async (code) => {
    const user = userEvent.setup();
    vi.mocked(desktopApi.state).mockResolvedValue(state);
    vi.mocked(desktopApi.search).mockResolvedValue({
      hits: [
        {
          artifact,
          text: "private search excerpt",
          score: 1,
          matched_by: ["text"],
        },
      ],
    });
    vi.mocked(desktopApi.atomicEntry)
      .mockResolvedValueOnce({
        artifact,
        kind: "note",
        text: "private exact body",
        source_refs: [],
      })
      .mockRejectedValueOnce({ code });
    render(
      <MemoryWorkspace
        state={state}
        language="en"
        onState={vi.fn()}
        onDirty={vi.fn()}
      />,
    );
    await user.type(screen.getByLabelText("Keyword search"), "private");
    await user.click(screen.getByRole("button", { name: "Search" }));
    await user.click(
      screen.getByRole("button", { name: "Read exact version" }),
    );
    expect(screen.getByText("private exact body")).toBeTruthy();
    await user.click(
      screen.getByRole("button", { name: "Read exact version" }),
    );
    expect(screen.getByRole("alert")).toBeTruthy();
    expect(screen.queryByText("private exact body")).toBeNull();
    expect(screen.queryByText("private search excerpt")).toBeNull();
    expect(screen.queryByRole("button", { name: "Copy text" })).toBeNull();
    expect(desktopApi.atomicEntry).toHaveBeenCalledTimes(2);
    expect(desktopApi.atomicEntry).toHaveBeenLastCalledWith(1, artifact);
  },
);

test("overview refresh shows the latest readiness instead of the activation snapshot", () => {
  const active = state.active!;
  const ready = {
    ...active.report,
    readiness: { value: { status: "ready" as const, checks: {} }, error: null },
  };
  const latest = {
    ...ready,
    readiness: {
      value: { status: "degraded" as const, checks: {} },
      error: null,
    },
  };
  render(
    <Overview
      state={{
        ...state,
        active: { ...active, report: ready },
        reports: [latest],
      }}
      language="en"
      onNavigate={vi.fn()}
    />,
  );
  expect(screen.getByText("Service not ready")).toBeTruthy();
  expect(screen.queryByText("Service ready")).toBeNull();
});

test.each(["scope", "page"])(
  "late save after a %s switch preserves the new draft's navigation guard",
  async (switchKind) => {
    const user = userEvent.setup();
    let finish!: (value: WriteOutcome) => void;
    vi.mocked(desktopApi.remember).mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    vi.mocked(desktopApi.state).mockResolvedValue(state);

    render(<App />);
    await user.click(screen.getByRole("button", { name: "记忆" }));
    await waitFor(() =>
      expect(document.activeElement).toBe(
        screen.getByRole("heading", { level: 1, name: "记忆" }),
      ),
    );
    await user.type(screen.getByLabelText("记忆内容"), "draft A");
    await user.click(screen.getByRole("button", { name: "保存" }));
    if (switchKind === "scope") {
      const scopeB = { ...scope, scope_id: "scope-b", title: "Scope B" };
      const next = {
        ...state,
        generation: 2,
        active: { ...state.active!, generation: 2, scope: scopeB },
      };
      vi.mocked(desktopApi.scopes).mockResolvedValue({
        items: [scopeB],
        next_cursor: null,
      });
      vi.mocked(desktopApi.selectScope).mockResolvedValue(next);
      vi.mocked(desktopApi.state).mockResolvedValue(next);
      await user.click(screen.getByRole("button", { name: /Test scope/ }));
      await user.click(screen.getByRole("button", { name: "查找范围" }));
      await user.click(screen.getByRole("button", { name: "Scope B" }));
      await answerConfirmation(user, true);
    } else {
      await user.click(screen.getByRole("button", { name: "总览" }));
      await answerConfirmation(user, true);
      await waitFor(() =>
        expect(document.activeElement).toBe(
          screen.getByRole("heading", { level: 1, name: "总览" }),
        ),
      );
      await user.click(screen.getByRole("button", { name: "记忆" }));
      await waitFor(() =>
        expect(document.activeElement).toBe(
          screen.getByRole("heading", { level: 1, name: "记忆" }),
        ),
      );
    }
    await user.type(screen.getByLabelText("记忆内容"), "draft B");
    await act(async () => {
      finish(makeSaved(2).outcome);
    });
    await user.click(screen.getByRole("button", { name: "总览" }));
    await answerConfirmation(user, false);
    expect(
      (screen.getByLabelText("记忆内容") as HTMLTextAreaElement).value,
    ).toBe("draft B");
    expect(screen.queryAllByText("Atomic note 0")).toHaveLength(0);
    expect(
      screen.queryAllByRole("button", { name: "打开保存的记录" }),
    ).toHaveLength(0);
  },
);

test("confirming an unknown write after the Scope changes cannot submit the old draft", async () => {
  const user = userEvent.setup();
  const previous = { ...state, lastWrite: outcome("unknown").record };
  const props = {
    language: "en" as const,
    onState: vi.fn(),
    onDirty: vi.fn(),
    onReadSaved: vi.fn(),
    invalidateSaved: 0,
  };
  const { rerender } = render(<NoteForm {...props} state={previous} />);
  await user.type(screen.getByLabelText("Memory text"), "old Scope draft");
  await user.click(screen.getByRole("button", { name: "Save" }));
  expect(screen.getByRole("alertdialog").textContent).toContain(
    "may create a duplicate",
  );
  expect(desktopApi.remember).not.toHaveBeenCalled();
  rerender(
    <NoteForm
      {...props}
      state={{
        ...state,
        generation: 2,
        active: {
          ...state.active!,
          generation: 2,
          scope: { ...scope, scope_id: "scope-b" },
        },
      }}
    />,
  );
  await answerConfirmation(user, true);
  expect(desktopApi.remember).not.toHaveBeenCalled();
  expect(
    (screen.getByLabelText("Memory text") as HTMLTextAreaElement).value,
  ).toBe("");
});
