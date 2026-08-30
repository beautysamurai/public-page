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

  function filterReports(reports, filters) {
    var source = Array.isArray(reports) ? reports : [];
    var options = filters && typeof filters === "object" ? filters : {};
    var kind = normaliseKind(options.kind);
    var range = normaliseRange(options.from, options.to);
    return source.filter(function (report) {
      if (!report || typeof report !== "object") return false;
      var date = normaliseDate(report.date);
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
    filterReports: filterReports,
    normaliseDate: normaliseDate,
    normaliseKind: normaliseKind,
    normaliseRange: normaliseRange,
    paperSearchQuery: paperSearchQuery,
    versionlessArxivId: versionlessArxivId,
    webSearchUrl: webSearchUrl,
    xSearchUrl: xSearchUrl
  });
});
