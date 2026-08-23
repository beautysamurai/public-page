from __future__ import annotations

import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HISTORY_PATH = ROOT / "content" / "chatgpt_scheduler_history.json"
TRANSLATIONS_PATH = ROOT / "site" / "data" / "i18n" / "en.json"
INDEX_PATH = ROOT / "site" / "index.html"
CATALOG_PATH = ROOT / "site" / "i18n.js"
APP_PATH = ROOT / "site" / "app.js"

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
        cls.ja_keys, cls.en_keys = catalog_keys(cls.catalog)

    def test_japanese_is_the_static_and_url_default(self):
        self.assertIn('<html lang="ja">', self.index)
        self.assertIn('data-language="ja" aria-pressed="true" lang="ja"', self.index)
        self.assertIn('data-language="en" aria-pressed="false" lang="en"', self.index)
        self.assertIn('return value === "en" ? "en" : "ja";', self.app)

    def test_interface_catalogs_have_identical_complete_keys(self):
        self.assertEqual(self.ja_keys, self.en_keys)
        self.assertGreater(len(self.ja_keys), 100)
        referenced = set(
            re.findall(
                r'data-i18n(?:-placeholder|-aria-label)?="([A-Za-z0-9_.]+)"',
                self.index,
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


if __name__ == "__main__":
    unittest.main()
