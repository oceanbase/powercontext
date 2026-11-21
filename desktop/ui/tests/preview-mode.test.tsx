import { describe, expect, it } from "vitest";
import { resolvePreviewMode } from "../src/app/preview-mode";

describe("development preview boundary", () => {
  it("cannot enable mock connections in a production build, including preview mode", () => {
    for (const mode of ["production", "preview"])
      for (const search of [
        "",
        "?preview=connected",
        "?preview=disconnected",
        "?preview=remote-only",
        "?preview=local-only",
      ])
        expect(resolvePreviewMode(false, mode, search)).toBeNull();
  });

  it("uses real connections in ordinary development unless explicitly requested", () => {
    expect(resolvePreviewMode(true, "development", "")).toBeNull();
    expect(resolvePreviewMode(true, "development", "?preview=connected")).toBe(
      "connected",
    );
    expect(
      resolvePreviewMode(true, "development", "?preview=disconnected"),
    ).toBe("disconnected");
    expect(
      resolvePreviewMode(true, "development", "?preview=remote-only"),
    ).toBe("remote-only");
    expect(resolvePreviewMode(true, "development", "?preview=local-only")).toBe(
      "local-only",
    );
    expect(
      resolvePreviewMode(true, "development", "?preview=unknown"),
    ).toBeNull();
  });

  it("opens the dedicated preview in connected state and allows returning to real data", () => {
    expect(resolvePreviewMode(true, "preview", "")).toBe("connected");
    expect(resolvePreviewMode(true, "preview", "?preview=real")).toBeNull();
    expect(resolvePreviewMode(true, "test", "?preview=connected")).toBeNull();
  });
});
