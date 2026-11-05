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
import { act, cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Statistics } from "../src/app/Statistics";
import { MemoryWorkspace } from "../src/app/MemoryWorkspace";
import { desktopApi } from "../src/shared/ipc";
import type { DesktopState, ExactScopeStatistics } from "../src/generated/ipc";

vi.mock("../src/shared/ipc", () => ({
  desktopApi: {
    stats: vi.fn(),
    state: vi.fn(),
    cancelStats: vi.fn().mockResolvedValue(undefined),
    cancelMemory: vi.fn().mockResolvedValue(undefined),
  },
}));
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const state: DesktopState = {
  generation: 1,
  profiles: [],
  reports: [],
  compatibilityProfiles: [],
  pendingCredentialCleanup: 0,
  lastWrite: null,
  active: {
    connectionId: "connection-a",
    generation: 1,
    scope: {
      scope_id: "scope-a",
      title: "Scope A",
      summary: "",
      context_references: [],
      external_references: [],
      version: 1,
    },
    report: {
      connectionId: "connection-a",
      revision: 1,
      checkedAt: 1,
      compatibilityVerified: true,
      anonymousAccess: true,
      supportedOperations: ["get_stats", "remember_memory"],
      liveness: { value: null, error: null },
      readiness: { value: null, error: null },
      identity: { value: null, error: null },
      capabilities: { value: null, error: null },
    },
  },
};
function statistics(): ExactScopeStatistics {
  const zero = { requests: 0, input_tokens: 0, output_tokens: 0 };
  const period = {
    preset: "30d" as const,
    start_date: "2026-09-25",
    end_date: "2026-10-24",
    timezone: "UTC" as const,
  };
  const usage = {
    period,
    totals: { generation: { ...zero }, embedding: { ...zero } },
    daily: [
      {
        date: "2026-10-24",
        generation: { ...zero },
        embedding: { ...zero },
        by_purpose: [],
      },
    ],
    by_purpose: [],
  };
  const recallTotals = {
    preparations: 0,
    ready_preparations: 0,
    comparable_preparations: 0,
    baseline_tokens: 0,
    recalled_tokens: 0,
    token_reduction: 0,
  };
  return {
    selection: { mode: "exact", scope_ids: ["scope-a"] },
    scope_ids: ["scope-a"],
    as_of: "2026-10-24T00:01:00Z",
    inventory: {
      sources: { total: 0, memory_processed: 0, memory_pending: 0 },
      artifacts: { total: 0, by_family: [] },
      candidates: {
        total: 0,
        pending: 0,
        approved: 0,
        rejected: 0,
        by_family: [],
      },
      memory: { entries: { total: 0, active: 0, inactive: 0, by_kind: [] } },
    },
    usage,
    recall: {
      period,
      estimator: null,
      totals: { ...recallTotals },
      daily: [{ date: "2026-10-24", ...recallTotals }],
    },
    by_scope: [],
  };
}
const props = { state, language: "en" as const, onState: vi.fn() };

test("statistics require an exact Scope and operation qualification without fabricating zero counts", () => {
  const view = render(<Statistics {...props} state={null} />);
  expect(screen.getByText(/Activate a connection/)).toBeTruthy();
  expect(screen.queryByRole("table")).toBeNull();
  view.rerender(
    <Statistics
      {...props}
      state={{
        ...state,
        active: {
          ...state.active!,
          report: { ...state.active!.report, supportedOperations: [] },
        },
      }}
    />,
  );
  expect(screen.getByText(/Statistics are not qualified/)).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Load statistics" })).toBeNull();
  expect(desktopApi.stats).not.toHaveBeenCalled();
});

test.each(["en", "zh"] as const)(
  "a pending context transition hides statistics and discards its late response in %s",
  async (language) => {
    const user = userEvent.setup();
    const localized = { ...props, language };
    const loadLabel = language === "en" ? "Load statistics" : "加载统计";
    let finish!: (value: ExactScopeStatistics) => void;
    vi.mocked(desktopApi.stats).mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    const view = render(<Statistics {...localized} />);
    await user.click(screen.getByRole("button", { name: loadLabel }));
    view.rerender(<Statistics {...localized} contextPending />);
    expect(screen.queryByRole("button", { name: loadLabel })).toBeNull();
    expect(screen.queryByRole("table")).toBeNull();
    expect(screen.getByRole("status").textContent).toBe(
      language === "en"
        ? "Switching connection or Scope…"
        : "正在切换连接或范围…",
    );
    expect(
      screen.queryByText(
        language === "en"
          ? /Statistics are not qualified/
          : /此连接尚无统计操作/,
      ),
    ).toBeNull();
    // Even a failed transition that leaves the original generation must not
    // revive a response started before that transition.
    view.rerender(<Statistics {...localized} />);
    await act(async () => finish(statistics()));
    expect(screen.queryByRole("table")).toBeNull();
    expect(
      screen.getByText(language === "en" ? /not been loaded/ : /尚未加载/),
    ).toBeTruthy();
    expect(desktopApi.stats).toHaveBeenCalledTimes(1);
  },
);

test.each(["en", "zh"] as const)(
  "empty inventory preserves real zeros, UTC dates and chart tables in %s",
  async (language) => {
    const user = userEvent.setup();
    vi.mocked(desktopApi.stats).mockResolvedValue(statistics());
    render(<Statistics {...props} language={language} />);
    expect(screen.queryByRole("table")).toBeNull();
    await user.click(
      screen.getByRole("button", {
        name: language === "en" ? "Load statistics" : "加载统计",
      }),
    );
    expect(desktopApi.stats).toHaveBeenCalledWith(1, "30d");
    expect(await screen.findByText("2026-10-24T00:01:00Z")).toBeTruthy();
    const inventory = screen.getByRole("table", {
      name: language === "en" ? "Inventory snapshot" : "存量快照",
    });
    const source = within(inventory).getAllByRole("row")[1];
    expect(within(source).getByRole("cell").textContent).toBe("0");
    const daily =
      language === "en" ? "Daily model usage (UTC)" : "每日模型用量（UTC）";
    expect(screen.getByRole("table", { name: daily })).toBeTruthy();
    expect(
      screen.getByRole("img", {
        name: new RegExp(daily.replace(/[()]/g, "\\$&")),
      }),
    ).toBeTruthy();
  },
);

test("partial token reports stay unknown and signed recall estimates remain signed", async () => {
  const user = userEvent.setup();
  const data = statistics();
  data.usage.totals.generation = {
    requests: 2,
    input_tokens: null,
    output_tokens: 9,
  };
  data.usage.daily[0].generation = { ...data.usage.totals.generation };
  data.recall.totals = {
    preparations: 3,
    ready_preparations: 2,
    comparable_preparations: 1,
    baseline_tokens: 3,
    recalled_tokens: 8,
    token_reduction: -5,
  };
  data.recall.daily[0] = { date: "2026-10-24", ...data.recall.totals };
  vi.mocked(desktopApi.stats).mockResolvedValue(data);
  render(<Statistics {...props} />);
  await user.click(screen.getByRole("button", { name: "Load statistics" }));
  const usage = await screen.findByRole("table", {
    name: "Recorded model usage totals",
  });
  const row = within(usage).getByRole("row", {
    name: "Generation 2 Unknown 9",
  });
  expect(within(row).getByRole("cell", { name: "Unknown" })).toBeTruthy();
  const recall = screen.getByRole("table", { name: "Recall token estimates" });
  expect(
    screen.getByRole("img", {
      name: "Daily recall estimates (UTC) · Token reduction (estimate)",
    }),
  ).toBeTruthy();
  expect(
    within(recall).getByRole("row", { name: "Token reduction (estimate) -5" }),
  ).toBeTruthy();
  await user.selectOptions(
    screen.getByLabelText("Daily chart metric"),
    "input_tokens",
  );
  expect(
    screen.getByRole("img", { name: "Daily model usage (UTC) · Input tokens" })
      .textContent,
  ).toContain("Unknown");
});

test.each(["scope", "connection"])(
  "a %s switch hides private statistics and ignores a late response",
  async (kind) => {
    const user = userEvent.setup();
    let finish!: (value: ExactScopeStatistics) => void;
    vi.mocked(desktopApi.stats)
      .mockResolvedValueOnce(statistics())
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            finish = resolve;
          }),
      );
    const view = render(<Statistics {...props} />);
    await user.click(screen.getByRole("button", { name: "Load statistics" }));
    expect(
      screen.getByRole("table", { name: "Inventory snapshot" }),
    ).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Load statistics" }));
    const next: DesktopState = {
      ...state,
      generation: 2,
      active: {
        ...state.active!,
        generation: 2,
        connectionId: kind === "connection" ? "connection-b" : "connection-a",
        scope: {
          ...state.active!.scope!,
          scope_id: "scope-b",
          title: "Scope B",
        },
      },
    };
    view.rerender(<Statistics {...props} state={next} />);
    await act(async () => {
      finish(statistics());
    });
    expect(screen.queryByText("2026-10-24T00:01:00Z")).toBeNull();
    expect(screen.queryByRole("table")).toBeNull();
    expect(desktopApi.cancelStats).toHaveBeenCalledWith(1);
    expect(screen.getByText(/Statistics have not been loaded/)).toBeTruthy();
  },
);

test("changing periods ignores late failure and refreshes only the selected period", async () => {
  const user = userEvent.setup();
  let fail!: (error: unknown) => void;
  const today = statistics();
  today.usage.period = {
    ...today.usage.period,
    preset: "today",
    start_date: "2026-10-24",
  };
  today.recall.period = { ...today.usage.period };
  vi.mocked(desktopApi.stats)
    .mockImplementationOnce(
      () =>
        new Promise((_, reject) => {
          fail = reject;
        }),
    )
    .mockResolvedValueOnce(today);
  render(<Statistics {...props} />);
  await user.click(screen.getByRole("button", { name: "Load statistics" }));
  await user.selectOptions(
    screen.getByLabelText("Usage period (UTC)"),
    "today",
  );
  await act(async () => {
    fail({ code: "forbidden" });
  });
  expect(screen.queryByRole("alert")).toBeNull();
  await user.click(screen.getByRole("button", { name: "Load statistics" }));
  expect(desktopApi.stats).toHaveBeenLastCalledWith(1, "today");
});

test.each(["forbidden", "network"])(
  "a %s statistics failure clears private data without disabling Memory or widening selection",
  async (code) => {
    const user = userEvent.setup();
    vi.mocked(desktopApi.stats)
      .mockResolvedValueOnce(statistics())
      .mockRejectedValueOnce({ code });
    vi.mocked(desktopApi.state).mockResolvedValue(state);
    render(
      <>
        <Statistics {...props} />
        <MemoryWorkspace {...props} onDirty={vi.fn()} />
      </>,
    );
    await user.click(screen.getByRole("button", { name: "Load statistics" }));
    await user.click(screen.getByRole("button", { name: "Load statistics" }));
    expect(await screen.findByRole("alert")).toBeTruthy();
    expect(screen.queryByRole("table")).toBeNull();
    expect(desktopApi.stats).toHaveBeenCalledTimes(2);
    await user.type(screen.getByLabelText("Memory text"), "still available");
    expect(
      (screen.getByRole("button", { name: "Save" }) as HTMLButtonElement)
        .disabled,
    ).toBe(false);
  },
);
