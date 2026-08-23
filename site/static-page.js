(function () {
  "use strict";

  var i18n = window.RatesI18n;
  var params = new URLSearchParams(window.location.search);
  var language = params.get("lang") === "en" ? "en" : "ja";

  function t(key) {
    var catalog = i18n && i18n.copy && i18n.copy[language];
    var fallback = i18n && i18n.copy && i18n.copy.ja;
    if (catalog && catalog[key] !== undefined) return String(catalog[key]);
    if (fallback && fallback[key] !== undefined) return String(fallback[key]);
    return key;
  }

  function languageUrl(nextLanguage) {
    var url = new URL(window.location.href);
    if (nextLanguage === "en") url.searchParams.set("lang", "en");
    else url.searchParams.delete("lang");
    return url;
  }

  function preserveLanguage(link) {
    var href = link.getAttribute("href");
    if (!href) return;
    var url = new URL(href, document.baseURI);
    if (url.origin !== window.location.origin) return;
    if (language === "en") url.searchParams.set("lang", "en");
    else url.searchParams.delete("lang");
    link.href = url.href;
  }

  function setMeta(selector, value) {
    var node = document.querySelector(selector);
    if (node) node.setAttribute("content", value);
  }

  function applyRichText(node, value) {
    var formulas = new Map();
    node.querySelectorAll("[data-inline-token]").forEach(function (formula) {
      formulas.set(formula.dataset.inlineToken, formula.cloneNode(true));
    });

    var fragment = document.createDocumentFragment();
    var tokenPattern = /\{\{([a-z0-9-]+)\}\}/g;
    var cursor = 0;
    var match;

    while ((match = tokenPattern.exec(value)) !== null) {
      fragment.append(document.createTextNode(value.slice(cursor, match.index)));
      var formula = formulas.get(match[1]);
      fragment.append(
        formula ? formula.cloneNode(true) : document.createTextNode(match[0])
      );
      cursor = match.index + match[0].length;
    }
    fragment.append(document.createTextNode(value.slice(cursor)));
    node.replaceChildren(fragment);
  }

  function applyLocale() {
    document.documentElement.lang = language;
    document.querySelectorAll("[data-i18n]").forEach(function (node) {
      node.textContent = t(node.dataset.i18n);
    });
    document.querySelectorAll("[data-i18n-rich]").forEach(function (node) {
      applyRichText(node, t(node.dataset.i18nRich));
    });
    document.querySelectorAll("[data-i18n-aria-label]").forEach(function (node) {
      node.setAttribute("aria-label", t(node.dataset.i18nAriaLabel));
    });
    document.querySelectorAll("[data-language]").forEach(function (button) {
      button.lang = button.dataset.language;
      button.setAttribute("aria-pressed", String(button.dataset.language === language));
    });
    document.querySelectorAll("[data-preserve-language]").forEach(preserveLanguage);

    var title = t(document.body.dataset.pageTitleKey);
    var description = t(document.body.dataset.descriptionKey);
    document.title = title;
    setMeta('meta[name="description"]', description);
    setMeta('meta[property="og:title"]', title);
    setMeta('meta[property="og:description"]', description);
    setMeta('meta[name="twitter:title"]', title);
    setMeta('meta[name="twitter:description"]', description);
    setMeta('meta[property="og:locale"]', language === "en" ? "en_US" : "ja_JP");

    var canonical = new URL(window.location.pathname, window.location.origin);
    if (language === "en") canonical.searchParams.set("lang", "en");
    setMeta('meta[property="og:url"]', canonical.href);
  }

  document.querySelectorAll("[data-language]").forEach(function (button) {
    button.addEventListener("click", function () {
      var nextLanguage = button.dataset.language === "en" ? "en" : "ja";
      if (nextLanguage !== language) window.location.assign(languageUrl(nextLanguage).href);
    });
  });

  applyLocale();
})();
