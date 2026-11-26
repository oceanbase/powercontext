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

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { isTauri } from "@tauri-apps/api/core";
import { desktopApi } from "../src/shared/ipc";
import { Overview } from "../src/app/Overview";
import { TopBar } from "../src/app/TopBar";
import {
  previewDesktopState,
  previewDataScopeId,
  previewEmptyScopeId,
} from "../src/app/preview-data";

vi.mock("@tauri-apps/api/core", () => ({ isTauri: vi.fn(() => true) }));
vi.mock("../src/shared/ipc", () => ({
  desktopApi: {
    cancelStats: vi.fn().mockResolvedValue(undefined),
    stats: vi.fn(),
  },
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  vi.mocked(isTauri).mockReturnValue(true);
});

function overviewProps(
  state = previewDesktopState(null, "zh", undefined, previewDataScopeId),
) {
  return {
    state,
    language: "zh" as const,
    onState: vi.fn(),
    onOpenLocalServer: vi.fn(),
    contextPending: false,
  };
}

test("keeps the space overview visible when the active service is unavailable", () => {
  const state = previewDesktopState(null, "zh", undefined, previewDataScopeId);
  const failure = {
    code: "network" as const,
    requestId: null,
    dispatched: true,
  };
  state.active!.report = {
    ...state.active!.report,
    liveness: { value: null, error: failure },
    compatibilityVerified: false,
  };
  state.reports = [state.active!.report];

  render(<Overview {...overviewProps(state)} />);

  expect(screen.getByRole("heading", { name: "空间概览" })).toBeTruthy();
  expect(screen.queryByText("开始使用 PowerContext")).toBeNull();
});

test.each([false, true])(
  "renders empty preview statistics in browser and desktop (Tauri: %s)",
  (native) => {
    vi.mocked(isTauri).mockReturnValue(native);
    const state = previewDesktopState(null, "zh");
    const { container, rerender } = render(
      <Overview {...overviewProps(state)} />,
    );

    expect(screen.getByRole("heading", { name: "记忆概况" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "使用成效" })).toBeTruthy();
    expect(screen.getByText("当前周期还没有使用记录")).toBeTruthy();
    expect(container.querySelectorAll(".memory-summary-card dd")).toHaveLength(
      2,
    );
    for (const value of container.querySelectorAll(
      ".memory-summary-card dd, .metric-strip dd",
    ))
      expect(value.textContent).toBe("0");
    expect(container.querySelector('[data-empty="true"]')).toBeTruthy();
    expect(screen.queryByText(/当前服务暂不支持空间统计/)).toBeNull();

    rerender(
      <Overview
        {...overviewProps(
          previewDesktopState(null, "zh", undefined, previewDataScopeId),
        )}
      />,
    );
    expect(screen.getByText("277")).toBeTruthy();
    expect(
      container.querySelector('[data-agent-connected="true"]'),
    ).toBeTruthy();

    rerender(
      <Overview
        {...overviewProps(
          previewDesktopState(null, "zh", undefined, previewEmptyScopeId),
        )}
      />,
    );
    expect(screen.getByText("当前周期还没有使用记录")).toBeTruthy();
    expect(desktopApi.stats).not.toHaveBeenCalled();
  },
);

test("keeps unsupported statistics feedback for a real service", () => {
  vi.mocked(isTauri).mockReturnValue(false);
  const state = previewDesktopState(null, "zh");
  state.active!.connectionId = "real-local-service";
  render(<Overview {...overviewProps(state)} />);

  expect(screen.getByText(/当前服务暂不支持空间统计/)).toBeTruthy();
  expect(screen.queryByRole("heading", { name: "使用成效" })).toBeNull();
  expect(desktopApi.stats).not.toHaveBeenCalled();
});

test("expresses an unavailable service through the global red status dot", () => {
  const state = previewDesktopState(null, "zh", undefined, previewDataScopeId);
  const failure = {
    code: "network" as const,
    requestId: null,
    dispatched: true,
  };
  state.active!.report = {
    ...state.active!.report,
    readiness: { value: null, error: failure },
  };
  state.reports = [state.active!.report];

  const { container } = render(
    <TopBar state={state} language="zh" onManageConnections={vi.fn()} />,
  );

  expect(
    screen.getByRole("button", {
      name: /当前服务.*服务异常/,
    }),
  ).toBeTruthy();
  expect(container.querySelector(".service-status-dot-danger")).toBeTruthy();
});
