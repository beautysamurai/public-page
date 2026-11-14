const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs"), os = require("node:os"), path = require("node:path");

test("a provisional edition uses observed dates, names gaps and yields its permalink to the final edition", async t => {
  const { addProvisionalReviews } = await import("../scripts/provisional_reviews.mjs");
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "period-preview-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const data = path.join(root, "data");
  const put = (name, obj) => fs.writeFileSync(path.join(data, name), JSON.stringify(obj));
  const get = name => JSON.parse(fs.readFileSync(path.join(data, name)));
  fs.mkdirSync(path.join(data, "archive"), { recursive: true });
  fs.mkdirSync(path.join(data, "i18n"));
  const periodEnd = "2026-10-16", kind = "weekly", id = periodEnd + "-weekly-openai-01";
  const paper = { arxivId: "2610.01234v1", title: "Paper", topics: ["Rates"], authors: ["Author"],
    schedulerSummary: "日次の要約", schedulerRating: 4, schedulerRatingScale: 5, ratings: [] };
  put("archive/daily.json", { editionId: "daily", observedBatchDate: "2026-10-12",
    expectedBatchDate: "2026-10-12", editionDate: "2026-10-13", importedAt: "2026-10-13T10:00:00Z", papers: [paper] });
  put("archive/index.json", { editions: [{kind: "daily", date: "2026-10-13", status: "UPDATE_CONFIRMED", editionId: "daily", path: "daily.json"}] });
  put("papers.json", { editions: [] });
  put("i18n/en.json", { editions: [{ editionId: "daily", papers: [{ ...paper, schedulerSummary: "Daily summary" }] }] });
  put("research-status.json", { pendingPeriods: [{reportKind: kind, periodEnd}] });
  const holidays = new Set(["2026-10-12"]);
  addProvisionalReviews(root, holidays);
  const edition = get("archive/" + id + ".json");
  assert.deepEqual(edition.coveredBatchDates, ["2026-10-12"]);
  assert.deepEqual(edition.missingBatchDates, ["2026-10-14", "2026-10-15", "2026-10-16"]);
  assert.equal(edition.status, "PROVISIONAL_REVIEW");
  assert.match(edition.sourceText, /4\/5/);
  assert.match(get("i18n/en.json").editions.at(-1).sourceText, /Daily summary/);
  assert.equal(get("research-status.json").pendingPeriods[0].provisionalEditionId, id);
  put("archive/" + id + ".json", {status: "WEEKLY_REVIEW"});
  const index = get("archive/index.json");
  index.editions.find(e => e.editionId === id).status = "WEEKLY_REVIEW";
  put("archive/index.json", index);
  addProvisionalReviews(root, holidays);
  assert.deepEqual(get("archive/" + id + ".json"), {status: "WEEKLY_REVIEW"});
  assert.equal(get("archive/index.json").editions.filter(e => e.editionId === id).length, 1);
});
