(function (root) {
  "use strict";
  var topics = ["microstructure", "execution", "market-making", "rates", "yield-curves", "volatility", "foundations"];
  function safeUrl(value) {
    try {
      var url = new URL(value);
      return url.protocol === "https:" && !url.username && !url.password;
    } catch (_) { return false; }
  }
  function bilingual(value) {
    return value && ["ja", "en"].every(function (lang) { return typeof value[lang] === "string" && value[lang].trim(); });
  }
  function validate(data) {
    if (!data || data.schemaVersion !== 1 || !/^\d{4}-\d{2}-\d{2}$/.test(data.verifiedOn) || !Array.isArray(data.items) || !data.items.length) throw new Error("Invalid reading list");
    var ids = new Set();
    data.items.forEach(function (item) {
      if (!item || typeof item.id !== "string" || !/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(item.id) || ids.has(item.id) ||
          !["paper", "book"].includes(item.kind) || !Number.isInteger(item.year) || item.year < 1900 || item.year > 2100 ||
          typeof item.title !== "string" || !item.title.trim() || !Array.isArray(item.authors) || !item.authors.length ||
          !item.authors.every(function (author) { return typeof author === "string" && author.trim(); }) ||
          !Array.isArray(item.topics) || !item.topics.length || !item.topics.every(function (topic) { return topics.includes(topic); }) ||
          !["why", "use", "prerequisite", "accessNote"].every(function (key) { return bilingual(item[key]); }) ||
          !safeUrl(item.sourceUrl) || (item.freeUrl !== null && !safeUrl(item.freeUrl)) ||
          (item.edition !== undefined && !bilingual(item.edition))) throw new Error("Invalid reading-list record");
      ids.add(item.id);
    });
    return data;
  }
  function filter(items, filters, topicNames) {
    var query = String(filters.q || "").trim().toLocaleLowerCase();
    return items.filter(function (item) {
      if (filters.kind && item.kind !== filters.kind) return false;
      if (filters.topic && !item.topics.includes(filters.topic)) return false;
      if (filters.access === "free" && !item.freeUrl) return false;
      if (filters.access === "publisher" && item.freeUrl) return false;
      var text = [item.title, item.authors.join(" "), item.year, item.why.ja, item.why.en, item.use.ja, item.use.en, item.topics.join(" "), item.topics.map(function (topic) { return topicNames[topic] || topic; }).join(" ")].join(" ").toLocaleLowerCase();
      return !query || text.includes(query);
    });
  }
  if (typeof module === "object" && module.exports) module.exports = { validate: validate, filter: filter, safeUrl: safeUrl };
  if (!root.document) return;
  var document = root.document, params = new URLSearchParams(root.location.search);
  var lang = params.get("lang") === "en" ? "en" : "ja";
  function t(key) { return root.RatesI18n.copy[lang]["classics." + key]; }
  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }
  var inputs = { q: document.getElementById("classics-search"), kind: document.getElementById("classics-kind"), topic: document.getElementById("classics-topic"), access: document.getElementById("classics-access") };
  var list = document.getElementById("classics-list"), status = document.getElementById("classics-status"), retry = document.getElementById("classics-retry");
  var catalogue = null, topicNames = {};
  topics.forEach(function (topic) {
    topicNames[topic] = t("topic." + topic);
    var option = el("option", "", topicNames[topic]); option.value = topic; inputs.topic.append(option);
  });
  function readUrl() {
    var search = new URLSearchParams(root.location.search);
    Object.keys(inputs).forEach(function (key) {
      var value = (search.get(key) || "").slice(0, key === "q" ? 180 : 40);
      if (key !== "q" && !Array.from(inputs[key].options).some(function (option) { return option.value === value; })) value = "";
      inputs[key].value = value;
    });
  }
  function writeUrl() {
    var url = new URL(root.location.href);
    Object.keys(inputs).forEach(function (key) {
      if (inputs[key].value) url.searchParams.set(key, inputs[key].value); else url.searchParams.delete(key);
    });
    root.history.replaceState(null, "", url);
  }
  function link(url, label) {
    var anchor = el("a", "", label); anchor.href = url; anchor.target = "_blank"; anchor.rel = "noopener noreferrer"; return anchor;
  }
  function card(item) {
    var article = el("article", "classic-card"); article.id = item.id; article.dataset.kind = item.kind;
    var meta = el("div", "classic-meta"); meta.append(el("span", "", t(item.kind)), el("span", "", item.year), el("span", "", t(item.freeUrl ? "free" : "publisherOnly")));
    if (item.edition) meta.append(el("span", "", item.edition[lang]));
    var heading = el("h2", "", item.title); heading.lang = "en"; heading.id = item.id + "-title"; article.setAttribute("aria-labelledby", heading.id);
    var tags = el("div", "classic-tags"); item.topics.forEach(function (topic) { tags.append(el("span", "", topicNames[topic])); });
    var notes = el("details", "classic-notes"); notes.append(el("summary", "", t("readingNotes")));
    var definitions = el("dl"); ["use", "prerequisite"].forEach(function (key) { definitions.append(el("dt", "", t(key)), el("dd", "", item[key][lang])); }); notes.append(definitions);
    var links = el("div", "classic-links");
    if (item.freeUrl) links.append(link(item.freeUrl, t("freeLink")));
    links.append(link(item.sourceUrl, t("sourceLink")));
    article.append(meta, heading, el("p", "classic-authors", item.authors.join(" · ")), tags, el("p", "classic-why", item.why[lang]), notes, links, el("p", "classic-access", item.accessNote[lang]));
    return article;
  }
  function render() {
    if (!catalogue) return;
    var filters = {}; Object.keys(inputs).forEach(function (key) { filters[key] = inputs[key].value; });
    var matches = filter(catalogue.items, filters, topicNames);
    list.replaceChildren.apply(list, matches.map(card));
    status.textContent = matches.length ? t("count").replace("{shown}", matches.length).replace("{total}", catalogue.items.length) : t("empty");
    document.getElementById("classics-clear").disabled = !Object.values(filters).some(Boolean);
  }
  async function load() {
    retry.hidden = true; status.textContent = t("loading"); list.setAttribute("aria-busy", "true");
    try {
      var response = await root.fetch("../data/classics.json", { credentials: "omit" });
      if (!response.ok) throw new Error("Reading list unavailable");
      catalogue = validate(await response.json());
      document.getElementById("classics-verified").textContent = t("verified").replace("{date}", catalogue.verifiedOn);
      render();
    } catch (_) { status.textContent = t("error"); retry.hidden = false; }
    finally { list.setAttribute("aria-busy", "false"); }
  }
  Object.keys(inputs).forEach(function (key) { inputs[key].addEventListener(key === "q" ? "input" : "change", function () { writeUrl(); render(); }); });
  document.getElementById("classics-clear").addEventListener("click", function () { Object.values(inputs).forEach(function (input) { input.value = ""; }); writeUrl(); render(); });
  root.addEventListener("popstate", function () { readUrl(); render(); });
  retry.addEventListener("click", load);
  readUrl(); load();
})(typeof window !== "undefined" ? window : globalThis);
