import { readFileSync, writeFileSync } from "node:fs";
import path from "node:path";

const read = file => JSON.parse(readFileSync(file, "utf8"));
const write = (file, value) => writeFileSync(file, JSON.stringify(value) + "\n");
const dayMs = 86400000;
const iso = time => new Date(time).toISOString().slice(0, 10);

// Only public archive records are passed in. Derive coverage from the observed
// batch date: a historical newsletter can be dated the day after the batch.
export function collectPeriod(kind, end, daily, holidays) {
  const start = kind === "weekly" ? iso(Date.parse(end) - 6 * dayMs) : end.slice(0, 8) + "01";
  const covered = [], missing = [], papers = new Map();
  for (let time = Date.parse(start); time <= Date.parse(end); time += dayMs) {
    const day = iso(time);
    const weekday = new Date(time).getUTCDay();
    if (!weekday || weekday === 6 || holidays.has(iso(time - dayMs))) continue;
    const sources = daily.get(day);
    if (!sources?.length) {
      missing.push(day);
      continue;
    }
    covered.push(day);
    for (const source of sources) {
      source.papers.forEach((paper, i) => {
        const key = paper.arxivId.replace(/v\d+$/i, "").toLowerCase();
        const previous = papers.get(key);
        // Later batch and edition supersede an earlier appearance. Keep its
        // original score, translation and version together.
        if (!previous || source.importedAt >= previous.importedAt) {
          papers.set(key, { paper, translation: source.translations[i], day, importedAt: source.importedAt });
        }
      });
    }
  }
  const score = item => item.paper.schedulerRatingScale > 0 && item.paper.schedulerRating != null
    ? item.paper.schedulerRating / item.paper.schedulerRatingScale : -1;
  const items = [...papers.values()].sort((a, b) => score(b) - score(a) ||
    a.day.localeCompare(b.day) || a.paper.arxivId.localeCompare(b.paper.arxivId));
  return {start, covered, missing, items};
}

function narrative(period, en) {
  const {covered, missing, items} = period;
  const list = days => days.length ? days.join(en ? ", " : "、") : (en ? "None" : "なし");
  const message = en
    ? `Provisional review · ${covered.length} of ${covered.length + missing.length} scheduled batches covered · ${items.length} papers.`
    : `暫定版 · 対象${covered.length + missing.length}日中${covered.length}日を収録 · ${items.length}本`;
  const lines = [message, "", en ? "Confirmed batch dates" : "収録したバッチ日", list(covered), "",
    en ? "Missing batch dates" : "欠落しているバッチ日", list(missing), "",
    en ? "The summaries and ratings come from the confirmed daily editions. The final synthesis will appear here."
      : "以下は確認済みの日次レビューの要約と評価です。正式な週次・月次レビューが完成すると、このページが切り替わります。"];
  for (const [i, {paper, translation, day}] of items.entries()) {
    const rating = paper.schedulerRating != null && paper.schedulerRatingScale > 0
      ? ` · ${paper.schedulerRating}/${paper.schedulerRatingScale}` : "";
    const summary = (en ? translation : paper).schedulerSummary.split(/\n\s*\n/)[0];
    lines.push("", `## ${i + 1}. ${paper.title}`, "", day + rating, "", summary);
  }
  return {message, sourceText: lines.join("\n")};
}

// Materialize previews in the Pages build only. A published final edition owns
// the same ID and path and always takes precedence on the next deployment.
export function addProvisionalReviews(stage, holidays) {
  const data = path.join(stage, "data"), archive = path.join(data, "archive");
  const status = read(path.join(data, "research-status.json"));
  const index = read(path.join(archive, "index.json"));
  const catalogue = read(path.join(data, "papers.json"));
  const english = read(path.join(data, "i18n/en.json"));
  const translations = new Map(english.editions.map(e => [e.editionId, e]));
  const daily = new Map();
  for (const record of index.editions.filter(e => e.kind === "daily" &&
      ["UPDATE_CONFIRMED", "NO_RELEVANT_PAPERS"].includes(e.status))) {
    const source = read(path.join(archive, record.path));
    const day = source.observedBatchDate;
    if (!day || day !== source.expectedBatchDate) continue;
    const overlay = translations.get(record.editionId);
    if (!overlay || overlay.papers.length !== source.papers.length ||
        overlay.papers.some((p, i) => p.arxivId !== source.papers[i].arxivId)) {
      throw new Error("Unaligned daily translation: " + record.editionId);
    }
    if (!daily.has(day)) daily.set(day, []);
    daily.get(day).push({...source, translations: overlay.papers});
  }
  for (const pending of status.pendingPeriods) {
    const {reportKind: kind, periodEnd: end} = pending;
    if (!["weekly", "monthly"].includes(kind) || !/^\d{4}-\d{2}-\d{2}$/.test(end)) {
      throw new Error("Invalid pending period");
    }
    const id = end + "-" + kind + "-openai-01";
    if (index.editions.some(e => e.editionId === id)) continue;
    const period = collectPeriod(kind, end, daily, holidays);
    if (!period.covered.length) continue;
    const ja = narrative(period, false), en = narrative(period, true);
    const papers = period.items.map(({paper}, i) => ({...paper, schedulerRank: i + 1}));
    const edition = {schemaVersion: 2, editionId: id, editionDate: end, editionKind: kind,
      sourceKind: "daily-review-archive", sourceLabel: "Saved daily reviews",
      importedAt: period.covered.flatMap(day => daily.get(day)).map(s => s.importedAt).sort().at(-1),
      periodStart: period.start, periodEnd: end, observedBatchDate: null, expectedBatchDate: null,
      status: "PROVISIONAL_REVIEW", ...ja, papers,
      coveredBatchDates: period.covered, missingBatchDates: period.missing};
    write(path.join(archive, id + ".json"), edition);
    english.editions.push({editionId: id, ...en, papers: period.items.map(item => item.translation)});
    const base = {editionId: id, date: end, periodEnd: end, kind};
    index.editions.push({...base, path: id + ".json", status: edition.status,
      paperCount: papers.length, sourceKind: edition.sourceKind, title: "Provisional " + kind + " review"});
    catalogue.editions.push({...base, papers: papers.map(({arxivId, title, authors, topics, schedulerRating, schedulerRatingScale}) =>
      ({arxivId, title, authors, topics, schedulerRating, schedulerRatingScale}))});
    pending.provisionalEditionId = id;
    pending.missingBatchDates = period.missing;
  }
  index.editions.sort((a, b) => b.date.localeCompare(a.date));
  catalogue.editions.sort((a, b) => b.date.localeCompare(a.date));
  write(path.join(archive, "index.json"), index);
  write(path.join(data, "papers.json"), catalogue);
  write(path.join(data, "i18n/en.json"), english);
  write(path.join(data, "research-status.json"), status);
}
