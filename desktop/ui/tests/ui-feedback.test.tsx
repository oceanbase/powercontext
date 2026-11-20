import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
import { CircleCheck } from "lucide-react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ActionNotification } from "../src/components/ui/action-notification";
import { PageFeedback } from "../src/components/ui/feedback";
import { Skeleton } from "../src/components/ui/skeleton";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("shared feedback components", () => {
  it("uses the standalone shadcn Skeleton primitive for structural loading", () => {
    const { container } = render(<Skeleton className="h-4" />);
    expect(container.querySelector('[data-slot="skeleton"]')).toBeTruthy();
  });

  it("announces loading without treating it as an error", () => {
    render(<PageFeedback kind="loading" title="Loading memories" />);

    const status = screen.getByRole("status");
    expect(status.getAttribute("aria-busy")).toBe("true");
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("announces errors immediately and keeps recovery actions available", () => {
    render(
      <PageFeedback
        compact
        kind="error"
        title="Could not load memories"
        action={<button type="button">Try again</button>}
      />,
    );

    expect(screen.getByRole("alert")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Try again" })).toBeTruthy();
  });

  it("keeps non-blocking notifications polite and dismissible", () => {
    const onClose = vi.fn();
    render(
      <ActionNotification
        icon={<CircleCheck />}
        title="Saved"
        closeLabel="Close"
        onClose={onClose}
        tone="success"
      />,
    );

    expect(screen.getByRole("status").getAttribute("aria-live")).toBe("polite");
    screen.getByRole("button", { name: "Close" }).click();
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("pauses toast dismissal while keyboard focus is inside the notification", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date"] });
    const onClose = vi.fn();
    render(
      <>
        <button>Outside</button>
        <ActionNotification
          icon={<CircleCheck />}
          title="Saved"
          closeLabel="Close"
          onClose={onClose}
          tone="success"
        />
      </>,
    );
    await act(async () => vi.advanceTimersByTimeAsync(1000));
    const close = screen.getByRole("button", { name: "Close" });
    fireEvent.focusIn(close);
    await act(async () => vi.advanceTimersByTimeAsync(10000));
    expect(onClose).not.toHaveBeenCalled();
    fireEvent.focusOut(close, {
      relatedTarget: screen.getByRole("button", { name: "Outside" }),
    });
    await act(async () => vi.advanceTimersByTimeAsync(5001));
    expect(onClose).toHaveBeenCalledOnce();
  });

  it.each(["success", "warning"] as const)(
    "announces %s without an assertive alert",
    (kind) => {
      render(
        <PageFeedback kind={kind} title="Configuration status">
          Details
        </PageFeedback>,
      );
      const status = screen.getByRole("status");
      expect(
        within(status).getByRole("heading", {
          level: 2,
          name: "Configuration status",
        }),
      ).toBeTruthy();
      expect(status.getAttribute("aria-live")).toBe("polite");
      expect(screen.queryByRole("alert")).toBeNull();
    },
  );

  it("preserves an error heading and recovery action when composed with Alert", () => {
    render(
      <PageFeedback
        kind="error"
        headingLevel={3}
        title="Connection failed"
        action={<button>Retry</button>}
      >
        Check the address.
      </PageFeedback>,
    );
    const alert = screen.getByRole("alert");
    expect(
      within(alert).getByRole("heading", {
        level: 3,
        name: "Connection failed",
      }),
    ).toBeTruthy();
    expect(within(alert).getByText("Check the address.")).toBeTruthy();
    expect(within(alert).getByRole("button", { name: "Retry" })).toBeTruthy();
  });
});
