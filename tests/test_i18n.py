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
STATIC_PAGE_APP_PATH = ROOT / "site" / "static-page.js"
THEORY_INDEX_PATH = ROOT / "site" / "theory" / "index.html"
HJB_INDEX_PATH = ROOT / "site" / "theory" / "hjb" / "index.html"

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

        self.assertEqual(paper_count, 11)
        self.assertEqual(rating_count, 20)

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
        for source in (self.index, self.theory_index, self.hjb_index):
            referenced.update(
                re.findall(
                    r'data-i18n(?:-placeholder|-aria-label)?="([A-Za-z0-9_.]+)"',
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
        cls.script = STATIC_PAGE_APP_PATH.read_text(encoding="utf-8")

    def test_physical_theory_routes_and_navigation_exist(self):
        self.assertTrue(THEORY_INDEX_PATH.is_file())
        self.assertTrue(HJB_INDEX_PATH.is_file())
        self.assertIn('href="./theory/"', self.home)
        self.assertIn('href="./hjb/" data-theory-id="hjb"', self.theory)
        self.assertIn('href="../" aria-current="page"', self.hjb)
        for source in (self.theory, self.hjb):
            self.assertIn('aria-current="page"', source)
            self.assertIn('id="main-content"', source)
            self.assertEqual(len(re.findall(r"<h1\b", source)), 1)

    def test_nested_local_assets_and_links_resolve_inside_site(self):
        site_root = (ROOT / "site").resolve()
        for page in (THEORY_INDEX_PATH, HJB_INDEX_PATH):
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
        for source in (self.theory, self.hjb):
            self.assertIn('data-language="ja" aria-pressed="true"', source)
            self.assertIn('data-language="en" aria-pressed="false"', source)
            self.assertIn("data-preserve-language", source)
        self.assertIn("new URLSearchParams(window.location.search)", self.script)
        self.assertIn("new URL(window.location.href)", self.script)
        self.assertIn('url.searchParams.set("lang", "en")', self.script)
        self.assertIn('url.searchParams.delete("lang")', self.script)
        self.assertIn("window.location.assign(languageUrl(nextLanguage).href)", self.script)
        self.assertNotIn("innerHTML", self.script)

    def test_hjb_shell_is_prepared_without_article_content(self):
        self.assertIn('data-theory-id="hjb"', self.hjb)
        self.assertIn('data-i18n="hjb.title"', self.hjb)
        self.assertIn('data-i18n="hjb.status"', self.hjb)
        self.assertIn('<meta name="robots" content="noindex">', self.hjb)
        content = re.search(
            r'<div id="theory-content">(.*?)</div>', self.hjb, re.DOTALL
        )
        self.assertIsNotNone(content)
        self.assertFalse(content.group(1).strip())


if __name__ == "__main__":
    unittest.main()
