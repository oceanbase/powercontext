/* Copyright (c) 2026 OceanBase. Licensed under the Apache License, Version 2.0. */

import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";
import { CreateSpaceDialog } from "../src/app/App";
import { previewDesktopState } from "../src/app/preview-data";

afterEach(cleanup);

test("rejects a duplicate space name in the active connection", async () => {
  const user = userEvent.setup();
  const onCreated = vi.fn();
  render(
    <CreateSpaceDialog
      language="zh"
      state={previewDesktopState(null, "zh")}
      onClose={() => {}}
      onCreated={onCreated}
      returnFocus={() => null}
    />,
  );

  await user.type(screen.getByLabelText("空间名称"), "  默认空间  ");
  await user.click(screen.getByRole("button", { name: "创建空间" }));

  expect(screen.getByText("当前连接下已存在同名空间")).toBeTruthy();
  expect(screen.getByLabelText("空间名称").getAttribute("aria-invalid")).toBe(
    "true",
  );
  expect(onCreated).not.toHaveBeenCalled();
});
