(function () {
  "use strict";
  var container = document.getElementById("research-update-status");
  if (!container) return;
  fetch(new URL("./data/research-status.json", document.baseURI), { cache: "no-store" })
    .then(function (response) { if (!response.ok) throw new Error("status unavailable"); return response.json(); })
    .then(function (status) {
      if (status.schemaVersion !== 1 || !status.daily || !Array.isArray(status.pendingPeriods)) return;
      function render() {
        var en = document.documentElement.lang === "en";
        var daily = status.daily;
        var datePattern = /^\d{4}-\d{2}-\d{2}$/;
        var lines = [];
        if (datePattern.test(daily.lastCompletedBatchDate)) {
          lines.push((en ? "Latest completed daily batch: " : "日次の最終完了バッチ：") + daily.lastCompletedBatchDate);
        }
        if (["UPDATE_NOT_CONFIRMED", "UPDATER_OFFLINE"].includes(daily.status)) {
          var target = datePattern.test(daily.pendingBatchDate) ? daily.pendingBatchDate : "";
          lines.push(en ? "Daily " + target + ": update not confirmed or processing failed. Pending retry; not zero new papers." : "日次 " + target + "：更新未確認・処理未完了のため再試行待ちです。新着ゼロではありません。");
        }
        var pending = [];
        status.pendingPeriods.slice(0, 20).forEach(function (item) {
          if (!["weekly", "monthly"].includes(item.reportKind) || !datePattern.test(item.periodEnd)) return;
          var kind = en ? item.reportKind : item.reportKind === "weekly" ? "週次" : "月次";
          pending.push(kind + " " + item.periodEnd + (item.ready
            ? (en ? ": generation pending; queued for retry." : "：生成未完了・再試行待ち。")
            : (en ? ": waiting for complete daily coverage or period end." : "：日次データの不足・対象期間の完了待ち。")));
        });
        // Keep an explicitly opened disclosure open when switching language.
        var wasOpen = Boolean(container.querySelector("details[open]"));
        container.replaceChildren();
        lines.forEach(function (line) { var p = document.createElement("p"); p.textContent = line; container.appendChild(p); });
        if (pending.length) {
          var details = document.createElement("details");
          details.open = wasOpen;
          var summary = document.createElement("summary");
          summary.textContent = en ? "Pending reviews: " + pending.length : "未完了のレビュー：" + pending.length + "件";
          details.appendChild(summary);
          pending.forEach(function (line) { var p = document.createElement("p"); p.textContent = line; details.appendChild(p); });
          container.appendChild(details);
        }
        container.hidden = lines.length === 0 && pending.length === 0;
      }
      render();
      new MutationObserver(render).observe(document.documentElement, { attributes: true, attributeFilter: ["lang"] });
    })
    .catch(function () {
      container.hidden = false;
      container.textContent = "更新状況を取得できません。 / Update status is unavailable.";
    });
})();
