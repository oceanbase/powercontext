"use strict";

const themeKey = "powercontext.dashboard.theme";
const localeKey = "powercontext.dashboard.locale";

export function createRequestGate() {
  let requestSequence = 0;
  return {
    cancel() {
      requestSequence += 1;
    },
    start() {
      const sequence = ++requestSequence;
      return {
        isCurrent() {
          return sequence === requestSequence;
        }
      };
    }
  };
}

export function createPageUi(translations, onLocaleChange = () => {}) {
  const themeToggle = document.getElementById("theme-toggle");
  const languageToggle = document.getElementById("language-toggle");
  let localePreference = null;
  try {
    const savedLocale = localStorage.getItem(localeKey);
    if (savedLocale === "zh" || savedLocale === "en") {
      localePreference = savedLocale;
    }
  } catch (error) {
    // Browser language and the current page remain available without storage.
  }
  let currentLocale = localePreference || (document.documentElement.lang === "zh" ? "zh" : "en");

  function translate(key, values = {}) {
    const template = translations[currentLocale][key] || translations.en[key] || key;
    return template.replace(/\{([a-zA-Z]+)\}/g, (match, name) => String(values[name] ?? match));
  }

  function locale() {
    return currentLocale;
  }

  function localeTag() {
    return currentLocale === "zh" ? "zh-CN" : "en";
  }

  function hasLocalePreference() {
    return localePreference !== null;
  }

  function formatNumber(value) {
    return new Intl.NumberFormat(localeTag()).format(value);
  }

  function formatDateTime(value) {
    return new Intl.DateTimeFormat(localeTag(), {dateStyle: "medium", timeStyle: "short"}).format(new Date(value));
  }

  function updateControls() {
    const nextTheme = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    themeToggle.setAttribute("aria-label", translate(nextTheme === "dark" ? "switchDark" : "switchLight"));
    themeToggle.setAttribute("title", translate(nextTheme === "dark" ? "switchDark" : "switchLight"));
    languageToggle.textContent = currentLocale === "en" ? translate("languageChinese") : translate("languageEnglish");
    languageToggle.setAttribute("aria-label", translate(currentLocale === "en" ? "switchChinese" : "switchEnglish"));
  }

  function applyTheme(theme, persist = true) {
    document.documentElement.dataset.theme = theme;
    if (persist) {
      try {
        localStorage.setItem(themeKey, theme);
      } catch (error) {
        // The selected theme still applies to the current page.
      }
    }
    updateControls();
  }

  function applyLocale(nextLocale, persist = true) {
    currentLocale = nextLocale === "zh" ? "zh" : "en";
    document.documentElement.lang = currentLocale;
    if (persist) {
      localePreference = currentLocale;
      try {
        localStorage.setItem(localeKey, currentLocale);
      } catch (error) {
        // The selected locale still applies to the current page.
      }
    }
    document.title = translate("pageTitle");
    document.querySelectorAll("[data-i18n]").forEach((element) => {
      element.textContent = translate(element.dataset.i18n);
    });
    document.querySelectorAll("[data-i18n-aria-label]").forEach((element) => {
      element.setAttribute("aria-label", translate(element.dataset.i18nAriaLabel));
    });
    document.querySelectorAll("[data-i18n-title]").forEach((element) => {
      element.setAttribute("title", translate(element.dataset.i18nTitle));
    });
    document.querySelectorAll("[data-i18n-placeholder]").forEach((element) => {
      element.setAttribute("placeholder", translate(element.dataset.i18nPlaceholder));
    });
    updateControls();
    onLocaleChange({userInitiated: persist});
  }

  function initialize() {
    themeToggle.addEventListener("click", () => {
      applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");
    });
    languageToggle.addEventListener("click", () => {
      applyLocale(currentLocale === "en" ? "zh" : "en");
    });
    const initialTheme = document.documentElement.dataset.theme === "dark"
      ? "dark"
      : (document.documentElement.dataset.theme === "light"
        ? "light"
        : (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light"));
    applyLocale(currentLocale, false);
    applyTheme(initialTheme, false);
  }

  return {
    applyLocale,
    formatDateTime,
    formatNumber,
    hasLocalePreference,
    initialize,
    locale,
    localeTag,
    translate
  };
}
