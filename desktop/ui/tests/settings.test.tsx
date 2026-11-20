/* Copyright (c) 2026 OceanBase. Licensed under the Apache License, Version 2.0. */

import { useState } from "react";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test } from "vitest";
import {
  defaultDesktopPreferences,
  loadDesktopPreferences,
  saveDesktopPreferences,
  type DesktopPreferences,
} from "../src/app/desktop-preferences";
import { SettingsPage } from "../src/app/SettingsPage";

afterEach(() => {
  cleanup();
  globalThis.localStorage.clear();
});

function SettingsHarness({ initial = defaultDesktopPreferences }) {
  const [preferences, setPreferences] = useState(initial);
  return (
    <SettingsPage
      preferences={preferences}
      onPreferenceChange={(key, value) =>
        setPreferences((current) => ({ ...current, [key]: value }))
      }
    />
  );
}

test("loads valid preferences and falls back safely for malformed values", () => {
  globalThis.localStorage.setItem("desktop-language", "en");
  globalThis.localStorage.setItem("desktop-theme", "dark");
  globalThis.localStorage.setItem("desktop-notifications-enabled", "false");
  globalThis.localStorage.setItem("desktop-launch-at-login", "invalid");

  expect(loadDesktopPreferences()).toEqual({
    language: "en",
    theme: "dark",
    notificationsEnabled: false,
    openDesktopAtLogin: true,
  });
});

test("persists the complete preference model and tolerates unavailable storage", () => {
  const preferences: DesktopPreferences = {
    language: "en",
    theme: "light",
    notificationsEnabled: false,
    openDesktopAtLogin: false,
  };
  saveDesktopPreferences(preferences);
  expect(loadDesktopPreferences()).toEqual(preferences);

  const unavailableStorage = {
    getItem: () => {
      throw new Error("unavailable");
    },
    setItem: () => {
      throw new Error("unavailable");
    },
  };
  expect(loadDesktopPreferences(unavailableStorage)).toEqual(
    defaultDesktopPreferences,
  );
  expect(() =>
    saveDesktopPreferences(preferences, unavailableStorage),
  ).not.toThrow();
});

test("all settings update through one controlled preference model", async () => {
  const user = userEvent.setup();
  render(<SettingsHarness />);

  await user.selectOptions(
    screen.getByRole("combobox", { name: "语言" }),
    "en",
  );
  expect(screen.getByRole("heading", { name: "Settings" })).toBeTruthy();

  await user.selectOptions(
    screen.getByRole("combobox", { name: "Theme" }),
    "dark",
  );
  expect(
    (screen.getByRole("combobox", { name: "Theme" }) as HTMLSelectElement)
      .value,
  ).toBe("dark");

  const notifications = screen.getByRole("switch", {
    name: "System notifications",
  });
  const launch = screen.getByRole("switch", {
    name: "Open Desktop at login",
  });
  await user.click(notifications);
  await user.click(launch);
  expect(notifications.getAttribute("data-state")).toBe("unchecked");
  expect(launch.getAttribute("data-state")).toBe("unchecked");
  expect(notifications.className).toContain("h-8");
  expect(notifications.className).toContain("w-10");
});
