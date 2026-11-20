/*
 * Copyright (c) 2026 OceanBase.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 * http://www.apache.org/licenses/LICENSE-2.0
 */

import { useState } from "react";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";
import {
  Dialog,
  DialogBody,
  DialogCloseButton,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "../src/components/ui/dialog";

afterEach(cleanup);

function ModalHarness({ onClose }: { onClose: () => void }) {
  const [open, setOpen] = useState(true);
  const close = () => {
    onClose();
    setOpen(false);
  };
  return open ? (
    <Dialog open onOpenChange={(nextOpen) => !nextOpen && close()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Example dialog</DialogTitle>
          <DialogCloseButton label="Close dialog" />
        </DialogHeader>
        <DialogBody>Dialog content</DialogBody>
        <DialogFooter>
          <button type="button" onClick={close}>
            Cancel
          </button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  ) : null;
}

test("backdrop does not dismiss a dialog", async () => {
  const onClose = vi.fn();
  const user = userEvent.setup();
  render(<ModalHarness onClose={onClose} />);

  await user.click(document.querySelector('[data-slot="dialog-overlay"]')!);

  expect(screen.getByRole("dialog", { name: "Example dialog" })).toBeTruthy();
  expect(onClose).not.toHaveBeenCalled();
});

test("Escape dismisses the topmost dialog", async () => {
  const onClose = vi.fn();
  const user = userEvent.setup();
  render(<ModalHarness onClose={onClose} />);

  await user.keyboard("{Escape}");

  expect(screen.queryByRole("dialog")).toBeNull();
  expect(onClose).toHaveBeenCalledOnce();
});

test.each(["Close dialog", "Cancel"])(
  "%s explicitly dismisses a modal",
  async (buttonName) => {
    const onClose = vi.fn();
    const user = userEvent.setup();
    render(<ModalHarness onClose={onClose} />);
    const dialog = within(
      screen.getByRole("dialog", { name: "Example dialog" }),
    );

    await user.click(dialog.getByRole("button", { name: buttonName }));

    expect(screen.queryByRole("dialog")).toBeNull();
    expect(onClose).toHaveBeenCalledOnce();
  },
);
