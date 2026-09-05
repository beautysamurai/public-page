"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const archiveUi = require("../site/archive-ui.js");

const reports = [
  { editionId: "d3", date: "2026-08-30", kind: "daily" },
  { editionId: "m1", date: "2026-08-30", periodEnd: "2026-08-29", kind: "monthly" },
  { editionId: "w1", date: "2026-08-30", periodEnd: "2026-08-28", kind: "weekly" },
  { editionId: "d2", date: "2026-08-27", kind: "daily" },
  { editionId: "d1", date: "2026-08-26", kind: "daily" }
];

test("archive filters combine kind and inclusive date bounds", () => {
  assert.deepEqual(
    archiveUi.filterReports(reports, {
      kind: "daily",
      from: "2026-08-26",
      to: "2026-08-27"
    }).map((report) => report.editionId),
    ["d2", "d1"]
  );
  assert.deepEqual(
    archiveUi.filterReports(reports, { kind: "weekly" }).map((report) => report.editionId),
    ["w1"]
  );
  assert.deepEqual(
    archiveUi.filterReports(reports, { kind: "monthly" }).map((report) => report.editionId),
    ["m1"]
  );
});

test("archive dates are calendar-valid and reversed ranges are normalized", () => {
  assert.equal(archiveUi.normaliseDate("2026-02-29"), "");
  assert.equal(archiveUi.normaliseDate("2028-02-29"), "2028-02-29");
  assert.deepEqual(
    archiveUi.normaliseRange("2026-08-30", "2026-08-26"),
    { from: "2026-08-26", to: "2026-08-30" }
  );
  assert.deepEqual(
    archiveUi.filterReports(reports, {
      kind: "not-a-kind",
      from: "2026-08-30",
      to: "2026-08-26"
    }).map((report) => report.editionId),
    ["d3", "m1", "w1", "d2", "d1"]
  );
});

test("kind counts reflect the selected date range", () => {
  assert.deepEqual(
    archiveUi.countKinds(reports, "2026-08-27", "2026-08-29"),
    { all: 3, daily: 1, weekly: 1, monthly: 1 }
  );
});

test("aggregate editions use period end while daily editions use edition date", () => {
  assert.equal(archiveUi.reportFilterDate(reports[0]), "2026-08-30");
  assert.equal(archiveUi.reportFilterDate(reports[1]), "2026-08-29");
  assert.equal(archiveUi.reportFilterDate(reports[2]), "2026-08-28");
  assert.deepEqual(
    archiveUi.filterReports(reports, { to: "2026-08-28" }).map((report) => report.editionId),
    ["w1", "d2", "d1"]
  );
});

test("search links use fixed providers and safely encoded paper identity", () => {
  const paper = {
    title: "A&B? \"# <img> 日本語",
    arxivId: "2608.12345v3"
  };
  const expectedQuery = "A&B? \"# <img> 日本語 arXiv:2608.12345";
  const web = new URL(archiveUi.webSearchUrl(paper));
  const x = new URL(archiveUi.xSearchUrl(paper));

  assert.equal(web.origin, "https://www.google.com");
  assert.equal(web.pathname, "/search");
  assert.equal(web.searchParams.get("q"), expectedQuery);
  assert.equal(x.origin, "https://x.com");
  assert.equal(x.pathname, "/search");
  assert.equal(x.searchParams.get("q"), expectedQuery);
  assert.equal(x.searchParams.get("f"), "live");
});

test("search identity removes modern and legacy arXiv versions", () => {
  assert.equal(archiveUi.versionlessArxivId("2608.12345v12"), "2608.12345");
  assert.equal(archiveUi.versionlessArxivId("hep-th/9901001v2"), "hep-th/9901001");
  assert.equal(
    archiveUi.paperSearchQuery({ arxivId: "hep-th/9901001v2" }),
    "arXiv:hep-th/9901001"
  );
  assert.equal(archiveUi.webSearchUrl({}), "");
  assert.equal(archiveUi.xSearchUrl({}), "");
});

const catalogue = [
  { editionId: "weekly", date: "2026-09-05", kind: "weekly", periodEnd: "2026-09-04", papers: [
    { arxivId: "2608.00001v2", title: "New assessment", authors: ["Smith"], topics: ["Rates", "rates"], schedulerRating: 3, schedulerRatingScale: 5 }
  ] },
  { editionId: "daily", date: "2026-09-04", kind: "daily", papers: [
    { arxivId: "2608.00002v1", title: "Execution", topics: ["Market making"], schedulerRating: 9.5, schedulerRatingScale: 10 },
    { arxivId: "2608.00003", title: "Unknown", topics: [], schedulerRating: null, schedulerRatingScale: null },
    { arxivId: "2608.00004", title: "Zero", topics: [], schedulerRating: 0, schedulerRatingScale: 10 }
  ] },
  { editionId: "old", date: "2026-08-01", kind: "daily", papers: [
    { arxivId: "2608.00001v1", title: "Old assessment", topics: ["Old tag"], schedulerRating: 9, schedulerRatingScale: 10 }
  ] }
];

test("cross-archive papers deduplicate versions using the latest matching review, not the highest score", () => {
  const before = JSON.stringify(catalogue);
  const entries = archiveUi.collectPapers(catalogue);
  assert.equal(entries.length, 4);
  assert.equal(entries[0].rating, 6);
  assert.equal(entries[0].paper.title, "New assessment");
  assert.deepEqual(entries[0].reviews.map((review) => review.editionId), ["weekly", "old"]);
  assert.deepEqual(entries[0].reviews.map((review) => review.rating), [6, 9]);
  assert.equal(JSON.stringify(catalogue), before, "stored reviews are never re-scored or mutated");
});

test("kind and date restrictions are applied before choosing score, tags and provenance", () => {
  const daily = archiveUi.collectPapers(catalogue, { kind: "daily" });
  assert.equal(daily.find((entry) => entry.paper.arxivId === "2608.00001v1").rating, 9);
  const old = archiveUi.collectPapers(catalogue, { to: "2026-08-31" });
  assert.equal(old.length, 1);
  assert.equal(old[0].paper.title, "Old assessment");
  assert.equal(archiveUi.collectPapers(catalogue, { from: "2026-09-04", to: "2026-09-04", kind: "weekly" }).length, 1);
});

test("rating, tag and query filters combine across the entire archive", () => {
  const entries = archiveUi.collectPapers(catalogue);
  assert.deepEqual(archiveUi.filterPapers(entries, { minRating: "8" }).map((entry) => entry.rating), [9.5]);
  assert.equal(archiveUi.filterPapers(entries, { minRating: "6", tag: " RATES ", query: "smith" }).length, 1);
  assert.equal(archiveUi.filterPapers(entries, { tag: "rates", minRating: "7" }).length, 0);
  assert.equal(archiveUi.filterPapers(entries, { query: "2608.00002" }).length, 1);
  assert.deepEqual(archiveUi.filterPapers(entries).map((entry) => entry.rating), [9.5, 6, 0, null]);
  assert.equal(archiveUi.filterPapers(entries, { sort: "date" })[0].paper.title, "New assessment");
  assert.equal(archiveUi.filterPapers(entries, { minRating: "0" }).length, 3, "unrated is not zero");
  assert.equal(archiveUi.paperTags(entries).find((tag) => tag.key === "rates").count, 1);
});

test("rating validation does not infer a score or scale", () => {
  for (const [value, scale] of [[null, 10], [4, null], ["4", 5], [-1, 10], [11, 10], [Infinity, 10], [1, 0]]) {
    assert.equal(archiveUi.ratingOutOfTen({ schedulerRating: value, schedulerRatingScale: scale }), null);
  }
  assert.equal(archiveUi.ratingOutOfTen({ schedulerRating: 4, schedulerRatingScale: 5 }), 8);
  assert.equal(archiveUi.normaliseMinRating("8"), "8");
  assert.equal(archiveUi.normaliseMinRating("0"), "0");
  for (const value of [null, "", "-1", "11", "bogus"]) assert.equal(archiveUi.normaliseMinRating(value), "");
});
