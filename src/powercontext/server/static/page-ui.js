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
  let currentLocale = document.documentElement.lang === "zh" ? "zh" : "en";

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
    languageToggle.textContent = currentLocale === "en" ? translate("languageChinese") : "EN";
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
    currentLocale = nextLocale;
    document.documentElement.lang = nextLocale;
    if (persist) {
      try {
        localStorage.setItem(localeKey, nextLocale);
      } catch (error) {
        // The selected locale still applies to the current page.
      }
    }
    document.title = translate("pageTitle");
    document.querySelectorAll("[data-i18n]").forEach((element) => {
      element.textContent = translate(element.dataset.i18n);
    });
    updateControls();
    onLocaleChange();
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

  return {formatDateTime, formatNumber, initialize, locale, localeTag, translate};
}
