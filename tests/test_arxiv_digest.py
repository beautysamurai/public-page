from __future__ import annotations

import html
import http.client
import io
import json
import tempfile
import unittest
import urllib.error
import urllib.parse
from datetime import date, datetime, timezone
from pathlib import Path

from scripts import arxiv_digest as digest


UTC = timezone.utc
CHECKED_AT = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)


def entry_xml(
    *,
    arxiv_id: str = "2608.12345v1",
    title: str = "Interest-rate swap execution in electronic markets",
    abstract: str = "We study market microstructure and optimal execution.",
    published: str = "2026-08-20T08:00:00Z",
    updated: str | None = None,
    categories: tuple[str, ...] = ("q-fin.TR",),
    authors: tuple[str, ...] = ("Researcher One",),
    source_url: str | None = None,
) -> str:
    updated = updated or published
    source_url = source_url or f"http://arxiv.org/abs/{arxiv_id}"
    category_xml = "".join(
        f'<category term="{html.escape(category, quote=True)}" />'
        for category in categories
    )
    author_xml = "".join(
        f"<author><name>{html.escape(author)}</name></author>"
        for author in authors
    )
    return f"""
    <entry>
      <id>{html.escape(source_url)}</id>
      <updated>{html.escape(updated)}</updated>
      <published>{html.escape(published)}</published>
      <title>{html.escape(title)}</title>
      <summary>{html.escape(abstract)}</summary>
      {author_xml}
      {category_xml}
      <link href="javascript:ignored-source-link" />
      <arxiv:comment xmlns:arxiv="http://arxiv.org/schemas/atom">
        This private-to-the-site field must not be copied.
      </arxiv:comment>
    </entry>
    """


def feed_xml(
    *entries: str,
    updated: str = "2026-08-20T12:00:00Z",
) -> bytes:
    joined = "".join(entries)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <id>https://export.arxiv.org/api/query</id>
      <updated>{html.escape(updated)}</updated>
      <title>arXiv Query</title>
      {joined}
    </feed>
    """.encode("utf-8")


class FakeResponse:
    def __init__(self, body: bytes):
        self._body = io.BytesIO(body)

    def read(self, size: int = -1) -> bytes:
        return self._body.read(size)

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None


def opener_for(body: bytes):
    def opener(request, timeout=0):
        opener.request = request
        opener.timeout = timeout
        return FakeResponse(body)

    return opener


def simple_config(**changes) -> digest.DigestConfig:
    values = {
        "categories": ("q-fin.TR",),
        "keywords": ("interest rate swap", "market microstructure"),
        "minimum_score": 6,
        "max_results": 25,
        "stale_after_days": 4,
    }
    values.update(changes)
    return digest.DigestConfig(**values)


class QueryAndConfigTests(unittest.TestCase):
    def test_query_is_submitted_date_descending_and_covers_config(self):
        config = simple_config()
        url = digest.build_query_url(config)
        parsed = urllib.parse.urlsplit(url)
        parameters = urllib.parse.parse_qs(parsed.query)

        self.assertEqual(parsed.scheme, "https")
        self.assertEqual(parsed.netloc, "export.arxiv.org")
        self.assertEqual(parameters["sortBy"], ["submittedDate"])
        self.assertEqual(parameters["sortOrder"], ["descending"])
        self.assertEqual(parameters["max_results"], ["25"])
        self.assertIn("cat:q-fin.TR", parameters["search_query"][0])
        self.assertIn('all:"interest rate swap"', parameters["search_query"][0])

    def test_default_scope_contains_every_requested_research_area(self):
        lowered = " ".join(digest.DEFAULT_KEYWORDS).casefold()
        for term in (
            "electronic trading",
            "market microstructure",
            "limit order book",
            "rfq",
            "market making",
            "optimal execution",
            "interest rate",
            "yield curve",
            "swap",
            "swaption",
            "ois",
            "fixed income",
        ):
            self.assertIn(term, lowered)

    def test_config_rejects_unknown_private_duplicate_and_injected_values(self):
        invalid_values = (
            {"_private": True},
            {"unexpected": []},
            {"keywords": ["RFQ", "rfq"]},
            {"keywords": ['safe" OR all:*']},
            {"categories": ["not-a-category"]},
            {"minimumScore": True},
        )
        for value in invalid_values:
            with self.subTest(value=value):
                with self.assertRaises(digest.ConfigurationError):
                    digest.config_from_mapping(value)

    def test_api_endpoint_is_pinned(self):
        with self.assertRaises(digest.ConfigurationError):
            digest.build_query_url(
                simple_config(),
                "https://example.invalid/api/query",
            )

    def test_cli_defaults_cannot_overwrite_public_scheduler_artifacts(self):
        args = digest.build_argument_parser().parse_args([])

        self.assertEqual(
            args.output,
            Path(".local/candidate-data/latest.json"),
        )
        self.assertEqual(
            args.archive_dir,
            Path(".local/candidate-data/archive"),
        )


class AtomParsingTests(unittest.TestCase):
    def test_atom_is_normalised_deduplicated_and_public_links_are_derived(self):
        duplicate = entry_xml(title="Duplicate must be ignored")
        feed = digest.parse_atom_feed(
            feed_xml(
                entry_xml(
                    title="  Interest-rate   swap execution  ",
                    abstract="  Market microstructure\n analysis. ",
                ),
                duplicate,
            )
        )
        self.assertEqual(len(feed.entries), 1)
        entry = feed.entries[0]
        self.assertEqual(entry.title, "Interest-rate swap execution")
        paper = digest.publication_from_entry(entry, simple_config())
        self.assertEqual(paper["absUrl"], "https://arxiv.org/abs/2608.12345v1")
        self.assertEqual(paper["pdfUrl"], "https://arxiv.org/pdf/2608.12345v1")
        self.assertEqual(set(paper), set(digest.PUBLICATION_FIELDS))
        self.assertNotIn("comment", paper)

    def test_parser_rejects_unsafe_id_host(self):
        xml = feed_xml(
            entry_xml(source_url="https://arxiv.org.example/abs/2608.12345v1")
        )
        with self.assertRaises(digest.FeedParseError):
            digest.parse_atom_feed(xml)

    def test_parser_rejects_dtd_and_entity_declarations(self):
        xml = b"""<?xml version="1.0"?>
        <!DOCTYPE feed [<!ENTITY secret SYSTEM "file:///etc/passwd">]>
        <feed xmlns="http://www.w3.org/2005/Atom">
          <title>&secret;</title>
        </feed>"""
        with self.assertRaises(digest.FeedParseError):
            digest.parse_atom_feed(xml)

    def test_parser_rejects_missing_required_public_author(self):
        xml = feed_xml(entry_xml(authors=()))
        with self.assertRaises(digest.FeedParseError):
            digest.parse_atom_feed(xml)


class ScoringAndSchemaTests(unittest.TestCase):
    def test_score_and_explanations_do_not_depend_on_config_order(self):
        entry = digest.parse_atom_feed(feed_xml(entry_xml())).entries[0]
        first = simple_config(
            keywords=("market microstructure", "interest rate swap")
        )
        second = simple_config(
            keywords=("interest rate swap", "market microstructure")
        )
        self.assertEqual(
            digest.score_entry(entry, first),
            digest.score_entry(entry, second),
        )

    def test_phrase_matching_accepts_hyphens_but_not_substrings(self):
        entry = digest.AtomEntry(
            arxiv_id="2608.11111v1",
            title="A yield-curve model",
            authors=("Researcher One",),
            submitted_at=CHECKED_AT,
            updated_at=CHECKED_AT,
            categories=(),
            abstract="A swaption model, without the word lobotomy as LOB.",
        )
        config = simple_config(
            keywords=("yield curve", "LOB"),
            minimum_score=1,
        )
        score, topics, reasons = digest.score_entry(entry, config)
        self.assertGreater(score, 0)
        self.assertEqual(topics, ["LOB", "yield curve"])
        self.assertTrue(any("yield curve" in reason for reason in reasons))

    def test_publication_schema_rejects_extra_private_and_unsafe_link_fields(self):
        entry = digest.parse_atom_feed(feed_xml(entry_xml())).entries[0]
        paper = digest.publication_from_entry(entry, simple_config())
        for field in ("rawAtom", "_internal"):
            hostile = dict(paper)
            hostile[field] = "must not pass"
            with self.subTest(field=field):
                with self.assertRaises(digest.SchemaError):
                    digest.validate_publication(hostile)

        for url in (
            "http://arxiv.org/abs/2608.12345v1",
            "https://arxiv.org.example/abs/2608.12345v1",
            "https://arxiv.org/abs/2608.99999v1",
            "https://arxiv.org/abs/2608.12345v1?download=1",
        ):
            hostile = dict(paper)
            hostile["absUrl"] = url
            with self.subTest(url=url):
                with self.assertRaises(digest.SchemaError):
                    digest.validate_publication(hostile)


class StatusTests(unittest.TestCase):
    def test_relevant_fresh_batch_is_confirmed(self):
        feed = digest.parse_atom_feed(feed_xml(entry_xml()))
        report = digest.report_from_feed(
            feed,
            simple_config(),
            CHECKED_AT,
            date(2026, 8, 20),
        )
        self.assertEqual(report["status"], digest.UPDATE_CONFIRMED)
        self.assertEqual(report["observedBatchDate"], "2026-08-20")
        self.assertEqual(len(report["papers"]), 1)
        digest.validate_report(report)

    def test_fresh_batch_with_zero_relevant_hits_is_explicit(self):
        feed = digest.parse_atom_feed(
            feed_xml(
                entry_xml(
                    title="A general probability theorem",
                    abstract="A mathematical result with no trading application.",
                )
            )
        )
        report = digest.report_from_feed(
            feed,
            simple_config(),
            CHECKED_AT,
            date(2026, 8, 20),
        )
        self.assertEqual(report["status"], digest.NO_RELEVANT_PAPERS)
        self.assertEqual(report["papers"], [])

    def test_stale_feed_is_not_mislabeled_as_zero_relevant_hits(self):
        feed = digest.parse_atom_feed(
            feed_xml(
                entry_xml(
                    title="A general probability theorem",
                    abstract="A mathematical result.",
                    published="2026-08-10T08:00:00Z",
                ),
                updated="2026-08-10T12:00:00Z",
            )
        )
        report = digest.report_from_feed(
            feed,
            simple_config(stale_after_days=4),
            CHECKED_AT,
            date(2026, 8, 20),
        )
        self.assertEqual(report["status"], digest.UPDATE_NOT_CONFIRMED)
        self.assertNotEqual(report["status"], digest.NO_RELEVANT_PAPERS)
        self.assertIn("10 days behind", report["statusMessage"])

    def test_empty_feed_cannot_claim_no_relevant_papers(self):
        feed = digest.parse_atom_feed(feed_xml())
        report = digest.report_from_feed(
            feed,
            simple_config(),
            CHECKED_AT,
            date(2026, 8, 20),
        )
        self.assertEqual(report["status"], digest.UPDATE_NOT_CONFIRMED)
        self.assertIsNone(report["observedBatchDate"])

    def test_only_newest_observed_batch_is_published(self):
        feed = digest.parse_atom_feed(
            feed_xml(
                entry_xml(
                    arxiv_id="2608.20000v1",
                    title="A general probability theorem",
                    abstract="A mathematical result.",
                    published="2026-08-20T08:00:00Z",
                ),
                entry_xml(
                    arxiv_id="2608.10000v1",
                    title="Market microstructure and interest rate swap execution",
                    published="2026-08-19T08:00:00Z",
                ),
            )
        )
        report = digest.report_from_feed(
            feed,
            simple_config(),
            CHECKED_AT,
            date(2026, 8, 20),
        )
        self.assertEqual(report["status"], digest.NO_RELEVANT_PAPERS)
        self.assertEqual(report["papers"], [])

    def test_future_dated_feed_is_not_confirmed(self):
        feed = digest.parse_atom_feed(
            feed_xml(
                entry_xml(published="2026-08-22T08:00:00Z"),
                updated="2026-08-22T12:00:00Z",
            )
        )
        report = digest.report_from_feed(
            feed,
            simple_config(),
            CHECKED_AT,
            date(2026, 8, 20),
        )
        self.assertEqual(report["status"], digest.UPDATE_NOT_CONFIRMED)

    def test_expected_batch_date_rolls_weekend_back_to_friday(self):
        sunday = datetime(2026, 8, 23, 3, 0, tzinfo=UTC)
        self.assertEqual(
            digest.expected_batch_date(sunday),
            date(2026, 8, 21),
        )


class FetchClassificationTests(unittest.TestCase):
    def test_network_failure_is_offline_without_exposing_exception_details(self):
        def offline(*_args, **_kwargs):
            raise urllib.error.URLError("private-network-detail")

        report = digest.generate_report(
            simple_config(),
            checked_at=CHECKED_AT,
            expected=date(2026, 8, 20),
            opener=offline,
        )
        self.assertEqual(report["status"], digest.UPDATER_OFFLINE)
        self.assertNotIn("private-network-detail", report["statusMessage"])

    def test_incomplete_http_response_is_classified_as_offline(self):
        def interrupted(*_args, **_kwargs):
            raise http.client.IncompleteRead(b"partial")

        report = digest.generate_report(
            simple_config(),
            checked_at=CHECKED_AT,
            expected=date(2026, 8, 20),
            opener=interrupted,
        )
        self.assertEqual(report["status"], digest.UPDATER_OFFLINE)

    def test_malformed_or_nonbyte_response_is_not_confirmed(self):
        malformed = digest.generate_report(
            simple_config(),
            checked_at=CHECKED_AT,
            expected=date(2026, 8, 20),
            opener=opener_for(b"<html>not atom</html>"),
        )
        self.assertEqual(malformed["status"], digest.UPDATE_NOT_CONFIRMED)

        class TextResponse(FakeResponse):
            def read(self, size=-1):
                return "not bytes"

        def text_opener(*_args, **_kwargs):
            return TextResponse(b"")

        nonbytes = digest.generate_report(
            simple_config(),
            checked_at=CHECKED_AT,
            expected=date(2026, 8, 20),
            opener=text_opener,
        )
        self.assertEqual(nonbytes["status"], digest.UPDATE_NOT_CONFIRMED)

    def test_fetch_sends_generic_user_agent_and_honors_timeout(self):
        opener = opener_for(feed_xml(entry_xml()))
        report = digest.generate_report(
            simple_config(),
            checked_at=CHECKED_AT,
            expected=date(2026, 8, 20),
            timeout=7.5,
            opener=opener,
        )
        self.assertEqual(report["status"], digest.UPDATE_CONFIRMED)
        self.assertEqual(opener.timeout, 7.5)
        self.assertEqual(
            opener.request.get_header("User-agent"),
            digest.USER_AGENT,
        )
        self.assertNotIn("@", digest.USER_AGENT)


class PersistenceTests(unittest.TestCase):
    def _report(self, expected: date, status: str) -> dict:
        feed = digest.parse_atom_feed(
            feed_xml(
                entry_xml(
                    published=f"{expected.isoformat()}T08:00:00Z",
                    updated=f"{expected.isoformat()}T09:00:00Z",
                ),
                updated=f"{expected.isoformat()}T12:00:00Z",
            )
        )
        report = digest.report_from_feed(
            feed,
            simple_config(),
            datetime.combine(
                expected,
                datetime.min.time(),
                tzinfo=UTC,
            ),
            expected,
        )
        report["status"] = status
        report["statusMessage"] = "Test status."
        digest.validate_report(report)
        return report

    def test_persist_writes_latest_dated_archive_and_newest_first_index(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            latest = root / "site" / "data" / "latest.json"
            archive = root / "site" / "data" / "archive"
            older = self._report(
                date(2026, 8, 19),
                digest.UPDATE_CONFIRMED,
            )
            newer = self._report(
                date(2026, 8, 20),
                digest.NO_RELEVANT_PAPERS,
            )

            digest.persist_report(older, latest, archive)
            digest.persist_report(newer, latest, archive)
            digest.persist_report(newer, latest, archive)

            with latest.open(encoding="utf-8") as handle:
                self.assertEqual(json.load(handle), newer)
            with (archive / "2026-08-19.json").open(encoding="utf-8") as handle:
                self.assertEqual(json.load(handle), older)
            with (archive / "index.json").open(encoding="utf-8") as handle:
                index = json.load(handle)
            self.assertEqual(
                index,
                {
                    "schemaVersion": 1,
                    "reports": [
                        {
                            "date": "2026-08-20",
                            "path": "2026-08-20.json",
                            "status": digest.NO_RELEVANT_PAPERS,
                            "paperCount": 1,
                        },
                        {
                            "date": "2026-08-19",
                            "path": "2026-08-19.json",
                            "status": digest.UPDATE_CONFIRMED,
                            "paperCount": 1,
                        },
                    ],
                },
            )
            self.assertEqual(
                list(archive.glob(".*.tmp")),
                [],
                "atomic temporary files should be cleaned up",
            )

    def test_invalid_existing_index_is_rejected_before_any_report_write(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            latest = root / "latest.json"
            archive = root / "archive"
            archive.mkdir()
            with (archive / "index.json").open("w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "schemaVersion": 1,
                        "reports": [],
                        "_private": "must be rejected",
                    },
                    handle,
                )
            report = self._report(
                date(2026, 8, 20),
                digest.UPDATE_CONFIRMED,
            )

            with self.assertRaises(digest.SchemaError):
                digest.persist_report(report, latest, archive)
            self.assertFalse(latest.exists())
            self.assertFalse((archive / "2026-08-20.json").exists())


if __name__ == "__main__":
    unittest.main()
