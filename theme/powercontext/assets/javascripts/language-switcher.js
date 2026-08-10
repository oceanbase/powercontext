(function () {
  const languages = new Set(["en", "zh"]);

  function localizedPath(targetLanguage) {
    const segments = window.location.pathname.split("/");
    const languageIndex = segments.findIndex((segment) => languages.has(segment));

    if (languageIndex >= 0) {
      segments[languageIndex] = targetLanguage;
      return segments.join("/") + window.location.search + window.location.hash;
    }

    const base = window.location.pathname.endsWith("/")
      ? window.location.pathname
      : `${window.location.pathname}/`;
    return `${base}${targetLanguage}/`;
  }

  function updateLanguageLinks() {
    document.querySelectorAll(".pc-language-switcher[hreflang]").forEach((link) => {
      const targetLanguage = link.getAttribute("hreflang");
      if (!languages.has(targetLanguage)) {
        return;
      }
      link.setAttribute("href", localizedPath(targetLanguage));
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", updateLanguageLinks);
  } else {
    updateLanguageLinks();
  }

  if (typeof document$ !== "undefined") {
    document$.subscribe(updateLanguageLinks);
  }
})();
