from __future__ import annotations

import json
import re
import unittest
from pathlib import Path
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
HISTORY_PATH = ROOT / "content" / "chatgpt_scheduler_history.json"
TRANSLATIONS_PATH = ROOT / "site" / "data" / "i18n" / "en.json"
INDEX_PATH = ROOT / "site" / "index.html"
CATALOG_PATH = ROOT / "site" / "i18n.js"
APP_PATH = ROOT / "site" / "app.js"
ARCHIVE_UI_PATH = ROOT / "site" / "archive-ui.js"
STATIC_PAGE_APP_PATH = ROOT / "site" / "static-page.js"
STYLES_PATH = ROOT / "site" / "styles.css"
THEORY_INDEX_PATH = ROOT / "site" / "theory" / "index.html"
HJB_INDEX_PATH = ROOT / "site" / "theory" / "hjb" / "index.html"
BS_INDEX_PATH = ROOT / "site" / "theory" / "black-scholes" / "index.html"
SABR_INDEX_PATH = ROOT / "site" / "theory" / "sabr" / "index.html"
ZABR_INDEX_PATH = ROOT / "site" / "theory" / "zabr" / "index.html"
SIMULATOR_PATHS = (BS_INDEX_PATH, SABR_INDEX_PATH, ZABR_INDEX_PATH, HJB_INDEX_PATH)

TOP_LEVEL_FIELDS = {"schemaVersion", "language", "editions"}
EDITION_FIELDS = {"editionId", "message", "sourceText", "papers"}
PAPER_FIELDS = {
    "arxivId",
    "schedulerLabel",
    "schedulerSummary",
    "ratings",
}
RATING_FIELDS = {"label"}
CJK = re.compile(r"[\u3040-\u30ff\u3400-\u9fff]")
PRIVATE_OR_UNSAFE = re.compile(
    r"(?:turn\d+(?:search|fetch|view|open)\d+|"
    r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}|"
    r"(?:[A-Za-z]:\\|/home/|\\\\wsl)|"
    r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,})",
    re.IGNORECASE,
)


def load_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def catalog_keys(source: str) -> tuple[set[str], set[str]]:
    ja_block, remainder = source.split("    en: {", 1)
    ja_block = ja_block.split("    ja: {", 1)[1].rsplit("    },", 1)[0]
    en_block = remainder.split("\n    }\n  };", 1)[0]
    pattern = re.compile(r'^\s+"([A-Za-z0-9_.]+)":', re.MULTILINE)
    return set(pattern.findall(ja_block)), set(pattern.findall(en_block))


class SchedulerTranslationCoverageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.history = load_json(HISTORY_PATH)
        cls.translations = load_json(TRANSLATIONS_PATH)

    def test_manifest_has_exact_safe_schema(self):
        self.assertEqual(set(self.translations), TOP_LEVEL_FIELDS)
        self.assertEqual(self.translations["schemaVersion"], 1)
        self.assertEqual(self.translations["language"], "en")

        for edition in self.translations["editions"]:
            self.assertEqual(set(edition), EDITION_FIELDS)
            self.assertTrue(edition["message"].strip())
            self.assertTrue(edition["sourceText"].strip())
            for paper in edition["papers"]:
                self.assertEqual(set(paper), PAPER_FIELDS)
                self.assertTrue(paper["schedulerLabel"].strip())
                self.assertTrue(paper["schedulerSummary"].strip())
                for rating in paper["ratings"]:
                    self.assertEqual(set(rating), RATING_FIELDS)
                    self.assertTrue(rating["label"].strip())

    def test_every_edition_paper_and_rating_is_translated_in_source_order(self):
        source_editions = self.history["editions"]
        translated_editions = self.translations["editions"]
        self.assertEqual(
            [item["editionId"] for item in translated_editions],
            [item["editionId"] for item in source_editions],
        )

        paper_count = 0
        rating_count = 0
        for source, translated in zip(source_editions, translated_editions, strict=True):
            self.assertEqual(
                [item["arxivId"] for item in translated["papers"]],
                [item["arxivId"] for item in source["papers"]],
                source["editionId"],
            )
            for source_paper, translated_paper in zip(
                source["papers"], translated["papers"], strict=True
            ):
                self.assertEqual(
                    len(translated_paper["ratings"]),
                    len(source_paper["ratings"]),
                )
                paper_count += 1
                rating_count += len(translated_paper["ratings"])

        expected_paper_count = sum(
            len(edition["papers"]) for edition in source_editions
        )
        expected_rating_count = sum(
            len(paper["ratings"])
            for edition in source_editions
            for paper in edition["papers"]
        )
        self.assertEqual(paper_count, expected_paper_count)
        self.assertEqual(rating_count, expected_rating_count)

    def test_english_editorial_copy_has_no_japanese_or_private_state(self):
        for edition in self.translations["editions"]:
            values = [edition["message"], edition["sourceText"]]
            for paper in edition["papers"]:
                values.extend(
                    [paper["schedulerLabel"], paper["schedulerSummary"]]
                )
                values.extend(rating["label"] for rating in paper["ratings"])
            combined = "\n".join(values)
            self.assertIsNone(CJK.search(combined), edition["editionId"])
            self.assertIsNone(PRIVATE_OR_UNSAFE.search(combined), edition["editionId"])
            self.assertNotIn("<script", combined.casefold())
            self.assertNotIn("javascript:", combined.casefold())

    def test_overlay_cannot_replace_authoritative_paper_fields(self):
        forbidden = {
            "title",
            "authors",
            "absUrl",
            "pdfUrl",
            "submittedDate",
            "updatedDate",
            "schedulerRank",
            "schedulerRating",
            "schedulerRatingScale",
            "value",
            "scale",
            "status",
        }
        serialized = json.dumps(self.translations, ensure_ascii=False)
        for field in forbidden:
            self.assertNotIn(f'"{field}":', serialized)

    def test_weekly_review_has_one_linkable_label_per_paper_in_both_languages(self):
        source = next(
            edition
            for edition in self.history["editions"]
            if edition["editionKind"] == "weekly"
        )
        translated = next(
            edition
            for edition in self.translations["editions"]
            if edition["editionId"] == source["editionId"]
        )

        for label, narrative in (
            ("Japanese", source["sourceText"]),
            ("English", translated["sourceText"]),
        ):
            numbered = re.findall(
                r"^##\s+\d+[.)]\s+(.+)$", narrative, re.MULTILINE
            )
            other_section = narrative.split("## Other reviewed papers", 1)[1]
            other_section = other_section.split("## Weekly conclusion", 1)[0]
            other = re.findall(
                r"^\*\*(.+?)\s+—\s+.+\*\*$", other_section, re.MULTILINE
            )
            self.assertEqual(len(numbered + other), len(source["papers"]), label)


class InterfaceLocaleContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index = INDEX_PATH.read_text(encoding="utf-8")
        cls.catalog = CATALOG_PATH.read_text(encoding="utf-8")
        cls.app = APP_PATH.read_text(encoding="utf-8")
        cls.archive_ui = ARCHIVE_UI_PATH.read_text(encoding="utf-8")
        cls.static_page_app = STATIC_PAGE_APP_PATH.read_text(encoding="utf-8")
        cls.theory_index = THEORY_INDEX_PATH.read_text(encoding="utf-8")
        cls.hjb_index = HJB_INDEX_PATH.read_text(encoding="utf-8")
        cls.ja_keys, cls.en_keys = catalog_keys(cls.catalog)

    def test_japanese_is_the_static_and_url_default(self):
        self.assertIn('<html lang="ja">', self.index)
        self.assertIn('data-language="ja" aria-pressed="true" lang="ja"', self.index)
        self.assertIn('data-language="en" aria-pressed="false" lang="en"', self.index)
        self.assertIn('return value === "en" ? "en" : "ja";', self.app)

    def test_interface_catalogs_have_identical_complete_keys(self):
        self.assertEqual(self.ja_keys, self.en_keys)
        self.assertGreater(len(self.ja_keys), 100)
        referenced = set()
        simulator_sources = [path.read_text(encoding="utf-8") for path in SIMULATOR_PATHS]
        for source in (self.index, self.theory_index, *simulator_sources):
            referenced.update(
                re.findall(
                    r'data-i18n(?:-placeholder|-aria-label|-rich)?="([A-Za-z0-9_.]+)"',
                    source,
                )
            )
            referenced.update(
                re.findall(
                    r'data-(?:page-title|description)-key="([A-Za-z0-9_.]+)"',
                    source,
                )
            )
        referenced.update(
            key
            for key in re.findall(r'\bt\("([A-Za-z0-9_.]+)"', self.app)
            if not key.endswith(".")
        )
        self.assertFalse(referenced - self.ja_keys, referenced - self.ja_keys)

    def test_language_toggle_and_archive_links_preserve_url_state(self):
        self.assertIn('initialParams.get("lang")', self.app)
        self.assertGreaterEqual(
            self.app.count('url.searchParams.set("lang", "en")'), 2
        )
        self.assertGreaterEqual(
            self.app.count('url.searchParams.delete("lang")'), 2
        )
        self.assertIn('url.searchParams.set("edition", editionId)', self.app)
        self.assertIn('window.location.assign(languageUrl(language).href)', self.app)
        self.assertIn('url.hash = "digest"', self.app)
        self.assertIn('data-i18n="nav.theory" data-preserve-language', self.index)
        self.assertIn(
            'document.querySelectorAll("[data-preserve-language]")', self.app
        )

    def test_archive_has_kind_date_and_url_backed_filters(self):
        self.assertLess(
            self.index.index('<script src="./archive-ui.js" defer>'),
            self.index.index('<script src="./app.js" defer>'),
        )
        self.assertLess(
            self.index.index('<script src="./tex-math.js" defer>'),
            self.index.index('<script src="./app.js" defer>'),
        )
        for kind in ("all", "daily", "weekly", "monthly"):
            self.assertIn(f'data-archive-kind="{kind}"', self.index)
            self.assertIn(f'data-archive-count="{kind}"', self.index)
        self.assertIn('id="archive-from" type="date"', self.index)
        self.assertIn('id="archive-to" type="date"', self.index)
        self.assertIn('id="archive-clear"', self.index)
        self.assertIn(
            'id="archive-results" role="status" aria-live="polite"', self.index
        )
        self.assertIn('id="archive-list" role="list"', self.index)
        self.assertNotIn('id="archive-list" aria-live=', self.index)
        self.assertIn('initialParams.get("archive-kind")', self.app)
        self.assertIn('initialParams.get("archive-from")', self.app)
        self.assertIn('initialParams.get("archive-to")', self.app)

    def test_shared_archive_link_scrolls_after_async_content_has_settled(self):
        self.assertIn('var initialHash = window.location.hash;', self.app)
        wait = self.app.index('await Promise.all([archivePromise, cataloguePromise]);')
        scroll = self.app.index('section.scrollIntoView({ block: "start", behavior: "instant" })')
        self.assertGreater(scroll, wait)
        self.assertGreater(wait, self.app.rindex('renderReport(report, archived)'))
        self.assertIn('window.location.hash === initialHash', self.app)

    def test_research_tex_uses_safe_native_mathml(self):
        styles = STYLES_PATH.read_text(encoding="utf-8")
        self.assertIn("window.RatesTexMath.render", self.app)
        self.assertIn("documentRef.createElementNS", (ROOT / "site" / "tex-math.js").read_text(encoding="utf-8"))
        self.assertNotIn("innerHTML", (ROOT / "site" / "tex-math.js").read_text(encoding="utf-8"))
        for class_name in ("source-inline-math", "source-block-math"):
            rules = re.findall(
                rf"\.{class_name}\s*\{{(?P<body>[^}}]*)\}}",
                styles,
            )
            self.assertTrue(rules)
            for body in rules:
                self.assertNotRegex(body, r"(?:^|;)\s*display\s*:")
        for key in ("archive-kind", "archive-from", "archive-to"):
            self.assertIn(f'url.searchParams.set("{key}"', self.app)
            self.assertIn(f'url.searchParams.delete("{key}")', self.app)
        self.assertIn("filteredReports.slice(0, state.archiveVisible)", self.app)
        self.assertIn("state.archiveVisible >= filteredReports.length", self.app)
        self.assertIn("Math.min(state.archiveVisible, filteredReports.length)", self.app)
        self.assertIn('t("archive.noMatches")', self.app)
        self.assertIn(
            'periodEnd: isDate(item.periodEnd) ? item.periodEnd : ""',
            self.app,
        )
        self.assertIn("archiveUi.reportFilterDate(report)", self.app)

    def test_paper_cards_offer_safe_web_and_x_search_actions(self):
        self.assertIn('new URL("https://www.google.com/search")', self.archive_ui)
        self.assertIn('new URL("https://x.com/search")', self.archive_ui)
        self.assertIn('url.searchParams.set("q", query)', self.archive_ui)
        self.assertIn('url.searchParams.set("f", "live")', self.archive_ui)
        self.assertIn("archiveUi.webSearchUrl(paper)", self.app)
        self.assertIn("archiveUi.xSearchUrl(paper)", self.app)
        self.assertIn('t("paper.impactLabel")', self.app)
        self.assertIn('link.target = "_blank"', self.app)
        self.assertIn('link.rel = "noopener noreferrer"', self.app)
        self.assertNotIn("innerHTML", self.archive_ui)
        self.assertNotIn("window.open", self.app)

    def test_method_is_concise_and_links_the_public_repository(self):
        method = self.index.split(
            '<section class="method-section"', 1
        )[1].split("</section>", 1)[0]
        self.assertEqual(method.count("<dt "), 3)
        self.assertIn('class="method-intro"', method)
        self.assertIn('class="method-summary"', method)
        self.assertIn(
            'href="https://github.com/beautysamurai/public-page"', method
        )
        self.assertIn('rel="noopener noreferrer"', method)
        self.assertNotIn("method-grid", method)
        self.assertNotIn("method-caveat", method)

    def test_translation_overlay_is_allowlisted_and_loaded_before_render(self):
        self.assertIn('new URL("./data/i18n/en.json", document.baseURI)', self.app)
        self.assertIn("function normaliseTranslations(raw)", self.app)
        self.assertIn("function localiseReport(report)", self.app)
        self.assertIn("translated.ratings.length !== paper.ratings.length", self.app)
        self.assertIn("fallBackToJapanese()", self.app)
        self.assertNotIn("innerHTML", self.app)

    def test_weekly_narrative_is_linked_at_render_time(self):
        self.assertIn("function linkWeeklySourceText(value, papers)", self.app)
        self.assertIn('report.editionKind === "weekly"', self.app)
        self.assertIn("linkWeeklySourceText(report.sourceText, report.papers)", self.app)
        self.assertIn("paper.absUrl", self.app)
        self.assertIn("renderMarkdownLite(sourceText)", self.app)

    def test_monthly_and_responses_api_editions_are_not_downgraded(self):
        self.assertIn('value === "weekly" || value === "monthly"', self.app)
        self.assertIn('report.sourceKind === "openai-responses-api"', self.app)
        self.assertIn('"status.MONTHLY_REVIEW.label"', self.catalog)
        self.assertIn('"kind.monthlyReview"', self.catalog)
        self.assertIn('"source.openai"', self.catalog)

    def test_unranked_weekly_papers_use_stable_display_numbers(self):
        weekly = next(
            edition
            for edition in load_json(HISTORY_PATH)["editions"]
            if edition["editionKind"] == "weekly"
        )
        self.assertEqual(
            [paper["schedulerRank"] for paper in weekly["papers"]],
            [1, 2, 3, None, None],
        )
        self.assertIn(
            "paper.schedulerRank === null ? paper.index + 1 : paper.schedulerRank",
            self.app,
        )
        self.assertNotIn('schemaVersion >= 2 ? "—"', self.app)

    def test_every_published_topic_has_a_japanese_label(self):
        topics = {
            topic
            for edition in load_json(HISTORY_PATH)["editions"]
            for paper in edition["papers"]
            for topic in paper["topics"]
        }
        for topic in topics:
            self.assertIsNotNone(
                re.search(
                    rf'^\s+"{re.escape(topic)}":\s+".+",?$',
                    self.catalog,
                    re.MULTILINE,
                ),
                topic,
            )


class StaticTheoryPageContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.home = INDEX_PATH.read_text(encoding="utf-8")
        cls.theory = THEORY_INDEX_PATH.read_text(encoding="utf-8")
        cls.hjb = HJB_INDEX_PATH.read_text(encoding="utf-8")
        cls.simulators = {
            path.parent.name: path.read_text(encoding="utf-8")
            for path in SIMULATOR_PATHS
        }
        cls.script = STATIC_PAGE_APP_PATH.read_text(encoding="utf-8")
        cls.styles = STYLES_PATH.read_text(encoding="utf-8")

    def test_physical_theory_routes_and_navigation_exist(self):
        self.assertTrue(THEORY_INDEX_PATH.is_file())
        self.assertTrue(HJB_INDEX_PATH.is_file())
        for path in SIMULATOR_PATHS:
            self.assertTrue(path.is_file(), path)
        self.assertIn('href="./theory/"', self.home)
        self.assertIn('href="./hjb/" data-theory-id="hjb"', self.theory)
        for slug in ("black-scholes", "sabr", "zabr", "hjb"):
            self.assertIn(f'href="./{slug}/" data-theory-id="{slug}"', self.theory)
        self.assertIn('href="../" aria-current="page"', self.hjb)
        for source in (self.theory, *self.simulators.values()):
            self.assertIn('aria-current="page"', source)
            self.assertIn('id="main-content"', source)
            self.assertEqual(len(re.findall(r"<h1\b", source)), 1)

    def test_nested_local_assets_and_links_resolve_inside_site(self):
        site_root = (ROOT / "site").resolve()
        for page in (THEORY_INDEX_PATH, *SIMULATOR_PATHS):
            source = page.read_text(encoding="utf-8")
            for raw in re.findall(r'(?:href|src)="([^"]+)"', source):
                parsed = urlsplit(raw)
                if parsed.scheme or parsed.netloc or not parsed.path:
                    continue
                target = (page.parent / parsed.path).resolve()
                self.assertTrue(target.is_relative_to(site_root), (page, raw))
                self.assertTrue(target.exists(), (page, raw))
                if target.is_dir():
                    self.assertTrue((target / "index.html").is_file(), (page, raw))

    def test_static_pages_preserve_deep_link_when_switching_language(self):
        for source in (self.theory, *self.simulators.values()):
            self.assertIn('data-language="ja" aria-pressed="true"', source)
            self.assertIn('data-language="en" aria-pressed="false"', source)
            self.assertIn("data-preserve-language", source)
        self.assertIn("new URLSearchParams(window.location.search)", self.script)
        self.assertIn("new URL(window.location.href)", self.script)
        self.assertIn('url.searchParams.set("lang", "en")', self.script)
        self.assertIn('url.searchParams.delete("lang")', self.script)
        self.assertIn("window.location.assign(languageUrl(nextLanguage).href)", self.script)
        self.assertNotIn("innerHTML", self.script)

    def test_inline_math_prose_is_safe_localized_mathml(self):
        expected_keys = {
            "black-scholes": {
                "bs.derivationStep1Body",
                "bs.derivationStep2Body",
                "bs.derivationStep3Body",
                "bs.derivationStep4Body",
            },
            "sabr": {
                "sabr.derivationStep1Body",
                "sabr.derivationStep2Body",
                "sabr.derivationStep4Body",
            },
            "zabr": {
                "zabr.theoryBody",
                "zabr.approxBody",
                "zabr.derivationStep3Body",
            },
            "hjb": {
                "hjb.calibration2",
                "hjb.derivationStep2Body",
                "hjb.derivationStep3Body",
            },
        }
        rich_pattern = re.compile(
            r'<(?P<tag>p|li)\b(?P<attrs>[^>]*)'
            r'data-i18n-rich="(?P<key>[A-Za-z0-9_.]+)"[^>]*>'
            r'(?P<body>.*?)</(?P=tag)>',
            re.DOTALL,
        )
        token_pattern = re.compile(r"\{\{([a-z0-9-]+)\}\}")

        for slug, source in self.simulators.items():
            with self.subTest(slug=slug):
                rich_blocks = list(rich_pattern.finditer(source))
                self.assertEqual(
                    {match.group("key") for match in rich_blocks},
                    expected_keys[slug],
                )
                for match in rich_blocks:
                    self.assertNotIn('data-i18n="', match.group("attrs"))
                    body = match.group("body")
                    inline_math = re.findall(r"<math [^>]+>", body)
                    self.assertGreaterEqual(len(inline_math), 1)
                    for math in inline_math:
                        self.assertIn('class="inline-math"', math)
                        self.assertIn('display="inline"', math)
                        self.assertIn('data-inline-token="', math)
                        self.assertIn('data-i18n-aria-label="', math)
                        self.assertIn('aria-label="', math)
                        self.assertNotIn('data-i18n="', math)
                        self.assertNotIn('aria-hidden="true"', math)
                    self.assertNotIn("<mfenced", body)

                    html_tokens = re.findall(
                        r'data-inline-token="([a-z0-9-]+)"',
                        body,
                    )
                    translations = re.findall(
                        rf'^\s+"{re.escape(match.group("key"))}":\s+"([^"]*)",?$',
                        CATALOG_PATH.read_text(encoding="utf-8"),
                        re.MULTILINE,
                    )
                    self.assertEqual(len(translations), 2)
                    for translation in translations:
                        self.assertEqual(
                            token_pattern.findall(translation),
                            html_tokens,
                        )

        self.assertIn("function applyRichText(node, value)", self.script)
        self.assertIn("document.createTextNode", self.script)
        self.assertIn("formula.cloneNode(true)", self.script)
        self.assertIn("node.replaceChildren(fragment)", self.script)
        self.assertNotIn("innerHTML", self.script)
        self.assertNotIn("insertAdjacentHTML", self.script)

        inline_rule = re.search(r"\.inline-math\s*\{(?P<body>[^}]*)\}", self.styles)
        self.assertIsNotNone(inline_rule)
        self.assertNotRegex(
            inline_rule.group("body"),
            r"(?:^|;)\s*display\s*:",
        )

    def test_all_model_pages_are_interactive_and_indexable(self):
        for slug, source in self.simulators.items():
            with self.subTest(slug=slug):
                self.assertIn(f'data-simulator="{slug}"', source)
                self.assertIn("data-simulator-form", source)
                self.assertIn("data-model-input", source)
                self.assertIn("aria-live=\"polite\"", source)
                self.assertIn("<canvas", source)
                self.assertIn('src="../../model-math.js"', source)
                self.assertIn('src="../../simulator.js"', source)
                self.assertNotIn('name="robots" content="noindex"', source)
                self.assertIn('class="theory-guide"', source)
                self.assertEqual(source.count('class="theory-guide-block"'), 3)
                self.assertIn('data-i18n="guide.theoryTitle"', source)
                self.assertIn('data-i18n="guide.approxTitle"', source)
                self.assertIn('data-i18n="guide.calibrationTitle"', source)
                self.assertIn('class="theory-references"', source)
                self.assertGreaterEqual(source.count('<math display="block"'), 3)
                self.assertNotIn('<p class="model-formula">', source)
                equations = re.findall(
                    r'<div class="model-equation">(.*?)</div>', source, re.DOTALL
                )
                self.assertGreaterEqual(len(equations), 2)
                self.assertTrue(all("<math " in equation for equation in equations))
                for math in re.findall(r'<math [^>]+>', source):
                    self.assertIn('aria-label="', math)
                for link in re.findall(
                    r'<a href="https://[^"]+"[^>]+>', source
                ):
                    self.assertIn('target="_blank"', link)
                    self.assertIn('rel="noopener noreferrer"', link)

    def test_all_model_pages_have_collapsed_expandable_derivations(self):
        for slug, source in self.simulators.items():
            with self.subTest(slug=slug):
                self.assertEqual(source.count('<details class="derivation-disclosure">'), 1)
                self.assertNotRegex(source, r"<details[^>]*\bopen\b")
                self.assertRegex(
                    source,
                    r'<details class="derivation-disclosure">\s*'
                    r"<summary>[\s\S]*?</summary>\s*"
                    r'<div class="derivation-content">',
                )
                self.assertIn(
                    'class="derivation-summary-closed" '
                    'data-i18n="guide.showDerivation"',
                    source,
                )
                self.assertIn(
                    'class="derivation-summary-open" '
                    'data-i18n="guide.hideDerivation"',
                    source,
                )
                self.assertIn('class="derivation-content"', source)
                self.assertEqual(source.count("derivation-goal"), 1)

                steps = source.split(
                    '<ol class="derivation-steps">', 1
                )[1].split("</ol>", 1)[0]
                self.assertEqual(steps.count("<li>"), 4)
                self.assertEqual(steps.count("</li>"), 4)
                self.assertEqual(steps.count("<h4 "), 4)
                self.assertGreaterEqual(steps.count("derivation-equation"), 4)
                for math in re.findall(r"<math [^>]+>", steps):
                    self.assertIn('data-i18n-aria-label="', math)
                    self.assertIn('aria-label="', math)
                self.assertNotIn("<mfenced", source)
                key_prefix = "bs" if slug == "black-scholes" else slug
                self.assertIn(
                    f'data-i18n="{key_prefix}.derivationTitle"',
                    source,
                )


if __name__ == "__main__":
    unittest.main()
