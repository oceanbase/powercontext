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
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {
  NoteForm,
  MemoryWorkspace,
  textBytes,
} from "../src/app/MemoryWorkspace";
import { App } from "../src/app/App";
import { Overview } from "../src/app/Overview";
import { desktopApi } from "../src/shared/ipc";
import type {
  DesktopState,
  MemoryCitation,
  MemorySearchResponse,
  AtomicMemoryRecord,
  AtomicMemoryRevision,
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
    entry: vi.fn(),
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
      supportedOperations: [],
    },
  },
  compatibilityProfiles: [],
  pendingCredentialCleanup: 0,
  lastWrite: null,
};
const citation: MemoryCitation = {
  memory_ref: { family: "memory", artifact_id: "memory-a", revision: 7 },
  entry_id: "entry-a",
  entry_version_id: "version-a",
};
const citationReference = { kind: "citation" as const, citation };
const atomicMemory: AtomicMemoryRecord = {
  artifact: { family: "atomic-memory", artifact_id: "atomic-a", revision: 7 },
  kind: "note",
  text: "<script>unsafe()</script>",
  state: "active",
  state_version: 2,
  merged_into_id: null,
};
const atomicReference = {
  kind: "artifact" as const,
  artifact: atomicMemory.artifact,
};
const atomicRevision: AtomicMemoryRevision = {
  scope_id: "scope-a",
  ...atomicMemory.artifact,
  content: {
    schema: "powercontext.atomic-memory.v1",
    kind: "note",
    text: "<img src=x onerror=unsafe()>",
    creation: null,
  },
  sources: [],
  artifacts: [],
  memory_citations: [],
  content_digest: `sha256:${"a".repeat(64)}`,
};
function outcome(status: "succeeded" | "unknown"): WriteOutcome {
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
    result:
      status === "succeeded"
        ? { memory: citation.memory_ref, entry: null }
        : null,
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
    />,
  );
}
test("a nullable save succeeds without inventing an entry or submitting on Enter", async () => {
  const user = userEvent.setup();
  form();
  vi.mocked(desktopApi.remember).mockResolvedValue(outcome("succeeded"));
  await user.type(screen.getByLabelText("Memory text"), "Café 中文{enter}note");
  expect(desktopApi.remember).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "Save" }));
  expect(desktopApi.remember).toHaveBeenCalledWith(1, "Café 中文\nnote");
  expect(screen.getByText(/Server returned no entry/)).toBeTruthy();
  expect(
    (screen.getByLabelText("Memory text") as HTMLTextAreaElement).value,
  ).toBe("");
});
test.each([0, 2])(
  "an Atomic save with %i records reports the actual write result",
  async (count) => {
    const user = userEvent.setup();
    form();
    const records = Array.from({ length: count }, (_, index) => ({
      ...atomicMemory,
      artifact: { ...atomicMemory.artifact, artifact_id: `atomic-${index}` },
    }));
    const result = outcome("succeeded");
    result.result = { changed: count > 0, records };
    result.record.references = records.map(({ artifact }) => ({
      kind: "artifact",
      artifact,
    }));
    vi.mocked(desktopApi.remember).mockResolvedValue(result);
    await user.type(screen.getByLabelText("Memory text"), "atomic note");
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(screen.getByRole("status").textContent).toContain(
      count ? "Saved successfully." : "Server returned no entry",
    );
    expect(
      (screen.getByLabelText("Memory text") as HTMLTextAreaElement).value,
    ).toBe("");
  },
);
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
test("unknown save retains the draft and does not replay automatically", async () => {
  const user = userEvent.setup();
  form();
  vi.mocked(desktopApi.remember).mockResolvedValue(outcome("unknown"));
  await user.type(screen.getByLabelText("Memory text"), "preserve me");
  await user.click(screen.getByRole("button", { name: "Save" }));
  expect(screen.getByRole("alert").textContent).toContain("outcome is unknown");
  expect(
    (screen.getByLabelText("Memory text") as HTMLTextAreaElement).value,
  ).toBe("preserve me");
  expect(desktopApi.remember).toHaveBeenCalledTimes(1);
});
test("search opens only the full citation and renders hostile text literally", async () => {
  const user = userEvent.setup();
  vi.mocked(desktopApi.search).mockResolvedValue({
    mode: null,
    hits: [
      {
        citation,
        text: "<script>unsafe()</script>",
        score: 1,
        matched_by: ["fts"],
      },
    ],
  });
  vi.mocked(desktopApi.entry).mockResolvedValue({
    citation,
    version: 1,
    kind: "note",
    text: "<img src=x onerror=unsafe()>",
    state: "active",
    source_refs: [],
    artifact_refs: [],
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
  expect(desktopApi.entry).toHaveBeenCalledWith(1, citationReference);
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
  expect(clipboard).toHaveBeenLastCalledWith(JSON.stringify(citation, null, 2));

  expect(
    screen.getByText("The Server returned no source references."),
  ).toBeTruthy();
});
test("Atomic search reads its exact artifact revision and copies the complete artifact reference", async () => {
  const user = userEvent.setup();
  vi.mocked(desktopApi.search).mockResolvedValue({
    mode: "text",
    hits: [{ memory: atomicMemory, score: 1, matched_by: ["text"] }],
  });
  vi.mocked(desktopApi.entry).mockResolvedValue({
    ...atomicRevision,
    sources: [{ source_type: "document", source_id: "source-a" }],
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
  expect(screen.getByText("Full-text match")).toBeTruthy();
  await user.click(screen.getByRole("button", { name: "Read exact version" }));
  expect(desktopApi.entry).toHaveBeenCalledWith(1, atomicReference);
  expect(screen.getByText(atomicRevision.content.text)).toBeTruthy();
  expect(document.querySelector(".hit-main img")).toBeNull();
  expect(document.querySelector(".reader-body img")).toBeNull();
  expect(screen.getByText("source-a")).toBeTruthy();
  expect(screen.queryByText("Entry ID")).toBeNull();
  expect(screen.queryByText("Entry version")).toBeNull();
  expect(screen.queryByText("Active")).toBeNull();
  const clipboard = vi
    .spyOn(navigator.clipboard, "writeText")
    .mockResolvedValue();
  await user.click(screen.getByRole("button", { name: "Copy text" }));
  expect(clipboard).toHaveBeenLastCalledWith(atomicRevision.content.text);
  await user.click(
    screen.getByRole("button", { name: "Copy exact reference" }),
  );
  expect(clipboard).toHaveBeenLastCalledWith(
    JSON.stringify(atomicMemory.artifact, null, 2),
  );
});
test.each(["forbidden", "not_found", "network"])(
  "an Atomic %s detail failure clears private results without substituting latest",
  async (code) => {
    const user = userEvent.setup();
    vi.mocked(desktopApi.state).mockResolvedValue(state);
    vi.mocked(desktopApi.search).mockResolvedValue({
      mode: "text",
      hits: [{ memory: atomicMemory, score: 1, matched_by: ["text"] }],
    });
    vi.mocked(desktopApi.entry)
      .mockResolvedValueOnce(atomicRevision)
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
    expect(screen.getByText(atomicRevision.content.text)).toBeTruthy();
    await user.click(
      screen.getByRole("button", { name: "Read exact version" }),
    );
    expect(screen.getByRole("alert")).toBeTruthy();
    expect(screen.queryByText(atomicRevision.content.text)).toBeNull();
    expect(screen.queryByText(atomicMemory.text)).toBeNull();
    expect(screen.queryByRole("button", { name: "Copy text" })).toBeNull();
    expect(desktopApi.entry).toHaveBeenCalledTimes(2);
    expect(desktopApi.entry).toHaveBeenLastCalledWith(1, atomicReference);
  },
);
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
      mode: null,
      hits: [{ citation, text: "old secret", score: 1, matched_by: ["fts"] }],
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
      mode: null,
      hits: [
        {
          citation,
          text: "private search excerpt",
          score: 1,
          matched_by: ["fts"],
        },
      ],
    });
    vi.mocked(desktopApi.entry)
      .mockResolvedValueOnce({
        citation,
        version: 7,
        kind: "note",
        text: "private exact body",
        state: "active",
        source_refs: [],
        artifact_refs: [],
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
    expect(desktopApi.entry).toHaveBeenCalledTimes(2);
    expect(desktopApi.entry).toHaveBeenLastCalledWith(1, citationReference);
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
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
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
    } else {
      await user.click(screen.getByRole("button", { name: "总览" }));
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
      finish(outcome("succeeded"));
    });
    confirm.mockClear().mockReturnValue(false);
    await user.click(screen.getByRole("button", { name: "总览" }));
    expect(confirm).toHaveBeenCalled();
    expect(
      (screen.getByLabelText("记忆内容") as HTMLTextAreaElement).value,
    ).toBe("draft B");
  },
);
