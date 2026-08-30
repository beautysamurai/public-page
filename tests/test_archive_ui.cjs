"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const archiveUi = require("../site/archive-ui.js");

const reports = [
  { editionId: "d3", date: "2026-08-30", kind: "daily" },
  { editionId: "m1", date: "2026-08-29", kind: "monthly" },
  { editionId: "w1", date: "2026-08-28", kind: "weekly" },
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
