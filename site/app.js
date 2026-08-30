(function () {
  "use strict";

  var i18n = window.RatesI18n;
  var archiveUi = window.RatesArchiveUi;
  if (!archiveUi) throw new Error("Archive UI helpers are unavailable.");
  var initialParams = new URLSearchParams(window.location.search);
  var initialArchiveRange = archiveUi.normaliseRange(
    initialParams.get("archive-from"),
    initialParams.get("archive-to")
  );

  function normaliseLanguage(value) {
    return value === "en" ? "en" : "ja";
  }

  function normaliseEditionKind(value) {
    return value === "weekly" || value === "monthly" ? value : "daily";
  }

  function kindValue(kind, dailyValue, weeklyValue, monthlyValue) {
    if (kind === "weekly") return weeklyValue;
    if (kind === "monthly") return monthlyValue;
    return dailyValue;
  }

  var byId = function (id) { return document.getElementById(id); };
  var elements = {
    archiveClear: byId("archive-clear"),
    archiveFrom: byId("archive-from"),
    archiveKindButtons: Array.from(document.querySelectorAll("[data-archive-kind]")),
    archiveList: byId("archive-list"),
    archiveMore: byId("archive-more"),
    archiveResults: byId("archive-results"),
    archiveTo: byId("archive-to"),
    digestTitle: byId("digest-title"),
    editionContext: byId("edition-context"),
    editionDate: byId("edition-date"),
    editionId: byId("edition-id"),
    editionImported: byId("edition-imported"),
    editionKind: byId("edition-kind"),
    editionPeriod: byId("edition-period"),
    editionSource: byId("edition-source"),
    filters: byId("topic-filters"),
    freshnessShort: byId("freshness-short"),
    headerStatus: byId("header-status"),
    lensVisual: byId("lens-visual"),
    loading: byId("loading-state"),
    noResults: byId("no-results"),
    notice: byId("notice-state"),
    noticeMessage: byId("notice-message"),
    noticeTitle: byId("notice-title"),
    paperCount: byId("paper-count"),
    paperIndexHeading: byId("paper-index-heading"),
    paperList: byId("paper-list"),
    search: byId("paper-search"),
    sourceDocument: byId("source-document"),
    sourceSection: byId("source-section"),
    sourceTitle: byId("source-title"),
    toolbar: byId("digest-toolbar"),
    topicCount: byId("topic-count"),
    updateNote: byId("update-note")
  };

  var state = {
    archiveEdition: initialParams.get("edition"),
    archiveFrom: initialArchiveRange.from,
    archiveKind: archiveUi.normaliseKind(initialParams.get("archive-kind")),
    archiveTo: initialArchiveRange.to,
    archiveVisible: 6,
    filter: "all",
    language: normaliseLanguage(initialParams.get("lang")),
    query: "",
    rawReport: null,
    report: null,
    reports: [],
    translations: new Map()
  };

  function t(key, values) {
    var catalog = i18n && i18n.copy && i18n.copy[state.language];
    var fallback = i18n && i18n.copy && i18n.copy.ja;
    var value = catalog && catalog[key] !== undefined ? catalog[key] : fallback && fallback[key];
    value = value === undefined ? key : String(value);
    return value.replace(/\{([A-Za-z0-9_]+)\}/g, function (_match, name) {
      return values && values[name] !== undefined ? String(values[name]) : "{" + name + "}";
    });
  }

  function statusCopyFor(status) {
    var states = {
      UPDATE_CONFIRMED: "fresh",
      NO_RELEVANT_PAPERS: "fresh",
      NO_NEW_BATCH_EXPECTED: "fresh",
      UPDATE_NOT_CONFIRMED: "warn",
      UPDATER_OFFLINE: "offline",
      WEEKLY_REVIEW: "fresh",
      MONTHLY_REVIEW: "fresh"
    };
    var key = states[status] ? status : "";
    return {
      label: key ? t("status." + key + ".label") : t("freshness.statusUnknown"),
      state: states[key] || "warn",
      emptyTitle: key ? t("status." + key + ".emptyTitle") : t("empty.noPapersTitle")
    };
  }

  function topicLabel(topic) {
    return state.language === "ja" && i18n && i18n.topicsJa[topic] ? i18n.topicsJa[topic] : topic;
  }

  function languageUrl(language) {
    var url = new URL(window.location.href);
    if (language === "en") url.searchParams.set("lang", "en");
    else url.searchParams.delete("lang");
    return url;
  }

  function applyStaticLocale() {
    document.documentElement.lang = state.language;
    document.querySelectorAll("[data-i18n]").forEach(function (node) {
      node.textContent = t(node.dataset.i18n);
    });
    document.querySelectorAll("[data-i18n-placeholder]").forEach(function (node) {
      node.setAttribute("placeholder", t(node.dataset.i18nPlaceholder));
    });
    document.querySelectorAll("[data-i18n-aria-label]").forEach(function (node) {
      node.setAttribute("aria-label", t(node.dataset.i18nAriaLabel));
    });
    document.querySelectorAll("[data-language]").forEach(function (button) {
      button.setAttribute("aria-pressed", String(button.dataset.language === state.language));
    });
    document.querySelectorAll("[data-preserve-language]").forEach(function (link) {
      var href = link.getAttribute("href");
      if (!href) return;
      var url = new URL(href, document.baseURI);
      if (url.origin !== window.location.origin) return;
      if (state.language === "en") url.searchParams.set("lang", "en");
      else url.searchParams.delete("lang");
      link.href = url.href;
    });
    var description = document.querySelector('meta[name="description"]');
    if (description) description.setAttribute("content", t("meta.description"));
    document.title = t("meta.baseTitle");
    var brand = document.querySelector(".brand");
    if (brand) {
      var homeUrl = new URL("./", document.baseURI);
      if (state.language === "en") homeUrl.searchParams.set("lang", "en");
      brand.href = homeUrl.href;
    }
  }

  function createNode(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function clean(value, fallback) {
    if (typeof value !== "string") return fallback || "";
    var result = value.replace(/\s+/g, " ").trim();
    return result || fallback || "";
  }

  function cleanMultiline(value) {
    if (typeof value !== "string") return "";
    return value.replace(/\r\n?/g, "\n").replace(/\u0000/g, "").trim();
  }

  function nullableNumber(value) {
    if (value === null || value === undefined || value === "") return null;
    var number = Number(value);
    return Number.isFinite(number) ? number : null;
  }

  function stringList(value) {
    if (Array.isArray(value)) {
      return value.map(function (item) { return clean(item); }).filter(Boolean);
    }
    if (typeof value === "string" && value.trim()) {
      return value.split(/\s*,\s*/).map(function (item) { return clean(item); }).filter(Boolean);
    }
    return [];
  }

  function isDate(value) {
    return typeof value === "string" && /^\d{4}-\d{2}-\d{2}$/.test(value);
  }

  function validEditionId(value) {
    var id = clean(value);
    return /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/.test(id) ? id : "";
  }

  function validArchiveFilename(value) {
    var path = clean(value);
    return /^[A-Za-z0-9][A-Za-z0-9._-]{0,159}\.json$/.test(path) ? path : "";
  }

  function validArxivId(value) {
    var id = clean(value);
    return /^(?:\d{4}\.\d{4,5}(?:v\d+)?|[a-z][a-z0-9.-]*\/\d{7}(?:v\d+)?)$/i.test(id) ? id : "";
  }

  function idFromArxivUrl(value) {
    try {
      var url = new URL(value);
      if (
        url.protocol !== "https:" ||
        url.hostname !== "arxiv.org" ||
        url.port ||
        url.username ||
        url.password ||
        url.search ||
        url.hash
      ) return "";
      var match = url.pathname.match(/^\/(?:abs|pdf)\/(.+?)(?:\.pdf)?$/i);
      return match ? validArxivId(decodeURIComponent(match[1])) : "";
    } catch (_error) {
      return "";
    }
  }

  function canonicalArxivUrl(id, kind) {
    var safeId = validArxivId(id);
    if (!safeId) return "";
    var encodedId = safeId.split("/").map(encodeURIComponent).join("/");
    return "https://arxiv.org/" + (kind === "pdf" ? "pdf" : "abs") + "/" + encodedId + (kind === "pdf" ? ".pdf" : "");
  }

  function safeArxivLink(value) {
    try {
      var url = new URL(value);
      var id = idFromArxivUrl(url.href);
      if (!id) return "";
      var kind = /^\/pdf\//i.test(url.pathname) ? "pdf" : "abs";
      return canonicalArxivUrl(id, kind);
    } catch (_error) {
      return "";
    }
  }

  function normaliseRating(raw) {
    raw = raw && typeof raw === "object" ? raw : {};
    var scale = raw.scale === 5 || raw.scale === 10 ? raw.scale : null;
    var value = nullableNumber(raw.value);
    if (!clean(raw.label) || scale === null || value === null || value < 0 || value > scale) return null;
    return { label: clean(raw.label), value: value, scale: scale };
  }

  function normalisePaper(raw, index) {
    raw = raw && typeof raw === "object" ? raw : {};
    var arxivId = validArxivId(raw.arxivId) || idFromArxivUrl(raw.absUrl) || idFromArxivUrl(raw.pdfUrl);
    var score = nullableNumber(raw.score);
    var schedulerRank = nullableNumber(raw.schedulerRank);
    var schedulerRating = nullableNumber(raw.schedulerRating);
    var schedulerRatingScale = raw.schedulerRatingScale === 5 || raw.schedulerRatingScale === 10
      ? raw.schedulerRatingScale
      : null;
    if (schedulerRatingScale === null || schedulerRating === null || schedulerRating < 0 || schedulerRating > schedulerRatingScale) {
      schedulerRating = null;
      schedulerRatingScale = null;
    }
    return {
      abstract: clean(raw.abstract),
      absUrl: canonicalArxivUrl(arxivId, "abs"),
      arxivId: arxivId,
      authors: stringList(raw.authors),
      index: index,
      pdfUrl: canonicalArxivUrl(arxivId, "pdf"),
      ratings: Array.isArray(raw.ratings) ? raw.ratings.map(normaliseRating).filter(Boolean) : [],
      schedulerLabel: clean(raw.schedulerLabel),
      schedulerRank: schedulerRank !== null && schedulerRank > 0 ? schedulerRank : null,
      schedulerRating: schedulerRating,
      schedulerRatingScale: schedulerRatingScale,
      schedulerSummary: cleanMultiline(raw.schedulerSummary),
      score: score,
      scoreReasons: stringList(raw.scoreReasons),
      submittedDate: clean(raw.submittedDate),
      title: clean(raw.title, t("paper.untitled")),
      topics: stringList(raw.topics),
      updatedDate: clean(raw.updatedDate)
    };
  }

  function normaliseReport(raw) {
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
      throw new Error("The edition data is not a JSON object.");
    }
    var schemaVersion = Number(raw.schemaVersion) || 1;
    var v2 = schemaVersion >= 2;
    var papers = Array.isArray(raw.papers) ? raw.papers.map(normalisePaper) : [];
    var expectedDate = isDate(raw.expectedBatchDate) ? raw.expectedBatchDate : "";
    var editionDate = isDate(raw.editionDate) ? raw.editionDate : expectedDate;
    var editionId = validEditionId(raw.editionId) || editionDate;
    var editionKind = normaliseEditionKind(raw.editionKind);
    return {
      checkedAt: clean(raw.checkedAt),
      editionDate: editionDate,
      editionId: editionId,
      editionKind: editionKind,
      expectedBatchDate: expectedDate,
      generatedAt: clean(raw.generatedAt),
      importedAt: clean(raw.importedAt),
      message: clean(v2 ? raw.message : raw.statusMessage),
      observedBatchDate: isDate(raw.observedBatchDate) ? raw.observedBatchDate : "",
      papers: papers,
      periodEnd: isDate(raw.periodEnd) ? raw.periodEnd : "",
      periodStart: isDate(raw.periodStart) ? raw.periodStart : "",
      schemaVersion: schemaVersion,
      sourceKind: v2 ? clean(raw.sourceKind, "chatgpt-scheduled-task") : "local-arxiv-updater",
      sourceLabel: v2 ? clean(raw.sourceLabel, "ChatGPT scheduled task") : "Local arXiv updater",
      sourceText: v2 ? cleanMultiline(raw.sourceText) : "",
      status: clean(raw.status, "UPDATE_NOT_CONFIRMED").toUpperCase(),
      statusMessage: clean(v2 ? raw.message : raw.statusMessage)
    };
  }

  function normaliseArchive(raw) {
    if (!raw || typeof raw !== "object") return [];
    var v2 = Number(raw.schemaVersion) >= 2;
    var source = v2 && Array.isArray(raw.editions) ? raw.editions : raw.reports;
    if (!Array.isArray(source)) return [];
    var reports = source.map(function (item) {
      item = item && typeof item === "object" ? item : {};
      var date = isDate(item.date) ? item.date : "";
      var editionId = validEditionId(item.editionId) || date;
      var expectedFile = editionId ? editionId + ".json" : "";
      var path = validArchiveFilename(item.path) || validArchiveFilename(expectedFile);
      return {
        date: date,
        editionId: editionId,
        kind: normaliseEditionKind(item.kind),
        paperCount: Math.max(0, Number(item.paperCount) || 0),
        path: path,
        sourceKind: v2 ? clean(item.sourceKind, "chatgpt-scheduled-task") : "local-arxiv-updater",
        status: clean(item.status, "UPDATE_NOT_CONFIRMED").toUpperCase(),
        title: clean(item.title, kindValue(
          normaliseEditionKind(item.kind),
          "Daily research screen",
          "Weekly research review",
          "Monthly research review"
        ))
      };
    }).filter(function (item) {
      return item.date && item.editionId && item.path;
    });
    if (v2) return reports;
    return reports.sort(function (a, b) {
      return b.date.localeCompare(a.date) || b.editionId.localeCompare(a.editionId);
    });
  }

  async function fetchJson(url) {
    var response = await fetch(url, {
      cache: "no-store",
      credentials: "same-origin",
      headers: { Accept: "application/json" }
    });
    if (!response.ok) throw new Error("Request failed with HTTP " + response.status + ".");
    return response.json();
  }

  function normaliseTranslations(raw) {
    if (!raw || raw.schemaVersion !== 1 || raw.language !== "en" || !Array.isArray(raw.editions)) {
      throw new Error("Invalid translation manifest.");
    }
    var editions = new Map();
    raw.editions.forEach(function (rawEdition) {
      if (!rawEdition || typeof rawEdition !== "object") throw new Error("Invalid translated edition.");
      var editionId = validEditionId(rawEdition.editionId);
      if (!editionId || editions.has(editionId)) throw new Error("Invalid translated edition identity.");
      var papers = new Map();
      if (!Array.isArray(rawEdition.papers)) throw new Error("Invalid translated paper list.");
      rawEdition.papers.forEach(function (rawPaper) {
        if (!rawPaper || typeof rawPaper !== "object") throw new Error("Invalid translated paper.");
        var arxivId = validArxivId(rawPaper.arxivId);
        if (!arxivId || papers.has(arxivId) || !Array.isArray(rawPaper.ratings)) {
          throw new Error("Invalid translated paper identity.");
        }
        papers.set(arxivId, {
          arxivId: arxivId,
          ratings: rawPaper.ratings.map(function (rating) {
            var label = clean(rating && rating.label);
            if (!label) throw new Error("Invalid translated rating label.");
            return { label: label };
          }),
          schedulerLabel: clean(rawPaper.schedulerLabel),
          schedulerSummary: cleanMultiline(rawPaper.schedulerSummary)
        });
      });
      editions.set(editionId, {
        editionId: editionId,
        message: clean(rawEdition.message),
        papers: papers,
        sourceText: cleanMultiline(rawEdition.sourceText)
      });
    });
    return editions;
  }

  function localiseReport(report) {
    if (state.language !== "en") return report;
    var edition = state.translations.get(report.editionId);
    if (!edition || edition.papers.size !== report.papers.length || !edition.message || !edition.sourceText) return null;
    var papers = [];
    for (var index = 0; index < report.papers.length; index += 1) {
      var paper = report.papers[index];
      var translated = edition.papers.get(paper.arxivId);
      if (!translated || translated.ratings.length !== paper.ratings.length) return null;
      papers.push(Object.assign({}, paper, {
        ratings: paper.ratings.map(function (rating, ratingIndex) {
          return Object.assign({}, rating, { label: translated.ratings[ratingIndex].label });
        }),
        schedulerLabel: translated.schedulerLabel,
        schedulerSummary: translated.schedulerSummary
      }));
    }
    return Object.assign({}, report, {
      message: edition.message,
      papers: papers,
      sourceText: edition.sourceText,
      statusMessage: edition.message
    });
  }

  function fallBackToJapanese() {
    state.language = "ja";
    var url = languageUrl("ja");
    window.history.replaceState(null, "", url.href);
    applyStaticLocale();
    renderArchive();
  }

  function archiveDataUrl(item) {
    if (!item || !validArchiveFilename(item.path)) return "";
    var url = new URL("./data/archive/" + item.path, document.baseURI);
    if (url.origin !== window.location.origin) return "";
    return url.href;
  }

  function dateObject(value) {
    if (!value) return null;
    var date = isDate(value) ? new Date(value + "T12:00:00Z") : new Date(value);
    return Number.isNaN(date.getTime()) ? null : date;
  }

  function formatDate(value, compact) {
    var date = dateObject(value);
    if (!date) return t("date.unavailable");
    var locale = state.language === "ja" ? "ja-JP-u-ca-gregory" : "en-GB";
    return new Intl.DateTimeFormat(locale, compact
      ? { day: state.language === "ja" ? "numeric" : "2-digit", month: "short", year: "numeric", timeZone: "UTC" }
      : { day: "numeric", month: "long", year: "numeric", timeZone: "UTC" }
    ).format(date);
  }

  function relativeTime(value, verb) {
    var date = dateObject(value);
    verb = verb || t("time.checked");
    if (!date) return t("time.unavailable", { verb: verb });
    var minutes = Math.max(0, Math.round((Date.now() - date.getTime()) / 60000));
    if (minutes < 2) return t("time.justNow", { verb: verb });
    if (minutes < 60) return t("time.minutes", { verb: verb, count: minutes });
    var hours = Math.round(minutes / 60);
    if (hours < 48) return t("time.hours", { verb: verb, count: hours });
    var days = Math.round(hours / 24);
    return t(days === 1 ? "time.day" : "time.days", { verb: verb, count: days });
  }

  function editionFreshness(report, archived) {
    if (archived) {
      return { label: t("freshness.archivedLabel"), short: t("freshness.archivedShort"), state: "archived" };
    }

    var copy = statusCopyFor(report.status);
    if (copy.state === "offline") return { label: copy.label, short: t("freshness.offlineShort"), state: "offline" };
    if (report.status === "UPDATE_NOT_CONFIRMED") return { label: copy.label, short: t("freshness.waitingShort"), state: "warn" };

    var checked = dateObject(report.importedAt || report.checkedAt || report.generatedAt);
    if (!checked) return { label: copy.label + " · " + t("freshness.timeUnknown"), short: t("freshness.timeUnknown"), state: "warn" };
    var ageHours = Math.max(0, (Date.now() - checked.getTime()) / 3600000);
    var staleAfter = kindValue(report.editionKind, 72, 240, 840);
    var delayedAfter = kindValue(report.editionKind, 36, 192, 744);
    if (ageHours > staleAfter) return { label: t("freshness.staleLabel"), short: t("freshness.staleShort"), state: "stale" };
    if (ageHours > delayedAfter) return { label: t("freshness.delayedLabel"), short: t("freshness.delayedShort"), state: "warn" };
    return { label: copy.label, short: t("freshness.freshShort"), state: "fresh" };
  }

  function topicCounts(papers) {
    var counts = new Map();
    papers.forEach(function (paper) {
      paper.topics.forEach(function (topic) {
        counts.set(topic, (counts.get(topic) || 0) + 1);
      });
    });
    return Array.from(counts.entries()).sort(function (a, b) {
      return b[1] - a[1] || a[0].localeCompare(b[0]);
    });
  }

  function showNotice(title, message, displayState) {
    elements.noticeTitle.textContent = title;
    elements.noticeMessage.textContent = message;
    elements.notice.dataset.state = displayState || "warn";
    elements.notice.hidden = false;
  }

  function clearNotice() {
    elements.notice.hidden = true;
  }
  function stripRawHtml(value) {
    return String(value || "")
      .replace(/<!--[\s\S]*?-->/g, "")
      .replace(/<\/?[A-Za-z][^>]*>/g, "");
  }

  function sourceLink(url, label) {
    var safe = safeArxivLink(url);
    if (!safe) return null;
    var link = createNode("a", "source-arxiv-link", label);
    link.href = safe;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.setAttribute("aria-label", label + " " + t("link.newTabSuffix"));
    return link;
  }

  function linkWeeklySourceText(value, papers) {
    var text = cleanMultiline(value);
    if (!text || !Array.isArray(papers) || !papers.length) return text;
    var paperIndex = 0;
    var inOtherPapers = false;

    return text.split("\n").map(function (line) {
      var trimmed = line.trim();
      if (/^##\s+Other reviewed papers\s*$/i.test(trimmed)) {
        inOtherPapers = true;
        return line;
      }
      if (/^##\s+Weekly conclusion\s*$/i.test(trimmed)) {
        inOtherPapers = false;
        return line;
      }
      if (paperIndex >= papers.length) return line;

      var numbered = line.match(/^(##\s+\d+[.)]\s+)(.+)$/);
      var other = inOtherPapers ? line.match(/^(\*\*)(.+?)(\s+—\s+.+\*\*)$/) : null;
      var match = numbered || other;
      if (!match) return line;

      var paper = papers[paperIndex];
      paperIndex += 1;
      if (!paper.absUrl || /\[[^\]]+\]\(https:\/\/arxiv\.org\/abs\//i.test(line)) return line;
      var label = match[2].replace(/[\[\]]/g, "").trim();
      return match[1] + "[" + label + "](" + paper.absUrl + ")" + (match[3] || "");
    }).join("\n");
  }

  function appendInline(parent, value) {
    var text = stripRawHtml(value);
    var pattern = /\*\*([^*]+)\*\*|\[([^\]]+)\]\(([^)\s]+)\)|(https:\/\/arxiv\.org\/(?:abs|pdf)\/[A-Za-z0-9.\/-]+(?:\.pdf)?)/gi;
    var cursor = 0;
    var match;

    while ((match = pattern.exec(text)) !== null) {
      if (match.index > cursor) parent.appendChild(document.createTextNode(text.slice(cursor, match.index)));
      if (match[1] !== undefined) {
        var strong = createNode("strong", "");
        appendInline(strong, match[1]);
        parent.appendChild(strong);
      } else if (match[2] !== undefined) {
        var markdownLink = sourceLink(match[3], match[2]);
        parent.appendChild(markdownLink || document.createTextNode(match[2]));
      } else {
        var bareLink = sourceLink(match[4], match[4]);
        parent.appendChild(bareLink || document.createTextNode(match[4]));
      }
      cursor = pattern.lastIndex;
    }
    if (cursor < text.length) parent.appendChild(document.createTextNode(text.slice(cursor)));
  }

  function appendSourcePre(parent, value, equation) {
    var pre = createNode("pre", equation ? "source-pre source-equation" : "source-pre");
    if (equation) pre.setAttribute("aria-label", t("equation"));
    pre.appendChild(createNode("code", "", value));
    parent.appendChild(pre);
  }

  function renderMarkdownLite(value) {
    var fragment = document.createDocumentFragment();
    var lines = cleanMultiline(value).split("\n");
    var index = 0;

    while (index < lines.length) {
      var raw = lines[index];
      var trimmed = raw.trim();
      if (!trimmed) {
        index += 1;
        continue;
      }

      var fence = trimmed.match(/^\x60{3}([A-Za-z0-9_-]*)\s*$/);
      if (fence) {
        var code = [];
        index += 1;
        while (index < lines.length && !/^\x60{3}\s*$/.test(lines[index].trim())) {
          code.push(lines[index]);
          index += 1;
        }
        if (index < lines.length) index += 1;
        appendSourcePre(fragment, code.join("\n"), /^(?:math|latex|equation)$/i.test(fence[1]));
        continue;
      }

      if (trimmed === "$$" || trimmed === "\\[") {
        var closing = trimmed === "$$" ? "$$" : "\\]";
        var equationLines = [];
        index += 1;
        while (index < lines.length && lines[index].trim() !== closing) {
          equationLines.push(lines[index]);
          index += 1;
        }
        if (index < lines.length) index += 1;
        appendSourcePre(fragment, equationLines.join("\n"), true);
        continue;
      }

      if (/^\$\$[\s\S]+\$\$$/.test(trimmed)) {
        appendSourcePre(fragment, trimmed.slice(2, -2).trim(), true);
        index += 1;
        continue;
      }

      var heading = trimmed.match(/^(#{1,4})\s+(.+)$/);
      if (heading) {
        var headingNode = createNode("h" + Math.min(6, heading[1].length + 2), "source-heading source-heading-" + heading[1].length);
        appendInline(headingNode, heading[2]);
        fragment.appendChild(headingNode);
        index += 1;
        continue;
      }

      if (/^[-*]\s+/.test(trimmed)) {
        var unordered = createNode("ul", "source-list");
        while (index < lines.length) {
          var bullet = lines[index].trim().match(/^[-*]\s+(.+)$/);
          if (!bullet) break;
          var bulletItem = createNode("li", "");
          appendInline(bulletItem, bullet[1]);
          unordered.appendChild(bulletItem);
          index += 1;
        }
        fragment.appendChild(unordered);
        continue;
      }

      if (/^\d+[.)]\s+/.test(trimmed)) {
        var ordered = createNode("ol", "source-list source-list-ordered");
        var firstNumber = Number(trimmed.match(/^(\d+)/)[1]);
        if (firstNumber > 1) ordered.start = firstNumber;
        while (index < lines.length) {
          var numbered = lines[index].trim().match(/^\d+[.)]\s+(.+)$/);
          if (!numbered) break;
          var numberedItem = createNode("li", "");
          appendInline(numberedItem, numbered[1]);
          ordered.appendChild(numberedItem);
          index += 1;
        }
        fragment.appendChild(ordered);
        continue;
      }

      if (/^ {4}/.test(raw)) {
        var preLines = [];
        while (index < lines.length && (/^ {4}/.test(lines[index]) || !lines[index].trim())) {
          preLines.push(lines[index].replace(/^ {4}/, ""));
          index += 1;
        }
        appendSourcePre(fragment, preLines.join("\n").trimEnd(), false);
        continue;
      }

      var paragraphLines = [];
      while (index < lines.length) {
        var candidate = lines[index];
        var candidateTrimmed = candidate.trim();
        if (
          !candidateTrimmed ||
          /^\x60{3}/.test(candidateTrimmed) ||
          candidateTrimmed === "$$" ||
          candidateTrimmed === "\\[" ||
          /^(#{1,4})\s+/.test(candidateTrimmed) ||
          /^[-*]\s+/.test(candidateTrimmed) ||
          /^\d+[.)]\s+/.test(candidateTrimmed) ||
          /^ {4}/.test(candidate)
        ) break;
        var safeLine = stripRawHtml(candidateTrimmed);
        if (safeLine) paragraphLines.push(safeLine);
        index += 1;
      }
      if (paragraphLines.length) {
        var paragraph = createNode("p", "");
        appendInline(paragraph, paragraphLines.join(" "));
        fragment.appendChild(paragraph);
      } else {
        index += 1;
      }
    }

    return fragment;
  }

  function formatDateTime(value) {
    var date = dateObject(value);
    if (!date) return t("unavailable");
    return new Intl.DateTimeFormat(state.language === "ja" ? "ja-JP-u-ca-gregory" : "en-GB", {
      day: "2-digit",
      hour: "2-digit",
      hour12: false,
      minute: "2-digit",
      month: "short",
      timeZone: "UTC",
      timeZoneName: "short",
      year: "numeric"
    }).format(date);
  }

  function renderEditionContext(report) {
    if (report.schemaVersion < 2) {
      elements.editionContext.hidden = true;
      elements.sourceSection.hidden = true;
      elements.sourceDocument.replaceChildren();
      return;
    }

    elements.editionContext.hidden = false;
    elements.editionKind.textContent = t(kindValue(
      report.editionKind,
      "kind.dailyScreen",
      "kind.weeklyReview",
      "kind.monthlyReview"
    ));
    elements.editionKind.dataset.kind = report.editionKind;
    var sourceKindLabel = report.sourceKind === "chatgpt-scheduled-task"
      ? t("source.chatgpt")
      : report.sourceKind === "openai-responses-api"
        ? t("source.openai")
        : report.sourceKind === "local-arxiv-updater" ? t("source.local") : t("source.unknown");
    elements.editionSource.textContent = sourceKindLabel;
    elements.editionId.textContent = report.editionId || t("unavailable");

    var period = report.periodStart && report.periodEnd
      ? formatDate(report.periodStart, true) + " — " + formatDate(report.periodEnd, true)
      : formatDate(report.periodStart || report.periodEnd || report.editionDate, true);
    elements.editionPeriod.textContent = period;
    elements.editionImported.textContent = formatDateTime(report.importedAt);

    elements.sourceSection.hidden = !report.sourceText;
    elements.sourceTitle.textContent = t(kindValue(
      report.editionKind,
      "source.dailyTitle",
      "source.weeklyTitle",
      "source.monthlyTitle"
    ));
    elements.sourceDocument.replaceChildren();
    elements.sourceDocument.lang = state.language;
    if (report.sourceText) {
      var sourceText = report.editionKind === "weekly" || report.editionKind === "monthly"
        ? linkWeeklySourceText(report.sourceText, report.papers)
        : report.sourceText;
      elements.sourceDocument.appendChild(renderMarkdownLite(sourceText));
    }
  }


  function renderStatus(report, archived) {
    var freshness = editionFreshness(report, archived);
    var activityAt = report.importedAt || report.checkedAt || report.generatedAt;
    var checked = archived
      ? t("status.snapshot")
      : relativeTime(activityAt, report.schemaVersion >= 2 ? t("time.imported") : t("time.checked"));
    elements.headerStatus.dataset.state = freshness.state;
    elements.headerStatus.lastElementChild.textContent = freshness.label + " · " + checked;
    elements.freshnessShort.textContent = freshness.short;
    elements.updateNote.dataset.state = freshness.state;

    var message = report.message || report.statusMessage || freshness.label + ".";
    elements.updateNote.textContent = message + (archived ? "" : (state.language === "ja" ? " · " + checked : " " + checked.charAt(0).toUpperCase() + checked.slice(1) + "."));
    return freshness;
  }
  function renderFilters(papers) {
    var counts = topicCounts(papers);
    if (state.filter !== "all" && !counts.some(function (entry) { return entry[0] === state.filter; })) {
      state.filter = "all";
    }

    var fragment = document.createDocumentFragment();
    [["all", papers.length]].concat(counts).forEach(function (entry) {
      var button = createNode("button", "topic-filter", entry[0] === "all" ? t("filter.all") + " · " + entry[1] : topicLabel(entry[0]) + " · " + entry[1]);
      button.type = "button";
      button.dataset.topic = entry[0];
      button.setAttribute("aria-pressed", String(state.filter === entry[0]));
      button.addEventListener("click", function () {
        state.filter = button.dataset.topic;
        elements.filters.querySelectorAll("button").forEach(function (item) {
          item.setAttribute("aria-pressed", String(item === button));
        });
        renderPapers();
      });
      fragment.appendChild(button);
    });
    elements.filters.replaceChildren(fragment);
  }

  function searchText(paper) {
    return [
      paper.title,
      paper.abstract,
      paper.arxivId,
      paper.authors.join(" "),
      paper.topics.join(" "),
      paper.topics.map(topicLabel).join(" "),
      paper.scoreReasons.join(" "),
      paper.schedulerLabel,
      paper.schedulerSummary,
      paper.ratings.map(function (rating) { return rating.label; }).join(" ")
    ].join(" ").toLocaleLowerCase();
  }

  function paperMatches(paper) {
    var topicMatch = state.filter === "all" || paper.topics.indexOf(state.filter) !== -1;
    var queryMatch = !state.query || searchText(paper).indexOf(state.query) !== -1;
    return topicMatch && queryMatch;
  }

  function abstractPreview(value) {
    if (value.length <= 360) return value;
    var sample = value.slice(0, 357);
    var breakAt = sample.lastIndexOf(" ");
    return sample.slice(0, breakAt > 250 ? breakAt : sample.length) + "…";
  }

  function displayScore(value) {
    if (value === null) return "";
    return Number.isInteger(value) ? String(value) : value.toFixed(2).replace(/0+$/, "").replace(/\.$/, "");
  }

  function scoreWidth(value) {
    if (value === null) return 0;
    var scaled = value >= 0 && value <= 1 ? value * 100 : value;
    return Math.min(100, Math.max(0, scaled));
  }

  function widthClass(value) {
    var bounded = Math.min(100, Math.max(0, value));
    return "width-" + Math.round(bounded / 5) * 5;
  }

  function externalLink(url, className, label, ariaLabel) {
    if (!url) return null;
    var link = createNode("a", className);
    link.href = url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.setAttribute("aria-label", ariaLabel + " " + t("link.newTabSuffix"));
    link.appendChild(document.createTextNode(label + " "));
    link.appendChild(createNode("span", "external-arrow", "↗"));
    link.lastElementChild.setAttribute("aria-hidden", "true");
    return link;
  }

  function renderSchedulerRatings(paper) {
    if (!paper.ratings.length) return null;
    var block = createNode("div", "scheduler-ratings");
    block.appendChild(createNode("p", "scheduler-ratings-title", t("ratings.title")));
    var grid = createNode("div", "scheduler-ratings-grid");
    paper.ratings.forEach(function (rating) {
      var item = createNode("div", "scheduler-rating");
      var header = createNode("div", "scheduler-rating-head");
      header.appendChild(createNode("span", "", rating.label));
      header.appendChild(createNode("strong", "", displayScore(rating.value) + "/" + rating.scale));
      item.appendChild(header);
      var track = createNode("span", "scheduler-rating-track");
      track.setAttribute("role", "meter");
      track.setAttribute("aria-label", rating.label + ": " + t("ratings.outOf", { value: displayScore(rating.value), scale: rating.scale }));
      track.setAttribute("aria-valuemin", "0");
      track.setAttribute("aria-valuemax", String(rating.scale));
      track.setAttribute("aria-valuenow", String(rating.value));
      var fill = createNode("span", "scheduler-rating-fill");
      fill.classList.add(widthClass(rating.value / rating.scale * 100));
      track.appendChild(fill);
      item.appendChild(track);
      grid.appendChild(item);
    });
    block.appendChild(grid);
    return block;
  }

  function renderPaper(paper) {
    var item = createNode("li", "paper-card");
    var number = paper.schedulerRank === null ? paper.index + 1 : paper.schedulerRank;
    var rank = displayScore(number).padStart(2, "0");
    var index = createNode("span", "paper-index", rank);
    index.setAttribute("aria-hidden", "true");
    item.appendChild(index);

    var main = createNode("article", "paper-main");
    var top = createNode("div", "paper-topline");
    var dateParts = [];
    if (paper.submittedDate) dateParts.push(t("paper.submitted") + " " + formatDate(paper.submittedDate, true));
    if (paper.updatedDate && paper.updatedDate !== paper.submittedDate) dateParts.push(t("paper.updated") + " " + formatDate(paper.updatedDate, true));
    if (paper.arxivId) dateParts.push("arXiv:" + paper.arxivId);
    top.appendChild(createNode("span", "paper-date", dateParts.join(" · ") || t("paper.record")));

    var badges = createNode("div", "paper-badges");
    if (paper.schedulerLabel) {
      badges.appendChild(createNode("span", "scheduler-label", paper.schedulerLabel));
    }
    if (paper.schedulerRating !== null) {
      badges.appendChild(createNode("span", "score-pill scheduler-rating-pill", t("paper.review") + " · " + displayScore(paper.schedulerRating) + "/" + paper.schedulerRatingScale));
    }
    if (paper.score !== null) {
      badges.appendChild(createNode("span", "score-pill", (paper.schedulerRating !== null ? t("paper.screenScore") + " · " : t("paper.relevance") + " · ") + displayScore(paper.score)));
    }
    if (badges.childElementCount) top.appendChild(badges);
    main.appendChild(top);

    var heading = createNode("h3", "paper-title");
    heading.lang = "en";
    if (paper.absUrl) {
      var titleLink = externalLink(paper.absUrl, "paper-title-link", paper.title, t("paper.openTitle", { title: paper.title }));
      heading.appendChild(titleLink);
    } else {
      heading.textContent = paper.title;
    }
    main.appendChild(heading);
    var authors = createNode("p", "paper-authors", paper.authors.length ? paper.authors.join(", ") : t("paper.authorsMissing"));
    if (paper.authors.length) authors.lang = "en";
    main.appendChild(authors);

    if (paper.topics.length) {
      var topicWrap = createNode("div", "paper-topics");
      paper.topics.forEach(function (topic) { topicWrap.appendChild(createNode("span", "topic-tag", topicLabel(topic))); });
      main.appendChild(topicWrap);
    }

    if (paper.schedulerSummary) {
      var summary = createNode("div", "scheduler-summary");
      summary.appendChild(createNode("span", "scheduler-summary-label", t("paper.scheduledReview")));
      summary.appendChild(createNode("p", "", paper.schedulerSummary));
      main.appendChild(summary);
    }

    var schedulerRatings = renderSchedulerRatings(paper);
    if (schedulerRatings) main.appendChild(schedulerRatings);

    if (paper.abstract) {
      var abstract = createNode("p", "paper-abstract", abstractPreview(paper.abstract));
      abstract.lang = "en";
      main.appendChild(abstract);
      if (paper.abstract.length > 360) {
        var details = createNode("details", "abstract-details");
        details.appendChild(createNode("summary", "", t("paper.fullAbstract")));
        var fullAbstract = createNode("p", "", paper.abstract);
        fullAbstract.lang = "en";
        details.appendChild(fullAbstract);
        main.appendChild(details);
      }
    }

    if (paper.score !== null || paper.scoreReasons.length) {
      var relevance = createNode("div", "relevance-block");
      var relevanceHead = createNode("div", "relevance-head");
      relevanceHead.appendChild(createNode("span", "", paper.scoreReasons.length ? t("paper.deterministicEvidence") : t("paper.deterministicScore")));
      if (paper.score !== null) {
        var track = createNode("span", "score-track");
        track.setAttribute("role", "meter");
        track.setAttribute("aria-label", t("paper.relevanceAria", { value: displayScore(paper.score) }));
        track.setAttribute("aria-valuemin", "0");
        track.setAttribute("aria-valuemax", "100");
        track.setAttribute("aria-valuenow", String(scoreWidth(paper.score)));
        var fill = createNode("span", "score-fill");
        fill.classList.add(widthClass(scoreWidth(paper.score)));
        track.appendChild(fill);
        relevanceHead.appendChild(track);
      }
      relevance.appendChild(relevanceHead);
      if (paper.scoreReasons.length) {
        var reasons = createNode("ul", "reason-list");
        paper.scoreReasons.forEach(function (reason) { reasons.appendChild(createNode("li", "", reason)); });
        relevance.appendChild(reasons);
      }
      main.appendChild(relevance);
    }

    var actions = createNode("div", "paper-actions");
    var sourceActions = createNode("div", "paper-source-actions");
    var abstractLink = externalLink(paper.absUrl, "arxiv-link", t("paper.abstractLink"), t("paper.openAbstract", { title: paper.title }));
    var pdfLink = externalLink(paper.pdfUrl, "pdf-link", t("paper.pdfLink"), t("paper.openPdf", { title: paper.title }));
    if (abstractLink) sourceActions.appendChild(abstractLink);
    if (pdfLink) sourceActions.appendChild(pdfLink);
    if (sourceActions.childElementCount) actions.appendChild(sourceActions);

    var impactActions = createNode("div", "paper-impact-actions");
    var webSearchLink = externalLink(
      archiveUi.webSearchUrl(paper),
      "impact-search-link impact-search-web",
      t("paper.webSearch"),
      t("paper.searchWebAria", { title: paper.title })
    );
    var xSearchLink = externalLink(
      archiveUi.xSearchUrl(paper),
      "impact-search-link impact-search-x",
      t("paper.xSearch"),
      t("paper.searchXAria", { title: paper.title })
    );
    if (webSearchLink || xSearchLink) {
      impactActions.setAttribute("role", "group");
      impactActions.setAttribute("aria-label", t("paper.impactLabel"));
      impactActions.appendChild(createNode("span", "paper-impact-label", t("paper.impactLabel")));
      if (webSearchLink) impactActions.appendChild(webSearchLink);
      if (xSearchLink) impactActions.appendChild(xSearchLink);
      actions.appendChild(impactActions);
    }
    if (actions.childElementCount) main.appendChild(actions);

    item.appendChild(main);
    return item;
  }

  function renderPapers() {
    if (!state.report) return;
    var visible = state.report.papers.filter(paperMatches);
    var fragment = document.createDocumentFragment();
    visible.forEach(function (paper, index) { fragment.appendChild(renderPaper(paper, index)); });
    elements.paperList.replaceChildren(fragment);
    elements.noResults.hidden = visible.length > 0;
  }

  function renderLens(papers) {
    var counts = topicCounts(papers).slice(0, 8);
    if (!counts.length) {
      elements.lensVisual.replaceChildren(createNode("p", "empty-lens", t("lens.none")));
      return;
    }
    var maximum = Math.max.apply(null, counts.map(function (entry) { return entry[1]; }));
    var fragment = document.createDocumentFragment();
    counts.forEach(function (entry) {
      var row = createNode("div", "lens-row");
      row.appendChild(createNode("span", "lens-name", topicLabel(entry[0])));
      var track = createNode("span", "lens-track");
      var fill = createNode("span", "lens-fill");
      fill.classList.add(widthClass(Math.max(10, Math.round(entry[1] / maximum * 100))));
      track.appendChild(fill);
      row.appendChild(track);
      row.appendChild(createNode("span", "lens-count", entry[1]));
      fragment.appendChild(row);
    });
    elements.lensVisual.replaceChildren(fragment);
  }

  function renderReport(report, archived) {
    state.report = report;
    state.filter = "all";
    state.query = "";
    elements.search.value = "";
    elements.loading.hidden = true;

    var edition = report.editionDate || report.observedBatchDate || report.expectedBatchDate || report.checkedAt || report.generatedAt;
    var displayDate = formatDate(edition, false);
    var kindLabel = t(kindValue(
      report.editionKind,
      "kind.daily",
      "kind.weekly",
      "kind.monthly"
    ));
    elements.editionDate.textContent = (archived ? t("edition.archivePrefix") + " · " : kindLabel + " · ") + displayDate;
    elements.paperCount.textContent = report.papers.length;
    elements.topicCount.textContent = topicCounts(report.papers).length;
    var currentTitleKey = kindValue(
      report.editionKind,
      "edition.dailyCurrent",
      "edition.weeklyCurrent",
      "edition.monthlyCurrent"
    );
    var archivedTitleKey = kindValue(
      report.editionKind,
      "edition.dailyArchived",
      "edition.weeklyArchived",
      "edition.monthlyArchived"
    );
    elements.digestTitle.textContent = archived
      ? t(archivedTitleKey, { date: displayDate })
      : t(currentTitleKey);
    document.title = archived
      ? t("edition.archivedPageTitle", { kind: kindLabel, date: displayDate })
      : t(kindValue(
        report.editionKind,
        "edition.dailyPageTitle",
        "edition.weeklyPageTitle",
        "edition.monthlyPageTitle"
      ));
    renderEditionContext(report);
    var freshness = renderStatus(report, archived);

    elements.paperList.replaceChildren();
    if (report.papers.length) {
      clearNotice();
      elements.paperIndexHeading.hidden = !(report.schemaVersion >= 2 && report.sourceText);
      elements.toolbar.hidden = false;
      renderFilters(report.papers);
      renderPapers();
    } else {
      elements.paperIndexHeading.hidden = true;
      elements.toolbar.hidden = true;
      elements.noResults.hidden = true;
      var copy = statusCopyFor(report.status);
      var message = report.message || report.statusMessage || t("empty.noPapersMessage");
      showNotice(copy.emptyTitle, message, freshness.state);
    }
    renderLens(report.papers);
  }

  function archiveFilters() {
    return {
      kind: state.archiveKind,
      from: state.archiveFrom,
      to: state.archiveTo
    };
  }

  function filteredArchiveReports() {
    return archiveUi.filterReports(state.reports, archiveFilters());
  }

  function syncArchiveFilterUrl() {
    var url = new URL(window.location.href);
    if (state.archiveKind === "all") url.searchParams.delete("archive-kind");
    else url.searchParams.set("archive-kind", state.archiveKind);
    if (state.archiveFrom) url.searchParams.set("archive-from", state.archiveFrom);
    else url.searchParams.delete("archive-from");
    if (state.archiveTo) url.searchParams.set("archive-to", state.archiveTo);
    else url.searchParams.delete("archive-to");
    window.history.replaceState(null, "", url.href);
  }

  function renderArchiveControls(filteredReports) {
    var counts = archiveUi.countKinds(state.reports, state.archiveFrom, state.archiveTo);
    elements.archiveKindButtons.forEach(function (button) {
      var kind = archiveUi.normaliseKind(button.dataset.archiveKind);
      button.setAttribute("aria-pressed", String(kind === state.archiveKind));
      button.disabled = !state.reports.length;
      var count = button.querySelector("[data-archive-count]");
      if (count) count.textContent = String(counts[kind]);
    });

    elements.archiveFrom.value = state.archiveFrom;
    elements.archiveTo.value = state.archiveTo;
    var dates = state.reports.map(function (report) {
      return archiveUi.reportFilterDate(report);
    }).filter(Boolean).sort();
    if (dates.length) {
      elements.archiveFrom.min = dates[0];
      elements.archiveFrom.max = dates[dates.length - 1];
      elements.archiveTo.min = dates[0];
      elements.archiveTo.max = dates[dates.length - 1];
      elements.archiveFrom.disabled = false;
      elements.archiveTo.disabled = false;
    } else {
      elements.archiveFrom.removeAttribute("min");
      elements.archiveFrom.removeAttribute("max");
      elements.archiveTo.removeAttribute("min");
      elements.archiveTo.removeAttribute("max");
      elements.archiveFrom.disabled = true;
      elements.archiveTo.disabled = true;
    }

    var filtersActive = state.archiveKind !== "all" || state.archiveFrom || state.archiveTo;
    elements.archiveClear.disabled = !filtersActive;
    elements.archiveResults.textContent = t("archive.results", {
      count: filteredReports.length,
      total: state.reports.length,
      visible: Math.min(state.archiveVisible, filteredReports.length)
    });
  }

  function applyArchiveFilterChange() {
    state.archiveVisible = 6;
    syncArchiveFilterUrl();
    renderArchive();
  }

  function updateArchiveDate(boundary, rawValue) {
    var value = archiveUi.normaliseDate(rawValue);
    if (boundary === "from") {
      state.archiveFrom = value;
      if (value && state.archiveTo && value > state.archiveTo) state.archiveTo = value;
    } else {
      state.archiveTo = value;
      if (value && state.archiveFrom && value < state.archiveFrom) state.archiveFrom = value;
    }
    applyArchiveFilterChange();
  }

  function archiveHref(editionId) {
    var url = new URL(window.location.href);
    url.searchParams.set("edition", editionId);
    if (state.language === "en") url.searchParams.set("lang", "en");
    else url.searchParams.delete("lang");
    url.hash = "digest";
    return url.href;
  }

  function archiveState(status) {
    var copy = statusCopyFor(status);
    return { label: copy.label, state: copy.state };
  }

  function renderArchive() {
    var filteredReports = filteredArchiveReports();
    renderArchiveControls(filteredReports);
    if (!state.reports.length) {
      elements.archiveList.replaceChildren(createNode("p", "archive-empty", t("archive.none")));
      elements.archiveMore.hidden = true;
      return;
    }
    if (!filteredReports.length) {
      elements.archiveList.replaceChildren(createNode("p", "archive-empty", t("archive.noMatches")));
      elements.archiveMore.hidden = true;
      return;
    }

    var fragment = document.createDocumentFragment();
    filteredReports.slice(0, state.archiveVisible).forEach(function (report) {
      var archiveTitle = t(kindValue(
        report.kind,
        "archive.dailyTitle",
        "archive.weeklyTitle",
        "archive.monthlyTitle"
      ));
      var link = createNode("a", "archive-row");
      link.href = archiveHref(report.editionId);
      if (state.archiveEdition === report.editionId) link.setAttribute("aria-current", "page");
      link.appendChild(createNode("span", "archive-date", formatDate(report.date, true)));

      var meta = createNode("span", "archive-meta");
      meta.appendChild(createNode("strong", "archive-title-text", archiveTitle));
      meta.appendChild(createNode("span", "archive-kind", t(kindValue(
        report.kind,
        "kind.daily",
        "kind.weekly",
        "kind.monthly"
      ))));
      if (report.sourceKind === "chatgpt-scheduled-task") {
        meta.appendChild(createNode("span", "archive-source", t("archive.scheduledTask")));
      } else if (report.sourceKind === "openai-responses-api") {
        meta.appendChild(createNode("span", "archive-source", t("archive.openai")));
      }
      var visual = archiveState(report.status);
      var status = createNode("span", "archive-state", visual.label);
      status.dataset.state = visual.state;
      meta.appendChild(status);
      var paperCount = t(report.paperCount === 1 ? "paper.countOne" : "paper.countMany", { count: report.paperCount });
      meta.appendChild(createNode("span", "", paperCount));
      link.appendChild(meta);
      link.setAttribute("aria-label", [
        archiveTitle,
        formatDate(report.date, true),
        visual.label,
        paperCount
      ].join(", "));

      var arrow = createNode("span", "archive-arrow", "→");
      arrow.setAttribute("aria-hidden", "true");
      link.appendChild(arrow);
      var archiveItem = createNode("div", "archive-item");
      archiveItem.setAttribute("role", "listitem");
      archiveItem.appendChild(link);
      fragment.appendChild(archiveItem);
    });

    elements.archiveList.replaceChildren(fragment);
    elements.archiveMore.hidden = state.archiveVisible >= filteredReports.length;
  }

  async function loadArchiveIndex() {
    try {
      var raw = await fetchJson(new URL("./data/archive/index.json", document.baseURI));
      state.reports = normaliseArchive(raw);
      renderArchive();
      return state.reports;
    } catch (_error) {
      state.reports = [];
      renderArchiveControls([]);
      elements.archiveList.replaceChildren(createNode("p", "archive-empty", t("archive.indexUnavailable")));
      elements.archiveMore.hidden = true;
      return [];
    }
  }

  function showLoadFailure(archived) {
    elements.loading.hidden = true;
    elements.toolbar.hidden = true;
    elements.paperIndexHeading.hidden = true;
    elements.editionContext.hidden = true;
    elements.sourceSection.hidden = true;
    elements.sourceDocument.replaceChildren();
    elements.paperList.replaceChildren();
    elements.paperCount.textContent = "0";
    elements.topicCount.textContent = "0";
    elements.freshnessShort.textContent = t("load.offlineShort");
    elements.headerStatus.dataset.state = "offline";
    elements.headerStatus.lastElementChild.textContent = t("load.editionUnavailable");
    elements.updateNote.dataset.state = "offline";
    elements.updateNote.textContent = archived ? t("load.archivedMessage") : t("load.latestMessage");
    showNotice(
      archived ? t("load.archivedTitle") : t("load.latestTitle"),
      t("load.detail"),
      "offline"
    );
    renderLens([]);
  }

  async function initialise() {
    applyStaticLocale();
    syncArchiveFilterUrl();

    document.querySelectorAll("[data-language]").forEach(function (button) {
      button.lang = button.dataset.language;
      button.addEventListener("click", function () {
        var language = normaliseLanguage(button.dataset.language);
        if (language !== state.language) window.location.assign(languageUrl(language).href);
      });
    });

    elements.search.addEventListener("input", function () {
      state.query = elements.search.value.trim().toLocaleLowerCase();
      renderPapers();
    });

    elements.archiveKindButtons.forEach(function (button) {
      button.addEventListener("click", function () {
        state.archiveKind = archiveUi.normaliseKind(button.dataset.archiveKind);
        applyArchiveFilterChange();
      });
    });

    elements.archiveFrom.addEventListener("change", function () {
      updateArchiveDate("from", elements.archiveFrom.value);
    });

    elements.archiveTo.addEventListener("change", function () {
      updateArchiveDate("to", elements.archiveTo.value);
    });

    elements.archiveClear.addEventListener("click", function () {
      state.archiveKind = "all";
      state.archiveFrom = "";
      state.archiveTo = "";
      applyArchiveFilterChange();
    });

    elements.archiveMore.addEventListener("click", function () {
      state.archiveVisible += 6;
      renderArchive();
    });

    var translationPromise = state.language === "en"
      ? fetchJson(new URL("./data/i18n/en.json", document.baseURI)).then(normaliseTranslations).catch(function () { return null; })
      : Promise.resolve(new Map());
    var archivePromise = loadArchiveIndex();
    var archived = false;
    var reportPromise;

    if (state.archiveEdition) {
      archived = true;
      if (!validEditionId(state.archiveEdition)) {
        await archivePromise;
        showLoadFailure(true);
        return;
      }

      var reports = await archivePromise;
      var selected = reports.find(function (item) { return item.editionId === state.archiveEdition; });
      var selectedUrl = archiveDataUrl(selected);
      if (!selected || !selectedUrl) {
        showLoadFailure(true);
        return;
      }
      reportPromise = fetchJson(selectedUrl);
    } else {
      reportPromise = fetchJson(new URL("./data/latest.json", document.baseURI));
    }

    try {
      var raw = await reportPromise;
      var report = normaliseReport(raw);
      if (archived && report.schemaVersion >= 2 && report.editionId !== state.archiveEdition) {
        throw new Error("Archive edition identity mismatch.");
      }
      state.rawReport = report;
      if (state.language === "en") {
        var translations = await translationPromise;
        if (translations) state.translations = translations;
        var translatedReport = translations ? localiseReport(report) : null;
        if (!translatedReport) {
          fallBackToJapanese();
          translatedReport = report;
        }
        renderReport(translatedReport, archived);
      } else {
        renderReport(report, archived);
      }
    } catch (_error) {
      showLoadFailure(archived);
    }
  }

  initialise();
})();
