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
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test } from "vitest";
import { Input } from "../src/components/ui/input";
import { Textarea } from "../src/components/ui/textarea";
import { FieldError } from "../src/components/ui/field";

afterEach(cleanup);

test.each([Input, Textarea])(
  "read-only text remains focusable and selectable without permitting edits",
  async (Field) => {
    const user = userEvent.setup();
    render(
      <>
        <Field aria-label="Read-only value" value="Saved address" readOnly />
        <Field aria-label="Disabled value" value="Unavailable" disabled />
        <button>Continue</button>
      </>,
    );
    const field = screen.getByLabelText("Read-only value") as
      | HTMLInputElement
      | HTMLTextAreaElement;
    await user.tab();
    expect(document.activeElement).toBe(field);
    field.select();
    expect(field.selectionEnd! - field.selectionStart!).toBe(
      field.value.length,
    );
    await user.type(field, "changed");
    expect(field.value).toBe("Saved address");
    await user.tab();
    expect(document.activeElement).toBe(
      screen.getByRole("button", { name: "Continue" }),
    );
  },
);

test("a field error remains associated with its input while preserving entered text", () => {
  render(
    <>
      <Input
        aria-label="Address"
        defaultValue="invalid"
        aria-invalid
        aria-describedby="address-error"
      />
      <FieldError id="address-error">Enter a valid address.</FieldError>
    </>,
  );
  const field = screen.getByLabelText("Address") as HTMLInputElement;
  expect(field.value).toBe("invalid");
  expect(field.getAttribute("aria-invalid")).toBe("true");
  expect(
    document.getElementById(field.getAttribute("aria-describedby")!)
      ?.textContent,
  ).toBe("Enter a valid address.");
});
