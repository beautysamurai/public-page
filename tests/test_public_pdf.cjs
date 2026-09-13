const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs"), path = require("node:path");
const { JSDOM } = require("jsdom");
const site = path.join(__dirname, "../site");
const html = fs.readFileSync(path.join(site, "theory/index.html"), "utf8");
const metadata = JSON.parse(fs.readFileSync(path.join(site, "documents/decision_theory_rates_e_trading.json"), "utf8"));

test("textbook is reachable without JavaScript, with download and durable upload dates", () => {
  const dom = new JSDOM(html, { url: "https://example.org/public-page/theory/" });
  try {
    const card = dom.window.document.getElementById("decision-theory");
    assert.ok(card);
    assert.ok(card.querySelector('a[download]').href.endsWith(metadata.file));
    assert.ok(card.querySelector('a[target="_blank"]').rel.includes("noopener"));
    assert.equal(card.querySelectorAll(".book-contents li").length, 4);
    for (const element of card.querySelectorAll("[data-book-date]")) {
      assert.equal(element.dateTime, metadata[element.dataset.bookDate].slice(0, 10));
    }
    assert.match(card.textContent, new RegExp(metadata.pageCount + "ページ"));
    assert.match(card.textContent, new RegExp((metadata.bytes / 1000000).toFixed(2) + " MB"));
    assert.match(fs.readFileSync(path.join(site, "index.html"), "utf8"), /href="\.\/theory\/#decision-theory"/);
  } finally { dom.window.close(); }
});

test("textbook description is bilingual and never adds language queries to PDF downloads", () => {
  for (const language of ["ja", "en"]) {
    const dom = new JSDOM(html, { url: "https://example.org/public-page/theory/?lang=" + language, runScripts: "outside-only" });
    try {
      for (const script of ["i18n.js", "static-page.js"]) dom.window.eval(fs.readFileSync(path.join(site, script), "utf8"));
      const card = dom.window.document.getElementById("decision-theory");
      assert.match(card.textContent, language === "ja" ? /最終アップロード日/ : /Last uploaded/);
      assert.match(card.textContent, language === "ja" ? /ノートブック・読書メモは未公開/ : /notebooks and reading notes are not hosted/);
      assert.ok(!card.querySelector('a[download]').href.includes("?"));
      assert.match(card.textContent, /239/);
      assert.ok(!card.textContent.includes("book."));
    } finally { dom.window.close(); }
  }
});
