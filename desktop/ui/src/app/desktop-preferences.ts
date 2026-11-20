/* Copyright (c) 2026 OceanBase. Licensed under the Apache License, Version 2.0. */

import type { Language } from "./messages";

export type DesktopTheme = "light" | "dark" | "system";

export type DesktopPreferences = {
  language: Language;
  theme: DesktopTheme;
  notificationsEnabled: boolean;
  openDesktopAtLogin: boolean;
};

export const defaultDesktopPreferences: DesktopPreferences = {
  language: "zh",
  theme: "system",
  notificationsEnabled: true,
  openDesktopAtLogin: true,
};

type PreferenceStorage = Pick<Storage, "getItem" | "setItem">;

const storageKeys: Record<keyof DesktopPreferences, string> = {
  language: "desktop-language",
  theme: "desktop-theme",
  notificationsEnabled: "desktop-notifications-enabled",
  openDesktopAtLogin: "desktop-launch-at-login",
};

function resolveStorage(storage?: PreferenceStorage) {
  if (storage) return storage;
  try {
    return globalThis.localStorage;
  } catch {
    return undefined;
  }
}

function readValue(storage: PreferenceStorage | undefined, key: string) {
  try {
    return storage?.getItem(key) ?? null;
  } catch {
    return null;
  }
}

export function loadDesktopPreferences(
  providedStorage?: PreferenceStorage,
): DesktopPreferences {
  const storage = resolveStorage(providedStorage);
  const language = readValue(storage, storageKeys.language);
  const theme = readValue(storage, storageKeys.theme);
  const notificationsEnabled = readValue(
    storage,
    storageKeys.notificationsEnabled,
  );
  const openDesktopAtLogin = readValue(storage, storageKeys.openDesktopAtLogin);

  return {
    language: language === "en" || language === "zh" ? language : "zh",
    theme:
      theme === "light" || theme === "dark" || theme === "system"
        ? theme
        : "system",
    notificationsEnabled:
      notificationsEnabled === "true"
        ? true
        : notificationsEnabled === "false"
          ? false
          : true,
    openDesktopAtLogin:
      openDesktopAtLogin === "true"
        ? true
        : openDesktopAtLogin === "false"
          ? false
          : true,
  };
}

export function saveDesktopPreferences(
  preferences: DesktopPreferences,
  providedStorage?: PreferenceStorage,
) {
  const storage = resolveStorage(providedStorage);
  if (!storage) return;
  for (const key of Object.keys(storageKeys) as (keyof DesktopPreferences)[]) {
    try {
      storage.setItem(storageKeys[key], String(preferences[key]));
    } catch {
      // The current UI state remains valid when persistence is unavailable.
    }
  }
}
