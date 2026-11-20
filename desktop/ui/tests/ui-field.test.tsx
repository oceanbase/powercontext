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
