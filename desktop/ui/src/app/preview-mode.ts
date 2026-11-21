/* Copyright (c) 2026 OceanBase. Licensed under the Apache License, Version 2.0. */

export function resolvePreviewMode(
  development: boolean,
  mode: string,
  search: string,
): "connected" | "disconnected" | "remote-only" | "local-only" | null {
  if (!development || mode === "test") return null;
  const requested = new URLSearchParams(search).get("preview");
  if (
    requested === "connected" ||
    requested === "disconnected" ||
    requested === "remote-only" ||
    requested === "local-only"
  )
    return requested;
  if (requested === "real") return null;
  return mode === "preview" ? "connected" : null;
}
