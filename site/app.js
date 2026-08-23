(function () {
  "use strict";

  var byId = function (id) { return document.getElementById(id); };
  var elements = {
    archiveList: byId("archive-list"),
    archiveMore: byId("archive-more"),
    digestTitle: byId("digest-title"),
    editionDate: byId("edition-date"),
    filters: byId("topic-filters"),
    freshnessShort: byId("freshness-short"),
    headerStatus: byId("header-status"),
    lensVisual: byId("lens-visual"),
    loading: byId("loading-state"),
    noResults: byId("no-results"),
    notice: byId("notice-state"),
    noticeMessage: byId("notice-message"),
    noticeTitle: byId("notice-title"),
    panelDate: byId("panel-date"),
    paperCount: byId("paper-count"),
    paperList: byId("paper-list"),
    search: byId("paper-search"),
    toolbar: byId("digest-toolbar"),
    topicCount: byId("topic-count"),
    updateNote: byId("update-note")
  };

  var statusCopy = {
    UPDATE_CONFIRMED: { label: "Update confirmed", state: "fresh", emptyTitle: "The latest batch is confirmed" },
    NO_RELEVANT_PAPERS: { label: "Screen clear", state: "fresh", emptyTitle: "No papers met today’s screen" },
    UPDATE_NOT_CONFIRMED: { label: "Awaiting batch", state: "warn", emptyTitle: "The expected batch is not confirmed" },
    UPDATER_OFFLINE: { label: "Updater offline", state: "offline", emptyTitle: "The updater is offline" }
  };

  var state = {
    archiveDate: new URLSearchParams(window.location.search).get("edition"),
    archiveVisible: 6,
    filter: "all",
    query: "",
    report: null,
    reports: []
  };

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

  function validArxivId(value) {
    var id = clean(value);
    return /^(?:\d{4}\.\d{4,5}(?:v\d+)?|[a-z][a-z0-9.-]*\/\d{7}(?:v\d+)?)$/i.test(id) ? id : "";
  }

  function idFromArxivUrl(value) {
    try {
      var url = new URL(value);
      if (url.protocol !== "https:" || url.hostname !== "arxiv.org" || url.port) return "";
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

  function normalisePaper(raw, index) {
    raw = raw && typeof raw === "object" ? raw : {};
    var arxivId = validArxivId(raw.arxivId) || idFromArxivUrl(raw.absUrl) || idFromArxivUrl(raw.pdfUrl);
    var score = Number(raw.score);
    return {
      abstract: clean(raw.abstract),
      absUrl: canonicalArxivUrl(arxivId, "abs"),
      arxivId: arxivId,
      authors: stringList(raw.authors),
      index: index,
      pdfUrl: canonicalArxivUrl(arxivId, "pdf"),
      score: Number.isFinite(score) ? score : null,
      scoreReasons: stringList(raw.scoreReasons),
      submittedDate: clean(raw.submittedDate),
      title: clean(raw.title, "Untitled arXiv record"),
      topics: stringList(raw.topics),
      updatedDate: clean(raw.updatedDate)
    };
  }

  function normaliseReport(raw) {
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
      throw new Error("The edition data is not a JSON object.");
    }
    var papers = Array.isArray(raw.papers) ? raw.papers.map(normalisePaper) : [];
    return {
      checkedAt: clean(raw.checkedAt),
      expectedBatchDate: clean(raw.expectedBatchDate),
      generatedAt: clean(raw.generatedAt),
      observedBatchDate: clean(raw.observedBatchDate),
      papers: papers,
      schemaVersion: Number(raw.schemaVersion) || 1,
      status: clean(raw.status, "UPDATE_NOT_CONFIRMED").toUpperCase(),
      statusMessage: clean(raw.statusMessage)
    };
  }

  function normaliseArchive(raw) {
    if (!raw || typeof raw !== "object" || !Array.isArray(raw.reports)) return [];
    return raw.reports.map(function (item) {
      item = item && typeof item === "object" ? item : {};
      var date = isDate(item.date) ? item.date : "";
      var expectedFile = date ? date + ".json" : "";
      var path = clean(item.path);
      if (!/^\d{4}-\d{2}-\d{2}\.json$/.test(path)) path = expectedFile;
      return {
        date: date,
        paperCount: Math.max(0, Number(item.paperCount) || 0),
        path: path,
        status: clean(item.status, "UPDATE_NOT_CONFIRMED").toUpperCase()
      };
    }).filter(function (item) {
      return item.date && item.path;
    }).sort(function (a, b) {
      return b.date.localeCompare(a.date);
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

  function archiveDataUrl(item) {
    if (!item || !/^\d{4}-\d{2}-\d{2}\.json$/.test(item.path)) return "";
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
    if (!date) return "Date unavailable";
    return new Intl.DateTimeFormat("en", compact
      ? { day: "2-digit", month: "short", year: "numeric", timeZone: "UTC" }
      : { day: "numeric", month: "long", year: "numeric", timeZone: "UTC" }
    ).format(date);
  }

  function relativeTime(value) {
    var date = dateObject(value);
    if (!date) return "check time unavailable";
    var minutes = Math.max(0, Math.round((Date.now() - date.getTime()) / 60000));
    if (minutes < 2) return "checked just now";
    if (minutes < 60) return "checked " + minutes + " min ago";
    var hours = Math.round(minutes / 60);
    if (hours < 48) return "checked " + hours + " hr ago";
    var days = Math.round(hours / 24);
    return "checked " + days + " day" + (days === 1 ? "" : "s") + " ago";
  }

  function editionFreshness(report, archived) {
    if (archived) {
      return { label: "Archived edition", short: "archive", state: "archived" };
    }

    var copy = statusCopy[report.status] || { label: "Status unknown", state: "warn" };
    if (copy.state === "offline") return { label: copy.label, short: "offline", state: "offline" };
    if (report.status === "UPDATE_NOT_CONFIRMED") return { label: copy.label, short: "waiting", state: "warn" };

    var checked = dateObject(report.checkedAt || report.generatedAt);
    if (!checked) return { label: copy.label + " · time unknown", short: "unknown", state: "warn" };
    var ageHours = Math.max(0, (Date.now() - checked.getTime()) / 3600000);
    if (ageHours > 72) return { label: "Stale edition", short: "stale", state: "stale" };
    if (ageHours > 36) return { label: "Edition delayed", short: "delayed", state: "warn" };
    return { label: copy.label, short: "fresh", state: "fresh" };
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

  function renderStatus(report, archived) {
    var freshness = editionFreshness(report, archived);
    var checked = archived ? "dated snapshot" : relativeTime(report.checkedAt || report.generatedAt);
    elements.headerStatus.dataset.state = freshness.state;
    elements.headerStatus.lastElementChild.textContent = freshness.label + " · " + checked;
    elements.freshnessShort.textContent = freshness.short;
    elements.updateNote.dataset.state = freshness.state;

    var message = report.statusMessage || freshness.label + ".";
    elements.updateNote.textContent = message + (archived ? "" : " " + checked.charAt(0).toUpperCase() + checked.slice(1) + ".");
    return freshness;
  }
  function renderFilters(papers) {
    var counts = topicCounts(papers);
    if (state.filter !== "all" && !counts.some(function (entry) { return entry[0] === state.filter; })) {
      state.filter = "all";
    }

    var fragment = document.createDocumentFragment();
    [["all", papers.length]].concat(counts).forEach(function (entry) {
      var button = createNode("button", "topic-filter", entry[0] === "all" ? "All · " + entry[1] : entry[0] + " · " + entry[1]);
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
      paper.scoreReasons.join(" ")
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
    link.setAttribute("aria-label", ariaLabel + " (opens on arXiv in a new tab)");
    link.appendChild(document.createTextNode(label + " "));
    link.appendChild(createNode("span", "external-arrow", "↗"));
    link.lastElementChild.setAttribute("aria-hidden", "true");
    return link;
  }

  function renderPaper(paper, visibleIndex) {
    var item = createNode("li", "paper-card");
    var index = createNode("span", "paper-index", String(visibleIndex + 1).padStart(2, "0"));
    index.setAttribute("aria-hidden", "true");
    item.appendChild(index);

    var main = createNode("article", "paper-main");
    var top = createNode("div", "paper-topline");
    var dateParts = [];
    if (paper.submittedDate) dateParts.push("Submitted " + formatDate(paper.submittedDate, true));
    if (paper.updatedDate && paper.updatedDate !== paper.submittedDate) dateParts.push("Updated " + formatDate(paper.updatedDate, true));
    if (paper.arxivId) dateParts.push("arXiv:" + paper.arxivId);
    top.appendChild(createNode("span", "paper-date", dateParts.join(" · ") || "arXiv record"));

    if (paper.score !== null) {
      top.appendChild(createNode("span", "score-pill", "Relevance · " + displayScore(paper.score)));
    }
    main.appendChild(top);

    var heading = createNode("h3", "paper-title");
    if (paper.absUrl) {
      var titleLink = externalLink(paper.absUrl, "paper-title-link", paper.title, "Open “" + paper.title + "”");
      heading.appendChild(titleLink);
    } else {
      heading.textContent = paper.title;
    }
    main.appendChild(heading);
    main.appendChild(createNode("p", "paper-authors", paper.authors.length ? paper.authors.join(", ") : "Authors not listed"));

    if (paper.topics.length) {
      var topicWrap = createNode("div", "paper-topics");
      paper.topics.forEach(function (topic) { topicWrap.appendChild(createNode("span", "topic-tag", topic)); });
      main.appendChild(topicWrap);
    }

    if (paper.abstract) {
      main.appendChild(createNode("p", "paper-abstract", abstractPreview(paper.abstract)));
      if (paper.abstract.length > 360) {
        var details = createNode("details", "abstract-details");
        details.appendChild(createNode("summary", "", "Read full abstract"));
        details.appendChild(createNode("p", "", paper.abstract));
        main.appendChild(details);
      }
    }

    if (paper.score !== null || paper.scoreReasons.length) {
      var relevance = createNode("div", "relevance-block");
      var relevanceHead = createNode("div", "relevance-head");
      relevanceHead.appendChild(createNode("span", "", paper.scoreReasons.length ? "Selection evidence" : "Relevance score"));
      if (paper.score !== null) {
        var track = createNode("span", "score-track");
        track.setAttribute("role", "meter");
        track.setAttribute("aria-label", "Relevance score " + displayScore(paper.score));
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
    var abstractLink = externalLink(paper.absUrl, "arxiv-link", "Abstract on arXiv", "Open abstract for “" + paper.title + "”");
    var pdfLink = externalLink(paper.pdfUrl, "pdf-link", "PDF on arXiv", "Open PDF for “" + paper.title + "”");
    if (abstractLink) actions.appendChild(abstractLink);
    if (pdfLink) actions.appendChild(pdfLink);
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
      elements.lensVisual.replaceChildren(createNode("p", "empty-lens", "No topic mix is available for this edition."));
      return;
    }
    var maximum = Math.max.apply(null, counts.map(function (entry) { return entry[1]; }));
    var fragment = document.createDocumentFragment();
    counts.forEach(function (entry) {
      var row = createNode("div", "lens-row");
      row.appendChild(createNode("span", "lens-name", entry[0]));
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

    var edition = report.observedBatchDate || report.expectedBatchDate || state.archiveDate || report.checkedAt || report.generatedAt;
    var displayDate = formatDate(edition, false);
    elements.editionDate.textContent = (archived ? "Archive · " : "Latest · ") + displayDate;
    elements.panelDate.textContent = formatDate(edition, true);
    elements.paperCount.textContent = report.papers.length;
    elements.topicCount.textContent = topicCounts(report.papers).length;
    elements.digestTitle.textContent = archived ? "Research brief · " + displayDate : "Today’s research brief";
    document.title = archived ? displayDate + " — Rates & Execution arXiv Daily" : "Rates & Execution — arXiv Daily";
    var freshness = renderStatus(report, archived);

    elements.paperList.replaceChildren();
    if (report.papers.length) {
      clearNotice();
      elements.toolbar.hidden = false;
      renderFilters(report.papers);
      renderPapers();
    } else {
      elements.toolbar.hidden = true;
      elements.noResults.hidden = true;
      var copy = statusCopy[report.status] || { emptyTitle: "No papers are available for this edition" };
      var message = report.statusMessage || "No selected papers were published in this dated screen.";
      showNotice(copy.emptyTitle, message, freshness.state);
    }
    renderLens(report.papers);
  }
  function archiveHref(date) {
    var url = new URL(window.location.href);
    url.searchParams.set("edition", date);
    url.hash = "digest";
    return url.href;
  }

  function archiveState(status) {
    var copy = statusCopy[status] || { label: "Status unknown", state: "warn" };
    return { label: copy.label, state: copy.state };
  }

  function renderArchive() {
    if (!state.reports.length) {
      elements.archiveList.replaceChildren(createNode("p", "archive-empty", "No archived editions have been published yet."));
      elements.archiveMore.hidden = true;
      return;
    }

    var fragment = document.createDocumentFragment();
    state.reports.slice(0, state.archiveVisible).forEach(function (report) {
      var link = createNode("a", "archive-row");
      link.href = archiveHref(report.date);
      if (state.archiveDate === report.date) link.setAttribute("aria-current", "page");
      link.appendChild(createNode("span", "archive-date", formatDate(report.date, true)));

      var meta = createNode("span", "archive-meta");
      var visual = archiveState(report.status);
      var status = createNode("span", "archive-state", visual.label);
      status.dataset.state = visual.state;
      meta.appendChild(status);
      meta.appendChild(createNode("span", "", report.paperCount + " paper" + (report.paperCount === 1 ? "" : "s")));
      link.appendChild(meta);

      var arrow = createNode("span", "archive-arrow", "→");
      arrow.setAttribute("aria-hidden", "true");
      link.appendChild(arrow);
      fragment.appendChild(link);
    });

    elements.archiveList.replaceChildren(fragment);
    elements.archiveMore.hidden = state.archiveVisible >= state.reports.length;
  }

  async function loadArchiveIndex() {
    try {
      var raw = await fetchJson(new URL("./data/archive/index.json", document.baseURI));
      state.reports = normaliseArchive(raw);
      renderArchive();
      return state.reports;
    } catch (_error) {
      state.reports = [];
      elements.archiveList.replaceChildren(createNode("p", "archive-empty", "The archive index is not available right now."));
      elements.archiveMore.hidden = true;
      return [];
    }
  }

  function showLoadFailure(archived) {
    elements.loading.hidden = true;
    elements.toolbar.hidden = true;
    elements.paperList.replaceChildren();
    elements.paperCount.textContent = "0";
    elements.topicCount.textContent = "0";
    elements.freshnessShort.textContent = "offline";
    elements.headerStatus.dataset.state = "offline";
    elements.headerStatus.lastElementChild.textContent = "Edition unavailable";
    elements.updateNote.dataset.state = "offline";
    elements.updateNote.textContent = archived ? "This archived edition could not be loaded." : "The latest edition could not be loaded.";
    showNotice(
      archived ? "Archived edition unavailable" : "Latest edition unavailable",
      "The edition JSON could not be loaded or validated. The archive below may still be available.",
      "offline"
    );
    renderLens([]);
  }

  async function initialise() {
    elements.search.addEventListener("input", function () {
      state.query = elements.search.value.trim().toLocaleLowerCase();
      renderPapers();
    });

    elements.archiveMore.addEventListener("click", function () {
      state.archiveVisible += 6;
      renderArchive();
    });

    var archivePromise = loadArchiveIndex();
    var archived = false;
    var reportPromise;

    if (state.archiveDate) {
      archived = true;
      if (!isDate(state.archiveDate)) {
        await archivePromise;
        showLoadFailure(true);
        return;
      }

      var reports = await archivePromise;
      var selected = reports.find(function (item) { return item.date === state.archiveDate; });
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
      renderReport(normaliseReport(raw), archived);
    } catch (_error) {
      showLoadFailure(archived);
    }
  }

  initialise();
})();
