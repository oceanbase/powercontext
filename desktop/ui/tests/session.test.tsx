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
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SessionView } from "../src/app/SessionView";
import { sessionMessages } from "../src/app/session-messages";
import { desktopApi } from "../src/shared/ipc";
import { emptySessionSummary, recordedSessionSummary } from "./session-fixture";
import { answerConfirmation } from "./confirmation";

vi.mock("../src/shared/ipc", () => ({
  desktopApi: {
    sessionSummary: vi.fn(),
    resetSessionObservations: vi.fn(),
    previewSessionDiagnostics: vi.fn(),
  },
}));
const safeText = '{\n  "schemaVersion": 2,\n  "note": "安全快照"\n}\n';
beforeEach(() => {
  vi.mocked(desktopApi.sessionSummary).mockResolvedValue(emptySessionSummary());
  vi.mocked(desktopApi.resetSessionObservations).mockResolvedValue();
  vi.mocked(desktopApi.previewSessionDiagnostics).mockResolvedValue(safeText);
});
afterEach(() => {
  cleanup();
  vi.resetAllMocks();
  vi.restoreAllMocks();
});

for (const language of ["zh", "en"] as const) {
  test(`session scope, retention, loading and empty state work offline (${language})`, async () => {
    let finish!: (value: ReturnType<typeof emptySessionSummary>) => void;
    vi.mocked(desktopApi.sessionSummary).mockReturnValueOnce(
      new Promise((resolve) => {
        finish = resolve;
      }),
    );
    render(<SessionView language={language} />);
    const t = sessionMessages[language];
    expect(screen.getByRole("status").textContent).toBe(t.loading);
    finish(emptySessionSummary());
    expect(await screen.findByRole("heading", { name: t.empty })).toBeTruthy();
    expect(screen.getByText(t.scope)).toBeTruthy();
    expect(screen.getByText(t.bounds)).toBeTruthy();
    expect(screen.getByText(t.opsEmpty)).toBeTruthy();
    expect(screen.getByText("512")).toBeTruthy();
    expect(screen.getByText("2026-10-25 00:00:00")).toBeTruthy();
    expect(screen.getByText("2026-10-25 01:00:00")).toBeTruthy();
    expect(screen.getByText("2026-10-25 02:00:00")).toBeTruthy();
    expect(screen.queryByRole("table")).toBeNull();
    expect(desktopApi.previewSessionDiagnostics).not.toHaveBeenCalled();
  });

  test(`HTTP and native action outcomes, units, latency and incomplete coverage remain distinct (${language})`, async () => {
    vi.mocked(desktopApi.sessionSummary).mockResolvedValue(
      recordedSessionSummary(),
    );
    render(<SessionView language={language} />);
    const t = sessionMessages[language];
    const http = await screen.findByRole("table", { name: t.http });
    const actions = screen.getByRole("table", { name: t.actions });
    expect(
      within(http)
        .getAllByRole("cell")
        .map((cell) => cell.textContent),
    ).toEqual(["4", "2", "1", "1", "0", "10", "25", "40"]);
    expect(
      within(actions)
        .getAllByRole("cell")
        .map((cell) => cell.textContent),
    ).toEqual(["4", "1", "1", "0", "2", "20", "35", "50"]);
    expect(
      within(http).getByRole("columnheader", { name: t.mean }),
    ).toBeTruthy();
    expect(screen.getByRole("alert").textContent).toContain(t.incompleteHint);
    expect(screen.getByText(t.outcomesHint)).toBeTruthy();
    expect(screen.getByText(t.httpHint)).toBeTruthy();
    expect(screen.getByText(t.actionHint)).toBeTruthy();
    expect(screen.getByText(t.operations.get_access_principal)).toBeTruthy();
  });
}

test("failed or unsupported snapshots never fabricate zero statistics or render raw errors", async () => {
  const user = userEvent.setup();
  vi.mocked(desktopApi.sessionSummary).mockRejectedValueOnce(
    new Error("private-token C:/private/path"),
  );
  render(<SessionView language="en" />);
  expect(await screen.findByRole("alert")).toBeTruthy();
  expect(document.body.textContent).not.toContain("private-token");
  expect(screen.queryByRole("table")).toBeNull();
  expect(screen.queryByText("512")).toBeNull();
  vi.mocked(desktopApi.sessionSummary).mockResolvedValueOnce({
    ...emptySessionSummary(),
    schemaVersion: 99,
  });
  await user.click(
    screen.getByRole("button", { name: "Retry reading summary" }),
  );
  expect(
    await screen.findByRole("heading", {
      name: "This summary version cannot be displayed",
    }),
  ).toBeTruthy();
  expect(
    (
      screen.getByRole("button", {
        name: "Preview safe diagnostic summary",
      }) as HTMLButtonElement
    ).disabled,
  ).toBe(true);
  await user.click(
    screen.getByRole("button", { name: "Refresh session summary" }),
  );
  expect(
    await screen.findByRole("heading", {
      name: "No completed observations yet",
    }),
  ).toBeTruthy();
});

test("preview fetch is explicit and copy preserves exactly the inspected native text", async () => {
  const user = userEvent.setup();
  const clipboard = vi
    .spyOn(navigator.clipboard, "writeText")
    .mockResolvedValue();
  render(<SessionView language="en" />);
  await screen.findByRole("heading", { name: "No completed observations yet" });
  const trigger = screen.getByRole("button", {
    name: "Preview safe diagnostic summary",
  });
  await user.click(trigger);
  const text = (await screen.findByRole("textbox", {
    name: "Safe diagnostic text to copy",
  })) as HTMLTextAreaElement;
  expect(text.value).toBe(safeText);
  expect(text.readOnly).toBe(true);
  expect(clipboard).not.toHaveBeenCalled();
  expect(document.activeElement).toBe(
    screen.getByRole("button", { name: "Close preview" }),
  );
  await user.tab({ shift: true });
  expect(document.activeElement).toBe(
    screen.getByRole("button", { name: "Copy preview text" }),
  );
  await user.keyboard("{Enter}");
  expect(clipboard).toHaveBeenCalledExactlyOnceWith(safeText);
  expect(desktopApi.previewSessionDiagnostics).toHaveBeenCalledTimes(1);
  expect(screen.getByRole("status").textContent).toBe(
    "Safe diagnostic summary copied.",
  );
  await user.keyboard("{Escape}");
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(document.activeElement).toBe(trigger);
});

test("failed preview can be retried without copying or exposing native errors", async () => {
  const user = userEvent.setup();
  const clipboard = vi
    .spyOn(navigator.clipboard, "writeText")
    .mockResolvedValue();
  vi.mocked(desktopApi.previewSessionDiagnostics).mockRejectedValueOnce(
    new Error("secret endpoint"),
  );
  render(<SessionView language="en" />);
  await screen.findByRole("heading", { name: "No completed observations yet" });
  await user.click(
    screen.getByRole("button", { name: "Preview safe diagnostic summary" }),
  );
  expect(await screen.findByRole("alert")).toBeTruthy();
  expect(screen.queryByRole("textbox")).toBeNull();
  expect(
    screen.queryByRole("button", { name: "Copy preview text" }),
  ).toBeNull();
  expect(document.body.textContent).not.toContain("secret endpoint");
  await user.click(
    screen.getByRole("button", { name: "Generate preview again" }),
  );
  expect(await screen.findByRole("textbox")).toBeTruthy();
  expect(clipboard).not.toHaveBeenCalled();
});

test.each(["zh", "en"] as const)(
  "copy failure preserves selectable text and supports an explicit retry (%s)",
  async (language) => {
    const user = userEvent.setup();
    const clipboard = vi
      .spyOn(navigator.clipboard, "writeText")
      .mockRejectedValueOnce(new Error("private clipboard error"))
      .mockResolvedValue();
    render(<SessionView language={language} />);
    const t = sessionMessages[language];
    await screen.findByRole("heading", { name: t.empty });
    await user.click(screen.getByRole("button", { name: t.preview }));
    const text = (await screen.findByRole("textbox", {
      name: t.previewText,
    })) as HTMLTextAreaElement;
    await user.click(screen.getByRole("button", { name: t.copy }));
    expect(screen.getByRole("alert").textContent).toBe(t.copyFailed);
    expect(document.body.textContent).not.toContain("private clipboard error");
    await user.click(text);
    expect(text.selectionStart).toBe(0);
    expect(text.selectionEnd).toBe(safeText.length);
    await user.click(screen.getByRole("button", { name: t.copy }));
    expect(screen.getByRole("status").textContent).toBe(t.copied);
    expect(clipboard).toHaveBeenLastCalledWith(safeText);
  },
);

test.each(["zh", "en"] as const)(
  "local setup diagnostics show safe phases, opaque references and distinct Ops outcomes (%s)",
  async (language) => {
    const user = userEvent.setup();
    const snapshot = emptySessionSummary();
    snapshot.installerOps = {
      capacity: 64,
      evicted: 2,
      droppedSinceStart: 1,
      recent: (
        [
          "failed",
          "partial_failure",
          "uncertain",
          "unknown",
          "cancelled",
          "pending",
          "succeeded",
          "rejected",
        ] as const
      ).map((outcome, index) => ({
        action: "apply",
        operationId: `2c597e84-6361-4f20-85aa-${String(index).padStart(12, "0")}`,
        attempt: 1,
        phase: index === 3 ? null : "preparing",
        attemptPhase: "finished",
        outcome,
        errorCode: outcome === "failed" ? "resource_prepare_failed" : null,
        bridgeError: outcome === "unknown" ? "timeout" : null,
        observedAtMs: snapshot.generatedAtMs,
      })),
    };
    vi.mocked(desktopApi.sessionSummary).mockResolvedValue(snapshot);
    const text = JSON.stringify(snapshot, null, 2) + "\n";
    vi.mocked(desktopApi.previewSessionDiagnostics).mockResolvedValue(text);
    const copy = vi.spyOn(navigator.clipboard, "writeText").mockResolvedValue();
    render(<SessionView language={language} />);
    const t = sessionMessages[language];
    const table = await screen.findByRole("table", { name: t.localOps });
    expect(
      within(table)
        .getAllByRole("rowheader")
        .map((item) => item.textContent),
    ).toEqual(snapshot.installerOps.recent.map((record) => record.operationId));
    for (const record of snapshot.installerOps.recent) {
      const row = within(table)
        .getByRole("rowheader", { name: record.operationId })
        .closest("tr")!;
      expect(within(row).getByText(t.opsOutcomes[record.outcome])).toBeTruthy();
      expect(
        within(row).getByText(
          record.phase ? t.opsPhases[record.phase] : t.notObserved,
        ),
      ).toBeTruthy();
    }
    expect(within(table).getByText("resource_prepare_failed")).toBeTruthy();
    expect(within(table).getByText("timeout")).toBeTruthy();
    expect(screen.getByText(t.opsIncomplete)).toBeTruthy();
    expect(screen.queryByRole("table", { name: t.http })).toBeNull();
    expect(screen.queryByRole("table", { name: t.actions })).toBeNull();
    await user.click(screen.getByRole("button", { name: t.preview }));
    await screen.findByRole("textbox", { name: t.previewText });
    expect(copy).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: t.copy }));
    await waitFor(() => expect(copy).toHaveBeenCalledWith(text));
  },
);

test("an unattached Ops source is unavailable rather than evidence of zero setup failures", async () => {
  const snapshot = emptySessionSummary();
  snapshot.coverage.installerOps = false;
  vi.mocked(desktopApi.sessionSummary).mockResolvedValue(snapshot);
  render(<SessionView language="en" />);
  expect(
    await screen.findByText(sessionMessages.en.localOpsUnavailable),
  ).toBeTruthy();
  expect(screen.queryByText(sessionMessages.en.opsEmpty)).toBeNull();
});

test("late preview and clipboard responses cannot populate a new view or preview", async () => {
  const user = userEvent.setup();
  let finishPreview!: (text: string) => void;
  vi.mocked(desktopApi.previewSessionDiagnostics).mockReturnValueOnce(
    new Promise((resolve) => {
      finishPreview = resolve;
    }),
  );
  let finishCopy!: () => void;
  vi.spyOn(navigator.clipboard, "writeText").mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        finishCopy = resolve;
      }),
  );
  const view = render(<SessionView key="original-context" language="en" />);
  await screen.findByRole("heading", { name: "No completed observations yet" });
  await user.click(
    screen.getByRole("button", { name: "Preview safe diagnostic summary" }),
  );
  expect(screen.getByRole("status").textContent).toBe(
    "Generating safe diagnostic preview…",
  );
  view.rerender(<SessionView key="new-context" language="en" />);
  finishPreview("old snapshot must stay closed");
  await screen.findByRole("heading", { name: "No completed observations yet" });
  expect(screen.queryByRole("dialog")).toBeNull();
  await user.click(
    screen.getByRole("button", { name: "Preview safe diagnostic summary" }),
  );
  await screen.findByRole("textbox");
  await user.click(screen.getByRole("button", { name: "Copy preview text" }));
  expect(
    (screen.getByRole("button", { name: "Copying…" }) as HTMLButtonElement)
      .disabled,
  ).toBe(true);
  await user.keyboard("{Escape}");
  await user.click(
    screen.getByRole("button", { name: "Preview safe diagnostic summary" }),
  );
  await screen.findByRole("textbox");
  finishCopy();
  await waitFor(() =>
    expect(screen.queryByText("Safe diagnostic summary copied.")).toBeNull(),
  );
});

test("reset requires confirmation, reloads the native window and preserves reported lifetime loss", async () => {
  const user = userEvent.setup();
  vi.mocked(desktopApi.sessionSummary).mockResolvedValueOnce(
    recordedSessionSummary(),
  );
  render(<SessionView language="en" />);
  await screen.findByRole("table", { name: "HTTP requests" });
  await user.click(
    screen.getByRole("button", { name: "Reset session observations" }),
  );
  expect(screen.getByRole("alertdialog").textContent).toContain(
    "Memory and running operations are unaffected",
  );
  await answerConfirmation(user, false);
  expect(desktopApi.resetSessionObservations).not.toHaveBeenCalled();
  vi.mocked(desktopApi.sessionSummary).mockResolvedValueOnce({
    ...emptySessionSummary(),
    droppedSinceStart: 9,
  });
  await user.click(
    screen.getByRole("button", { name: "Reset session observations" }),
  );
  await answerConfirmation(user, true);
  expect(
    await screen.findByRole("heading", {
      name: "No completed observations yet",
    }),
  ).toBeTruthy();
  expect(screen.getByText("9")).toBeTruthy();
  expect(screen.getByText(sessionMessages.en.resetDone)).toBeTruthy();
  expect(screen.getByRole("alert").textContent).toContain(
    sessionMessages.en.incomplete,
  );
});

test("a failed reset preserves the visible snapshot and asks for explicit inspection", async () => {
  const user = userEvent.setup();
  vi.mocked(desktopApi.sessionSummary).mockResolvedValue(
    recordedSessionSummary(),
  );
  vi.mocked(desktopApi.resetSessionObservations).mockRejectedValue(
    new Error("private reset details"),
  );
  render(<SessionView language="en" />);
  await screen.findByRole("table", { name: "HTTP requests" });
  await user.click(
    screen.getByRole("button", { name: "Reset session observations" }),
  );
  await answerConfirmation(user, true);
  expect(await screen.findByText(sessionMessages.en.resetFailed)).toBeTruthy();
  expect(screen.getByRole("table", { name: "HTTP requests" })).toBeTruthy();
  expect(document.body.textContent).not.toContain("private reset details");
  expect(desktopApi.sessionSummary).toHaveBeenCalledTimes(1);
});

test.each(["zh", "en"] as const)(
  "Server discovery observations have localized coverage and row names (%s)",
  async (language) => {
    const user = userEvent.setup();
    vi.mocked(desktopApi.sessionSummary).mockResolvedValue({
      ...emptySessionSummary(),
      coverage: {
        ...emptySessionSummary().coverage,
        httpOperations: ["get_server_info"],
      },
      operations: [
        {
          unit: "http_request",
          operation: "get_server_info",
          sampleCount: 1,
          outcomes: { success: 1, failure: 0, cancelled: 0, unknown: 0 },
          minDurationMs: 5,
          meanDurationMs: 5,
          maxDurationMs: 5,
        },
      ],
      recent: [
        {
          unit: "http_request",
          operation: "get_server_info",
          outcome: "success",
          completedAtMs: Date.UTC(2026, 9, 31),
          durationMs: 5,
          httpStatus: 200,
          errorCode: null,
        },
      ],
    });
    render(<SessionView language={language} />);
    const t = sessionMessages[language];
    const label =
      language === "zh" ? "读取 Server 信息" : "Read Server information";
    const table = await screen.findByRole("table", { name: t.http });
    expect(within(table).getByRole("rowheader").textContent).toContain(label);
    await user.click(screen.getByText(t.operationCoverage));
    const coverage = screen.getByText(t.operationCoverage).closest("details")!;
    expect(
      within(coverage)
        .getAllByRole("listitem")
        .map((item) => item.textContent),
    ).toContain(label);
  },
);
