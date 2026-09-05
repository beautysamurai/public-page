(function (root, factory) {
  "use strict";

  var api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.RatesArchiveUi = api;
})(typeof window !== "undefined" ? window : globalThis, function () {
  "use strict";

  var FILTER_KINDS = ["all", "daily", "weekly", "monthly"];
  var REPORT_KINDS = ["daily", "weekly", "monthly"];

  function normaliseKind(value) {
    return FILTER_KINDS.indexOf(value) >= 0 ? value : "all";
  }

  function normaliseReportKind(value) {
    return REPORT_KINDS.indexOf(value) >= 0 ? value : "daily";
  }

  function normaliseDate(value) {
    if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return "";
    var parts = value.split("-").map(Number);
    var parsed = new Date(Date.UTC(parts[0], parts[1] - 1, parts[2]));
    if (
      parsed.getUTCFullYear() !== parts[0] ||
      parsed.getUTCMonth() + 1 !== parts[1] ||
      parsed.getUTCDate() !== parts[2]
    ) return "";
    return value;
  }

  function normaliseRange(from, to) {
    var safeFrom = normaliseDate(from);
    var safeTo = normaliseDate(to);
    if (safeFrom && safeTo && safeFrom > safeTo) {
      return { from: safeTo, to: safeFrom };
    }
    return { from: safeFrom, to: safeTo };
  }

  function reportFilterDate(report) {
    if (!report || typeof report !== "object") return "";
    var kind = normaliseReportKind(report.kind);
    if (kind !== "daily") {
      var periodEnd = normaliseDate(report.periodEnd);
      if (periodEnd) return periodEnd;
    }
    return normaliseDate(report.date);
  }

  function filterReports(reports, filters) {
    var source = Array.isArray(reports) ? reports : [];
    var options = filters && typeof filters === "object" ? filters : {};
    var kind = normaliseKind(options.kind);
    var range = normaliseRange(options.from, options.to);
    return source.filter(function (report) {
      if (!report || typeof report !== "object") return false;
      var date = reportFilterDate(report);
      if (!date) return false;
      if (kind !== "all" && normaliseReportKind(report.kind) !== kind) return false;
      if (range.from && date < range.from) return false;
      if (range.to && date > range.to) return false;
      return true;
    });
  }

  function countKinds(reports, from, to) {
    var matchingDates = filterReports(reports, { kind: "all", from: from, to: to });
    return matchingDates.reduce(function (counts, report) {
      counts.all += 1;
      counts[normaliseReportKind(report.kind)] += 1;
      return counts;
    }, { all: 0, daily: 0, weekly: 0, monthly: 0 });
  }

  function versionlessArxivId(value) {
    return typeof value === "string" ? value.trim().replace(/v\d+$/i, "") : "";
  }

  function normaliseTag(value) {
    return typeof value === "string" ? value.replace(/\s+/g, " ").trim().toLowerCase().slice(0, 120) : "";
  }

  function ratingOutOfTen(paper) {
    var value = paper && paper.schedulerRating;
    var scale = paper && paper.schedulerRatingScale;
    if (typeof value !== "number" || typeof scale !== "number" ||
        !Number.isFinite(value) || !Number.isFinite(scale) || scale <= 0 || scale > 100 ||
        value < 0 || value > scale) return null;
    return value / scale * 10;
  }

  function normaliseMinRating(value) {
    if (value === null || value === "" || value === undefined) return "";
    var number = Number(value);
    return Number.isInteger(number) && number >= 0 && number <= 10 ? String(number) : "";
  }

  function normalisePaperSort(value) {
    return value === "date" ? "date" : "rating";
  }

  function collectPapers(editions, filters) {
    var grouped = new Map();
    // The catalogue is generated newest-edition-first. Filter editions before
    // deduplicating, so score, tags and provenance refer to the same review.
    filterReports(editions, filters).forEach(function (edition) {
      (Array.isArray(edition.papers) ? edition.papers : []).forEach(function (paper) {
        var id = versionlessArxivId(paper.arxivId).toLowerCase();
        if (!id) return;
        var entry = grouped.get(id);
        if (!entry) {
          entry = { paper: paper, rating: ratingOutOfTen(paper), reviews: [] };
          grouped.set(id, entry);
        }
        entry.reviews.push({
          editionId: edition.editionId, date: edition.date, kind: normaliseReportKind(edition.kind),
          rating: ratingOutOfTen(paper)
        });
      });
    });
    return Array.from(grouped.values());
  }

  function paperTags(entries) {
    var tags = new Map();
    entries.forEach(function (entry) {
      var counted = new Set();
      (entry.paper.topics || []).forEach(function (label) {
        var key = normaliseTag(label);
        if (!key || counted.has(key)) return;
        counted.add(key);
        var item = tags.get(key) || { key: key, label: label, count: 0 };
        item.count += 1;
        tags.set(key, item);
      });
    });
    return Array.from(tags.values()).sort(function (a, b) { return a.label.localeCompare(b.label); });
  }

  function filterPapers(entries, filters) {
    var options = filters || {};
    var min = normaliseMinRating(options.minRating);
    var tag = normaliseTag(options.tag);
    var query = typeof options.query === "string" ? options.query.trim().toLowerCase() : "";
    var sort = normalisePaperSort(options.sort);
    return entries.filter(function (entry) {
      if (min !== "" && (entry.rating === null || entry.rating < Number(min))) return false;
      if (tag && !(entry.paper.topics || []).some(function (label) { return normaliseTag(label) === tag; })) return false;
      var text = [entry.paper.title, entry.paper.arxivId].concat(entry.paper.authors || [], entry.paper.topics || []);
      return !query || text.join(" ").toLowerCase().indexOf(query) !== -1;
    }).sort(function (a, b) {
      var ratingOrder = (b.rating === null ? -1 : b.rating) - (a.rating === null ? -1 : a.rating);
      var dateOrder = b.reviews[0].date.localeCompare(a.reviews[0].date);
      return (sort === "date" ? dateOrder || ratingOrder : ratingOrder || dateOrder) ||
        a.paper.arxivId.localeCompare(b.paper.arxivId);
    });
  }

  function paperSearchQuery(paper) {
    var source = paper && typeof paper === "object" ? paper : {};
    var title = typeof source.title === "string"
      ? source.title.replace(/\s+/g, " ").trim().slice(0, 180)
      : "";
    var arxivId = versionlessArxivId(source.arxivId);
    return [title, arxivId ? "arXiv:" + arxivId : ""].filter(Boolean).join(" ");
  }

  function webSearchUrl(paper) {
    var query = paperSearchQuery(paper);
    if (!query) return "";
    var url = new URL("https://www.google.com/search");
    url.searchParams.set("q", query);
    return url.href;
  }

  function xSearchUrl(paper) {
    var query = paperSearchQuery(paper);
    if (!query) return "";
    var url = new URL("https://x.com/search");
    url.searchParams.set("q", query);
    url.searchParams.set("f", "live");
    return url.href;
  }

  return Object.freeze({
    countKinds: countKinds,
    collectPapers: collectPapers,
    filterPapers: filterPapers,
    filterReports: filterReports,
    normaliseDate: normaliseDate,
    normaliseKind: normaliseKind,
    normaliseMinRating: normaliseMinRating,
    normalisePaperSort: normalisePaperSort,
    normaliseRange: normaliseRange,
    normaliseTag: normaliseTag,
    paperTags: paperTags,
    paperSearchQuery: paperSearchQuery,
    ratingOutOfTen: ratingOutOfTen,
    reportFilterDate: reportFilterDate,
    versionlessArxivId: versionlessArxivId,
    webSearchUrl: webSearchUrl,
    xSearchUrl: xSearchUrl
  });
});
