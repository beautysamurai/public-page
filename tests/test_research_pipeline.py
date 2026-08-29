import json
import os
import sys
import tempfile
import unittest
import urllib.error
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import arxiv_digest as digest  # noqa: E402
import research_pipeline as pipeline  # noqa: E402


UTC = timezone.utc
CHECKED_AT = datetime(2026, 8, 28, 12, 0, tzinfo=UTC)


def listing_html(
    batch: str = "Friday, 28 August 2026",
    *,
    new: tuple[str, ...] = (),
    cross: tuple[str, ...] = (),
    replacements: tuple[str, ...] = (),
) -> bytes:
    def section(title, values):
        items = "".join(
            f'<dt><a name="item{index}">[{index}]</a>'
            f'<a href="/abs/{arxiv_id}" title="Abstract">arXiv:{arxiv_id}</a></dt>'
            f'<dd><div class="meta">Untrusted source {arxiv_id}</div></dd>'
            for index, arxiv_id in enumerate(values, 1)
        )
        return f"<h3>{title} (showing {len(values)} of {len(values)} entries)</h3><dl>{items}</dl>"

    return (
        "<!doctype html><html><body>"
        f"<h3>Showing new listings for {batch}</h3>"
        + section("New submissions", new)
        + section("Cross submissions", cross)
        + section("Replacement submissions", replacements)
        + "</body></html>"
    ).encode("utf-8")


def pastweek_html(*batches: tuple[str, tuple[str, ...]]) -> bytes:
    sections = []
    for batch, arxiv_ids in batches:
        items = "".join(
            f'<dt><a href="/abs/{arxiv_id}" title="Abstract">'
            f'arXiv:{arxiv_id}</a></dt><dd>Untrusted source</dd>'
            for arxiv_id in arxiv_ids
        )
        sections.extend(
            [
                f"<h3>Showing submissions for {batch}</h3>",
                f"<h3>New submissions (showing {len(arxiv_ids)} entries)</h3>",
                f"<dl>{items}</dl>",
            ]
        )
    return ("<!doctype html><html><body>" + "".join(sections) + "</body></html>").encode(
        "utf-8"
    )


def entry(
    arxiv_id: str,
    *,
    title: str = "Electronic execution in rates markets",
    abstract: str = "We study market microstructure and yield-curve trading.",
) -> digest.AtomEntry:
    return digest.AtomEntry(
        arxiv_id=f"{arxiv_id}v1" if "v" not in arxiv_id else arxiv_id,
        title=title,
        authors=("Researcher One",),
        submitted_at=datetime(2026, 8, 28, 8, 0, tzinfo=UTC),
        updated_at=datetime(2026, 8, 28, 9, 0, tzinfo=UTC),
        categories=("q-fin.TR",),
        abstract=abstract,
    )


def analysis(
    *,
    importance: int = 3,
    classification: str = "market_microstructure",
    recommended: bool = True,
) -> dict:
    return {
        "classification": classification,
        "summary": "日本語の要約です。",
        "mainResult": "主要な結果です。",
        "practicalApplication": "実務への応用です。",
        "methodology": "研究手法です。",
        "limitations": "限界があります。",
        "importance": importance,
        "recommended": recommended,
        "reason": "読む理由です。",
        "tags": ["市場マイクロストラクチャー"],
        "english": {
            "classification": classification,
            "summary": "English summary.",
            "mainResult": "Main result.",
            "practicalApplication": "Practical application.",
            "methodology": "Research methodology.",
            "limitations": "Study limitations.",
            "reason": "Reason to read.",
            "tags": ["market microstructure"],
        },
    }


def config(**changes) -> pipeline.PipelineConfig:
    values = {
        "categories": ("q-fin.TR",),
        "pdf_importance_threshold": 3,
        "screen_model": "screen-model",
        "full_model": "full-model",
        "weekly_model": "weekly-model",
        "monthly_model": "monthly-model",
        "screen_reasoning_effort": "low",
        "full_reasoning_effort": "medium",
        "weekly_reasoning_effort": "medium",
        "monthly_reasoning_effort": "high",
        "pdf_detail": "low",
        "max_candidates": 100,
        "retries": 0,
        "timeout": 2.0,
    }
    values.update(changes)
    return pipeline.PipelineConfig(**values)


class FakeAnalyzer:
    def __init__(self, screens=None, full=None, synthesized=None):
        self.screens = screens or {}
        self.full = full or {}
        self.synthesized = synthesized
        self.screened = []
        self.pdfs = []
        self.synthesis_inputs = []

    def analyze_abstract(self, candidate):
        arxiv_id = pipeline._base_arxiv_id(candidate.entry.arxiv_id)
        self.screened.append(arxiv_id)
        return self.screens.get(arxiv_id, analysis())

    def analyze_pdf(self, candidate):
        arxiv_id = pipeline._base_arxiv_id(candidate.entry.arxiv_id)
        self.pdfs.append(arxiv_id)
        return self.full.get(arxiv_id, analysis(importance=5))

    def synthesize(self, papers, report_kind, period_start, period_end):
        self.synthesis_inputs.append(
            (json.loads(json.dumps(papers)), report_kind, period_start, period_end)
        )
        if self.synthesized is not None:
            return self.synthesized
        return [
            {
                "arxivId": paper["metadata"]["arxivId"],
                "finalAnalysis": paper["finalAnalysis"],
            }
            for paper in papers
        ]


class ListingParsingTests(unittest.TestCase):
    def test_parses_batch_ids_and_listing_types_and_deduplicates(self):
        parsed = pipeline.parse_listing_page(
            listing_html(
                new=("2608.10001",),
                cross=("2608.10001", "2608.10002"),
                replacements=("2608.10003v2",),
            ),
            "q-fin.TR",
        )
        self.assertEqual(parsed.batch_date, date(2026, 8, 28))
        self.assertEqual(
            [(item.arxiv_id, item.listing_type) for item in parsed.items],
            [
                ("2608.10001", "new"),
                ("2608.10001", "cross"),
                ("2608.10002", "cross"),
                ("2608.10003v2", "replacement"),
            ],
        )

    def test_rejects_missing_date_and_oversized_page(self):
        with self.assertRaises(pipeline.ListingParseError):
            pipeline.parse_listing_page(b"<html><h3>New submissions</h3></html>", "q-fin.TR")
        with self.assertRaises(pipeline.ListingParseError):
            pipeline.parse_listing_page(b"x" * (pipeline.MAX_LIST_BYTES + 1), "q-fin.TR")

    def test_parses_bounded_pastweek_batches_by_announcement_date(self):
        pages = pipeline.parse_pastweek_listing_page(
            pastweek_html(
                ("Friday, 28 August 2026", ("2608.10002",)),
                ("Thursday, 27 August 2026", ("2608.10001",)),
            ),
            "q-fin.TR",
        )
        self.assertEqual(
            [(page.batch_date, [item.arxiv_id for item in page.items]) for page in pages],
            [
                (date(2026, 8, 28), ["2608.10002"]),
                (date(2026, 8, 27), ["2608.10001"]),
            ],
        )

    def test_pastweek_parser_accepts_real_short_heading_and_cross_list_marker(self):
        pages = pipeline.parse_pastweek_listing_page(
            b"""
            <dl id='articles'>
              <h3>Fri, 28 Aug 2026 (showing 1 of 1 entries)</h3>
              <dt><a name='item1'>[1]</a>
                <a href ='/abs/2608.27076' title='Abstract'>arXiv:2608.27076</a>
                (cross-list from cs.LG)
              </dt>
            </dl>
            <dl id='articles'>
              <h3>Mon, 24 Aug 2026</h3><p>No updates for this time period.</p>
            </dl>
            """,
            "q-fin.TR",
        )
        self.assertEqual([page.batch_date for page in pages], [date(2026, 8, 28), date(2026, 8, 24)])
        self.assertEqual(pages[0].items[0].listing_type, "cross")
        self.assertEqual(pages[1].items, ())

    def test_rfq_hint_is_boundary_aware(self):
        self.assertTrue(pipeline.contains_topic_term("An RFQ execution protocol", "RFQ"))
        self.assertFalse(pipeline.contains_topic_term("A nonrfqsignal classifier", "RFQ"))
        self.assertFalse(pipeline.contains_topic_term("RFQuality image restoration", "RFQ"))


class RecordingResponses:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(output_text=json.dumps(self.outputs.pop(0), ensure_ascii=False))


class ResponsesAdapterTests(unittest.TestCase):
    def test_abstract_and_pdf_use_strict_schema_store_false_and_file_url(self):
        responses = RecordingResponses([analysis(importance=4), analysis(importance=5)])
        adapter = pipeline.ResponsesAnalyzer(config(), SimpleNamespace(responses=responses))
        candidate = pipeline.PaperCandidate(entry("2608.12345"), ("new",), ("q-fin.TR",))

        adapter.analyze_abstract(candidate)
        adapter.analyze_pdf(candidate)

        abstract_call, pdf_call = responses.calls
        for call in responses.calls:
            self.assertIs(call["store"], False)
            self.assertEqual(call["text"]["format"]["type"], "json_schema")
            self.assertIs(call["text"]["format"]["strict"], True)
            self.assertIn("untrusted", call["instructions"].casefold())
        self.assertEqual(abstract_call["model"], "screen-model")
        self.assertEqual(abstract_call["reasoning"], {"effort": "low"})
        self.assertEqual(pdf_call["model"], "full-model")
        self.assertEqual(pdf_call["reasoning"], {"effort": "medium"})
        contents = pdf_call["input"][0]["content"]
        file_input = next(item for item in contents if item["type"] == "input_file")
        self.assertEqual(
            file_input,
            {
                "type": "input_file",
                "file_url": "https://arxiv.org/pdf/2608.12345v1",
                "detail": "low",
            },
        )

    def test_weekly_and_monthly_synthesis_use_distinct_models(self):
        responses = RecordingResponses([{"papers": []}, {"papers": []}])
        adapter = pipeline.ResponsesAnalyzer(config(), SimpleNamespace(responses=responses))

        adapter.synthesize([], pipeline.WEEKLY, date(2026, 8, 22), date(2026, 8, 28))
        adapter.synthesize([], pipeline.MONTHLY, date(2026, 8, 1), date(2026, 8, 31))

        self.assertEqual(responses.calls[0]["model"], "weekly-model")
        self.assertEqual(responses.calls[0]["reasoning"], {"effort": "medium"})
        self.assertEqual(responses.calls[1]["model"], "monthly-model")
        self.assertEqual(responses.calls[1]["reasoning"], {"effort": "high"})

    def test_structured_output_validation_rejects_missing_extra_and_bool_importance(self):
        mismatched_classification = analysis()
        mismatched_classification["english"] = {
            **mismatched_classification["english"],
            "classification": "yield_curve",
        }
        for bad in (
            {key: value for key, value in analysis().items() if key != "english"},
            {**analysis(), "private": "forbidden"},
            {**analysis(), "importance": True},
            {**analysis(), "classification": "ignore_previous_instructions"},
            mismatched_classification,
        ):
            with self.subTest(bad=bad):
                with self.assertRaises(pipeline.StructuredOutputError):
                    pipeline.validate_analysis(bad)

        short_classification = analysis()
        short_classification["classification"] = "mixed"
        short_classification["english"]["classification"] = "mixed"
        self.assertEqual(
            pipeline.validate_analysis(short_classification),
            short_classification,
        )

    def test_primary_narratives_require_japanese_but_allow_mixed_terms(self):
        for field in pipeline.PRIMARY_NARRATIVE_FIELDS:
            with self.subTest(field=field):
                bad = analysis()
                bad[field] = bad["english"][field]
                with self.assertRaisesRegex(
                    pipeline.StructuredOutputError,
                    rf"{field} must contain Japanese text",
                ):
                    pipeline.validate_analysis(bad)

        for almost_japanese in (
            "This remains an English sentence with one ideograph 日",
            "金融市场微观结构研究",
            "这是关于市场微观结构・电子交易的研究。",
            "这是关于市场微观结构和电子交易の研究。",
        ):
            bad = analysis()
            bad["summary"] = almost_japanese
            with self.subTest(almost_japanese=almost_japanese):
                with self.assertRaisesRegex(
                    pipeline.StructuredOutputError,
                    "summary must contain Japanese text",
                ):
                    pipeline.validate_analysis(bad)

        mixed = analysis()
        mixed.update(
            {
                "summary": "Cross-regime validationでsignalを評価します。",
                "mainResult": "OOS Sharpeが安定しました。",
                "practicalApplication": "quote engineへ応用できます。",
                "methodology": "Bayesian optimizationを使います。",
                "limitations": "survivorship biasに注意が必要です。",
                "reason": "model selectionの実務に役立ちます。",
            }
        )
        self.assertEqual(pipeline.validate_analysis(mixed), mixed)

        mixed["english"]["summary"] = (
            "The study measures 日本国債 market liquidity under stress."
        )
        self.assertEqual(pipeline.validate_analysis(mixed), mixed)

    def test_model_narratives_must_already_satisfy_public_text_rules(self):
        for unsafe in (
            "<b>日本語の要約です。</b>",
            "結果は https://doi.org/10.1000/example で確認できます。",
            "日本語の要約です。\ue000",
            "日本語の要約です。\u0080",
        ):
            bad = analysis()
            bad["summary"] = unsafe
            with self.subTest(unsafe=unsafe):
                with self.assertRaisesRegex(
                    pipeline.StructuredOutputError,
                    "summary is not safe for publication",
                ):
                    pipeline.validate_analysis(bad)

    def test_client_initialization_error_is_sanitized(self):
        sensitive_detail = "malformed OPENAI_API_KEY sk-private-value"

        def failing_client():
            raise ValueError(sensitive_detail)

        fake_openai = SimpleNamespace(OpenAI=failing_client)
        with mock.patch.dict(sys.modules, {"openai": fake_openai}):
            with self.assertRaises(pipeline.UpdaterOfflineError) as caught:
                pipeline.ResponsesAnalyzer(config())

        self.assertEqual(
            str(caught.exception),
            "Responses API client could not be initialized",
        )
        self.assertNotIn(sensitive_detail, str(caught.exception))


class PersistenceTests(unittest.TestCase):
    @staticmethod
    def pending_report(*, generated_at=CHECKED_AT, message="Review is pending."):
        return pipeline._report(
            report_kind=pipeline.DAILY,
            report_date=date(2026, 8, 28),
            generated_at=generated_at,
            status=pipeline.UPDATER_OFFLINE,
            message=message,
            expected_batch_date=date(2026, 8, 28),
            observed_batch_date=date(2026, 8, 28),
            period_start=None,
            period_end=None,
            papers=[],
        )

    @staticmethod
    def completed_report(*, generated_at=CHECKED_AT, message="Review completed."):
        paper = {
            "metadata": pipeline.metadata_from_entry(entry("2608.12345")),
            "finalAnalysis": analysis(importance=4),
        }
        return pipeline._report(
            report_kind=pipeline.DAILY,
            report_date=date(2026, 8, 28),
            generated_at=generated_at,
            status=pipeline.UPDATE_CONFIRMED,
            message=message,
            expected_batch_date=date(2026, 8, 28),
            observed_batch_date=date(2026, 8, 28),
            period_start=None,
            period_end=None,
            papers=[paper],
        )

    @staticmethod
    def fail_first_markdown_replace(markdown_path, real_replace):
        failed = False

        def replace(source, destination):
            nonlocal failed
            if Path(destination) == markdown_path and not failed:
                failed = True
                raise OSError("injected Markdown replace failure")
            return real_replace(source, destination)

        return replace

    def test_initial_pair_failure_rolls_back_and_retry_writes_both_files(self):
        report = self.completed_report()
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            json_path = output_dir / "2026-08-28.json"
            markdown_path = output_dir / "2026-08-28.md"
            real_replace = os.replace
            injected_replace = self.fail_first_markdown_replace(
                markdown_path, real_replace
            )

            with mock.patch.object(pipeline.os, "replace", side_effect=injected_replace):
                with self.assertRaises(OSError):
                    pipeline.persist_report(report, output_dir)

            self.assertFalse(json_path.exists())
            self.assertFalse(markdown_path.exists())
            self.assertEqual(list(output_dir.glob("*.tmp")), [])

            stored = pipeline.persist_report(report, output_dir)
            self.assertEqual(stored, report)
            self.assertEqual(pipeline._read_persisted_report(json_path), report)
            self.assertEqual(markdown_path.read_text(encoding="utf-8"), pipeline.report_to_markdown(report))

    def test_json_only_report_repairs_missing_markdown_without_mutating_json(self):
        report = self.completed_report()
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            json_path = output_dir / "2026-08-28.json"
            markdown_path = output_dir / "2026-08-28.md"
            pipeline.atomic_write_json(json_path, report)
            original_json = json_path.read_bytes()

            stored = pipeline.persist_report(report, output_dir)

            self.assertEqual(stored, report)
            self.assertEqual(json_path.read_bytes(), original_json)
            self.assertEqual(markdown_path.read_text(encoding="utf-8"), pipeline.report_to_markdown(report))

    def test_completed_collision_repairs_stale_markdown_from_authoritative_json(self):
        original = self.completed_report()
        conflicting = self.completed_report(
            generated_at=datetime(2026, 8, 28, 18, 0, tzinfo=UTC),
            message="A conflicting rerun must not replace the edition.",
        )
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            json_path = output_dir / "2026-08-28.json"
            markdown_path = output_dir / "2026-08-28.md"
            pipeline.persist_report(original, output_dir)
            original_json = json_path.read_bytes()
            markdown_path.write_text("stale Markdown\n", encoding="utf-8")

            stored = pipeline.persist_report(conflicting, output_dir)

            self.assertEqual(stored, original)
            self.assertEqual(json_path.read_bytes(), original_json)
            self.assertEqual(markdown_path.read_text(encoding="utf-8"), pipeline.report_to_markdown(original))

    def test_pending_to_complete_pair_failure_restores_pending_edition(self):
        pending = self.pending_report()
        completed = self.completed_report(
            generated_at=datetime(2026, 8, 28, 18, 0, tzinfo=UTC)
        )
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            json_path = output_dir / "2026-08-28.json"
            markdown_path = output_dir / "2026-08-28.md"
            pipeline.persist_report(pending, output_dir)
            pending_json = json_path.read_bytes()
            pending_markdown = markdown_path.read_bytes()
            real_replace = os.replace
            injected_replace = self.fail_first_markdown_replace(
                markdown_path, real_replace
            )

            with mock.patch.object(pipeline.os, "replace", side_effect=injected_replace):
                with self.assertRaises(OSError):
                    pipeline.persist_report(completed, output_dir)

            self.assertEqual(json_path.read_bytes(), pending_json)
            self.assertEqual(markdown_path.read_bytes(), pending_markdown)
            self.assertEqual(list(output_dir.glob("*.tmp")), [])

            stored = pipeline.persist_report(completed, output_dir)
            self.assertEqual(stored, completed)
            self.assertEqual(pipeline._read_persisted_report(json_path), completed)
            self.assertEqual(markdown_path.read_text(encoding="utf-8"), pipeline.report_to_markdown(completed))


class DailyWorkflowTests(unittest.TestCase):
    def run_in_temp(self, *, page, entries=None, analyzer=None, checked_at=CHECKED_AT, initial_state=None):
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        state_path = root / "state.json"
        output_dir = root / "daily"
        if initial_state is not None:
            pipeline.save_state(state_path, initial_state)

        def metadata_fetcher(ids):
            values = entries or {}
            return {arxiv_id: values[arxiv_id] for arxiv_id in ids}

        report = pipeline.run_daily(
            config(),
            state_path=state_path,
            output_dir=output_dir,
            checked_at=checked_at,
            list_fetcher=lambda _category: page,
            metadata_fetcher=metadata_fetcher,
            analyzer=analyzer,
            sleep_fn=lambda _delay: None,
        )
        return temporary, root, state_path, output_dir, report

    def test_confirmed_zero_does_not_need_openai_or_metadata(self):
        called = []
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = pipeline.run_daily(
                config(),
                state_path=root / "state.json",
                output_dir=root / "daily",
                checked_at=CHECKED_AT,
                list_fetcher=lambda _category: listing_html(),
                metadata_fetcher=lambda _ids: called.append("metadata"),
                analyzer=None,
                sleep_fn=lambda _delay: None,
            )
            self.assertEqual(report["status"], pipeline.NO_RELEVANT_PAPERS)
            self.assertEqual(called, [])
            self.assertEqual(report["papers"], [])

    def test_pdf_is_called_only_at_or_above_threshold(self):
        ids = ("2608.10001", "2608.10002")
        analyzer = FakeAnalyzer(
            screens={
                ids[0]: analysis(importance=2),
                ids[1]: analysis(importance=3),
            }
        )
        temporary, root, state_path, output_dir, report = self.run_in_temp(
            page=listing_html(new=ids),
            entries={arxiv_id: entry(arxiv_id) for arxiv_id in ids},
            analyzer=analyzer,
        )
        self.addCleanup(temporary.cleanup)
        self.assertEqual(analyzer.screened, list(ids))
        self.assertEqual(analyzer.pdfs, [ids[1]])
        self.assertEqual(report["status"], pipeline.UPDATE_CONFIRMED)
        self.assertEqual(len(report["papers"]), 2)
        self.assertEqual(set(report), set(pipeline.TOP_LEVEL_FIELDS))
        self.assertTrue((output_dir / "2026-08-28.json").exists())
        self.assertTrue((output_dir / "2026-08-28.md").exists())
        state = pipeline.load_state(state_path)
        self.assertEqual(state["lastCompletedBatchDate"], "2026-08-28")
        self.assertIsNone(state["pendingBatchDate"])

    def test_abstract_screen_can_confirm_no_relevant_candidates(self):
        arxiv_id = "2608.10001"
        analyzer = FakeAnalyzer(
            screens={
                arxiv_id: analysis(
                    importance=1,
                    classification="out_of_scope",
                    recommended=False,
                )
            }
        )
        temporary, _root, _state_path, _output_dir, report = self.run_in_temp(
            page=listing_html(new=(arxiv_id,)),
            entries={
                arxiv_id: entry(
                    arxiv_id,
                    title="RFQuality restoration outside finance",
                    abstract="An image restoration system with no trading content.",
                )
            },
            analyzer=analyzer,
        )
        self.addCleanup(temporary.cleanup)
        self.assertEqual(report["status"], pipeline.NO_RELEVANT_PAPERS)
        self.assertEqual(report["papers"], [])
        self.assertEqual(analyzer.screened, [arxiv_id])
        self.assertEqual(analyzer.pdfs, [])

    def test_stale_batch_is_carried_forward_and_retry_clears_on_confirmation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_path = root / "state.json"
            output_dir = root / "daily"
            first = pipeline.run_daily(
                config(),
                state_path=state_path,
                output_dir=output_dir,
                checked_at=CHECKED_AT,
                list_fetcher=lambda _category: listing_html(
                    "Thursday, 27 August 2026"
                ),
                metadata_fetcher=lambda _ids: {},
                analyzer=FakeAnalyzer(),
                sleep_fn=lambda _delay: None,
            )
            self.assertEqual(first["status"], pipeline.UPDATE_NOT_CONFIRMED)
            pending = pipeline.load_state(state_path)
            self.assertEqual(pending["pendingBatchDate"], "2026-08-28")
            self.assertEqual(pending["retryCount"], 1)

            second = pipeline.run_daily(
                config(),
                state_path=state_path,
                output_dir=output_dir,
                checked_at=datetime(2026, 8, 29, 12, 0, tzinfo=UTC),
                list_fetcher=lambda _category: listing_html(),
                metadata_fetcher=lambda _ids: {},
                analyzer=None,
                sleep_fn=lambda _delay: None,
            )
            self.assertEqual(second["status"], pipeline.NO_RELEVANT_PAPERS)
            completed = pipeline.load_state(state_path)
            self.assertIsNone(completed["pendingBatchDate"])
            self.assertEqual(completed["retryCount"], 0)
            self.assertEqual(completed["lastCompletedBatchDate"], "2026-08-28")

    def test_weekend_after_completed_friday_is_no_new_batch_expected(self):
        state = pipeline._default_state()
        state.update(
            {
                "lastCompletedBatchDate": "2026-08-28",
                "lastStatus": pipeline.NO_RELEVANT_PAPERS,
                "lastAttemptedAt": "2026-08-28T12:00:00Z",
            }
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_path = root / "state.json"
            pipeline.save_state(state_path, state)
            calls = []
            report = pipeline.run_daily(
                config(),
                state_path=state_path,
                output_dir=root / "daily",
                checked_at=datetime(2026, 8, 29, 12, 0, tzinfo=UTC),
                list_fetcher=lambda _category: calls.append("unexpected"),
                analyzer=None,
                sleep_fn=lambda _delay: None,
            )
            self.assertEqual(report["status"], pipeline.NO_NEW_BATCH_EXPECTED)
            self.assertEqual(report["reportDate"], "2026-08-29")
            self.assertEqual(calls, [])

    def test_weekend_reports_do_not_overwrite_friday_or_each_other(self):
        state = pipeline._default_state()
        state.update(
            {
                "lastCompletedBatchDate": "2026-08-28",
                "lastStatus": pipeline.UPDATE_CONFIRMED,
                "lastAttemptedAt": "2026-08-28T12:00:00Z",
            }
        )
        friday_paper = {
            "metadata": pipeline.metadata_from_entry(entry("2608.12345")),
            "finalAnalysis": analysis(importance=5),
        }
        friday = pipeline._report(
            report_kind=pipeline.DAILY,
            report_date=date(2026, 8, 28),
            generated_at=CHECKED_AT,
            status=pipeline.UPDATE_CONFIRMED,
            message="Friday completed.",
            expected_batch_date=date(2026, 8, 28),
            observed_batch_date=date(2026, 8, 28),
            period_start=None,
            period_end=None,
            papers=[friday_paper],
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_path = root / "state.json"
            output_dir = root / "daily"
            pipeline.save_state(state_path, state)
            pipeline.persist_report(friday, output_dir)
            for day in (29, 30):
                report = pipeline.run_daily(
                    config(),
                    state_path=state_path,
                    output_dir=output_dir,
                    checked_at=datetime(2026, 8, day, 12, 0, tzinfo=UTC),
                    list_fetcher=lambda _category: self.fail("listing should not be fetched"),
                    analyzer=None,
                    sleep_fn=lambda _delay: None,
                )
                self.assertEqual(report["reportDate"], f"2026-08-{day:02d}")
                self.assertEqual(report["status"], pipeline.NO_NEW_BATCH_EXPECTED)
            friday_stored = pipeline._read_persisted_report(
                output_dir / "2026-08-28.json"
            )
            self.assertEqual(friday_stored, friday)
            self.assertEqual(len(friday_stored["papers"]), 1)
            self.assertTrue((output_dir / "2026-08-29.json").exists())
            self.assertTrue((output_dir / "2026-08-30.json").exists())

    def test_same_day_rerun_returns_completed_immutable_edition(self):
        arxiv_id = "2608.12345"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_path = root / "state.json"
            output_dir = root / "daily"
            first = pipeline.run_daily(
                config(),
                state_path=state_path,
                output_dir=output_dir,
                checked_at=CHECKED_AT,
                list_fetcher=lambda _category: listing_html(new=(arxiv_id,)),
                metadata_fetcher=lambda _ids: {arxiv_id: entry(arxiv_id)},
                analyzer=FakeAnalyzer(),
                sleep_fn=lambda _delay: None,
            )
            original_bytes = (output_dir / "2026-08-28.json").read_bytes()
            markdown_path = output_dir / "2026-08-28.md"
            markdown_path.unlink()
            second = pipeline.run_daily(
                config(),
                state_path=state_path,
                output_dir=output_dir,
                checked_at=datetime(2026, 8, 28, 18, 0, tzinfo=UTC),
                list_fetcher=lambda _category: self.fail("listing should not be fetched"),
                analyzer=None,
                sleep_fn=lambda _delay: None,
            )
            self.assertEqual(second, first)
            self.assertEqual(
                (output_dir / "2026-08-28.json").read_bytes(), original_bytes
            )
            self.assertEqual(
                markdown_path.read_text(encoding="utf-8"),
                pipeline.report_to_markdown(first),
            )

    def test_advanced_listing_recovers_pending_batch_and_queues_next_batch(self):
        arxiv_id = "2608.10001"
        state = pipeline._default_state()
        state.update(
            {
                "lastCompletedBatchDate": "2026-08-26",
                "pendingBatchDate": "2026-08-27",
                "retryCount": 2,
                "lastStatus": pipeline.UPDATE_NOT_CONFIRMED,
                "lastAttemptedAt": "2026-08-27T12:00:00Z",
            }
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_path = root / "state.json"
            pipeline.save_state(state_path, state)
            report = pipeline.run_daily(
                config(),
                state_path=state_path,
                output_dir=root / "daily",
                checked_at=CHECKED_AT,
                list_fetcher=lambda _category: listing_html(),
                history_fetcher=lambda _category: pastweek_html(
                    ("Friday, 28 August 2026", ()),
                    ("Thursday, 27 August 2026", (arxiv_id,)),
                ),
                metadata_fetcher=lambda _ids: {arxiv_id: entry(arxiv_id)},
                analyzer=FakeAnalyzer(),
                sleep_fn=lambda _delay: None,
            )
            self.assertEqual(report["status"], pipeline.UPDATE_CONFIRMED)
            self.assertEqual(report["reportDate"], "2026-08-27")
            self.assertEqual(report["observedBatchDate"], "2026-08-27")
            self.assertIn("Recovered carried", report["message"])
            advanced = pipeline.load_state(state_path)
            self.assertEqual(advanced["lastCompletedBatchDate"], "2026-08-27")
            self.assertEqual(advanced["pendingBatchDate"], "2026-08-28")
            self.assertEqual(advanced["retryCount"], 0)

            caught_up = pipeline.run_daily(
                config(),
                state_path=state_path,
                output_dir=root / "daily",
                checked_at=datetime(2026, 8, 29, 12, 0, tzinfo=UTC),
                list_fetcher=lambda _category: listing_html(),
                metadata_fetcher=lambda _ids: {},
                analyzer=None,
                sleep_fn=lambda _delay: None,
            )
            self.assertEqual(caught_up["reportDate"], "2026-08-28")
            self.assertEqual(caught_up["status"], pipeline.NO_RELEVANT_PAPERS)
            completed = pipeline.load_state(state_path)
            self.assertEqual(completed["lastCompletedBatchDate"], "2026-08-28")
            self.assertIsNone(completed["pendingBatchDate"])

    def test_missed_weekday_resumes_first_unprocessed_configured_batch(self):
        arxiv_id = "2608.10001"
        state = pipeline._default_state()
        state.update(
            {
                "lastCompletedBatchDate": "2026-08-25",
                "lastStatus": pipeline.NO_RELEVANT_PAPERS,
                "lastAttemptedAt": "2026-08-25T12:00:00Z",
            }
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_path = root / "state.json"
            pipeline.save_state(state_path, state)
            report = pipeline.run_daily(
                config(no_announcement_dates=(date(2026, 8, 26),)),
                state_path=state_path,
                output_dir=root / "daily",
                checked_at=CHECKED_AT,
                list_fetcher=lambda _category: listing_html(),
                history_fetcher=lambda _category: pastweek_html(
                    ("Friday, 28 August 2026", ()),
                    ("Thursday, 27 August 2026", (arxiv_id,)),
                ),
                metadata_fetcher=lambda _ids: {arxiv_id: entry(arxiv_id)},
                analyzer=FakeAnalyzer(),
                sleep_fn=lambda _delay: None,
            )

            self.assertEqual(report["status"], pipeline.UPDATE_CONFIRMED)
            self.assertEqual(report["reportDate"], "2026-08-27")
            self.assertEqual(report["observedBatchDate"], "2026-08-27")
            recovered = pipeline.load_state(state_path)
            self.assertEqual(recovered["lastCompletedBatchDate"], "2026-08-27")
            self.assertEqual(recovered["pendingBatchDate"], "2026-08-28")

    def test_pending_batch_outside_pastweek_window_remains_pending(self):
        state = pipeline._default_state()
        state.update(
            {
                "pendingBatchDate": "2026-08-19",
                "retryCount": 1,
                "lastStatus": pipeline.UPDATE_NOT_CONFIRMED,
                "lastAttemptedAt": "2026-08-19T12:00:00Z",
            }
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_path = root / "state.json"
            pipeline.save_state(state_path, state)
            report = pipeline.run_daily(
                config(),
                state_path=state_path,
                output_dir=root / "daily",
                checked_at=CHECKED_AT,
                list_fetcher=lambda _category: listing_html(),
                history_fetcher=lambda _category: self.fail(
                    "out-of-window recovery must not fetch pastweek"
                ),
                metadata_fetcher=lambda _ids: {},
                analyzer=None,
                sleep_fn=lambda _delay: None,
            )
            self.assertEqual(report["status"], pipeline.UPDATE_NOT_CONFIRMED)
            pending = pipeline.load_state(state_path)
            self.assertEqual(pending["pendingBatchDate"], "2026-08-19")
            self.assertIsNone(pending["lastCompletedBatchDate"])

    def test_offline_keeps_pending_and_increments_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def offline(_category):
                raise urllib.error.URLError("private detail")

            report = pipeline.run_daily(
                config(),
                state_path=root / "state.json",
                output_dir=root / "daily",
                checked_at=CHECKED_AT,
                list_fetcher=offline,
                analyzer=None,
                sleep_fn=lambda _delay: None,
            )
            self.assertEqual(report["status"], pipeline.UPDATER_OFFLINE)
            self.assertNotIn("private detail", report["message"])
            state = pipeline.load_state(root / "state.json")
            self.assertEqual(state["pendingBatchDate"], "2026-08-28")
            self.assertEqual(state["retryCount"], 1)

    def test_client_initialization_failure_persists_sanitized_pending_report_and_state(self):
        arxiv_id = "2608.10001"
        sensitive_detail = "malformed OPENAI_API_KEY sk-private-value"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_path = root / "state.json"
            output_dir = root / "daily"
            with mock.patch.object(
                pipeline.ResponsesAnalyzer,
                "__init__",
                side_effect=ValueError(sensitive_detail),
            ):
                report = pipeline.run_daily(
                    config(),
                    state_path=state_path,
                    output_dir=output_dir,
                    checked_at=CHECKED_AT,
                    list_fetcher=lambda _category: listing_html(new=(arxiv_id,)),
                    metadata_fetcher=lambda _ids: {arxiv_id: entry(arxiv_id)},
                    analyzer=None,
                    sleep_fn=lambda _delay: None,
                )

            self.assertEqual(report["status"], pipeline.UPDATER_OFFLINE)
            self.assertEqual(report["papers"], [])
            self.assertNotIn(sensitive_detail, report["message"])
            state = pipeline.load_state(state_path)
            self.assertEqual(state["lastStatus"], pipeline.UPDATER_OFFLINE)
            self.assertEqual(state["pendingBatchDate"], "2026-08-28")
            self.assertEqual(state["retryCount"], 1)
            self.assertIsNone(state["lastCompletedBatchDate"])

            json_path = output_dir / "2026-08-28.json"
            markdown_path = output_dir / "2026-08-28.md"
            self.assertEqual(pipeline._read_persisted_report(json_path), report)
            self.assertTrue(markdown_path.exists())
            self.assertNotIn(sensitive_detail, json_path.read_text(encoding="utf-8"))
            self.assertNotIn(
                sensitive_detail, markdown_path.read_text(encoding="utf-8")
            )

    def test_atomic_state_and_reports_leave_no_temporary_files(self):
        temporary, root, state_path, output_dir, _report = self.run_in_temp(
            page=listing_html(), analyzer=None
        )
        self.addCleanup(temporary.cleanup)
        self.assertTrue(state_path.exists())
        self.assertEqual(list(root.rglob("*.tmp")), [])


class AggregateTests(unittest.TestCase):
    def make_daily(self, report_date, status, papers):
        return pipeline._report(
            report_kind=pipeline.DAILY,
            report_date=report_date,
            generated_at=datetime.combine(report_date, datetime.min.time(), tzinfo=UTC),
            status=status,
            message="Daily status.",
            expected_batch_date=report_date,
            observed_batch_date=(report_date if status == pipeline.UPDATE_CONFIRMED else None),
            period_start=None,
            period_end=None,
            papers=papers,
        )

    def test_aggregate_reuses_daily_json_without_arxiv_and_writes_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            daily_dir = root / "daily"
            source_paper = {
                "metadata": pipeline.metadata_from_entry(entry("2608.12345")),
                "finalAnalysis": analysis(importance=4),
            }
            period_start = date(2026, 8, 24)
            period_end = date(2026, 8, 30)
            cursor = period_start
            while cursor <= period_end:
                status = (
                    pipeline.UPDATE_CONFIRMED
                    if cursor == date(2026, 8, 28)
                    else pipeline.NO_RELEVANT_PAPERS
                )
                papers = [source_paper] if status == pipeline.UPDATE_CONFIRMED else []
                pipeline.persist_report(
                    self.make_daily(cursor, status, papers), daily_dir
                )
                cursor += timedelta(days=1)
            analyzer = FakeAnalyzer()
            report = pipeline.run_aggregate(
                config(),
                report_kind=pipeline.WEEKLY,
                period_start=period_start,
                period_end=period_end,
                daily_dir=daily_dir,
                output_dir=root / "weekly",
                generated_at=datetime(2026, 8, 30, 12, 0, tzinfo=UTC),
                analyzer=analyzer,
                sleep_fn=lambda _delay: None,
            )
            self.assertEqual(report["status"], pipeline.UPDATE_CONFIRMED)
            self.assertEqual(len(analyzer.synthesis_inputs), 1)
            passed_papers = analyzer.synthesis_inputs[0][0]
            self.assertEqual(passed_papers, [source_paper])
            self.assertTrue((root / "weekly" / "2026-08-30.json").exists())

    def test_incomplete_daily_coverage_is_not_mislabeled_no_relevant(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            daily_dir = root / "daily"
            pipeline.persist_report(
                self.make_daily(
                    date(2026, 8, 28), pipeline.UPDATE_NOT_CONFIRMED, []
                ),
                daily_dir,
            )
            report = pipeline.run_aggregate(
                config(),
                report_kind=pipeline.WEEKLY,
                period_start=date(2026, 8, 24),
                period_end=date(2026, 8, 30),
                daily_dir=daily_dir,
                output_dir=root / "weekly",
                generated_at=datetime(2026, 8, 30, 12, 0, tzinfo=UTC),
                analyzer=None,
                sleep_fn=lambda _delay: None,
            )
            self.assertEqual(report["status"], pipeline.UPDATE_NOT_CONFIRMED)
            self.assertIn("Coverage is incomplete", report["message"])

    def test_missing_announcement_weekday_marks_aggregate_incomplete(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            daily_dir = root / "daily"
            pipeline.persist_report(
                self.make_daily(
                    date(2026, 8, 28), pipeline.NO_RELEVANT_PAPERS, []
                ),
                daily_dir,
            )
            report = pipeline.run_aggregate(
                config(),
                report_kind=pipeline.WEEKLY,
                period_start=date(2026, 8, 27),
                period_end=date(2026, 8, 28),
                daily_dir=daily_dir,
                output_dir=root / "weekly",
                generated_at=datetime(2026, 8, 28, 12, 0, tzinfo=UTC),
                analyzer=None,
                sleep_fn=lambda _delay: None,
            )
            self.assertEqual(report["status"], pipeline.UPDATE_NOT_CONFIRMED)
            self.assertIn("2026-08-27", report["message"])
            self.assertNotIn("No relevant papers were available", report["message"])

    def test_absent_weekend_and_configured_holiday_do_not_make_coverage_incomplete(self):
        period_start = date(2026, 8, 24)
        period_end = date(2026, 8, 30)
        holiday = date(2026, 8, 26)
        stored_dates = (
            date(2026, 8, 24),
            date(2026, 8, 25),
            date(2026, 8, 27),
            date(2026, 8, 28),
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            daily_dir = root / "daily"
            for report_date in stored_dates:
                pipeline.persist_report(
                    self.make_daily(
                        report_date, pipeline.NO_RELEVANT_PAPERS, []
                    ),
                    daily_dir,
                )

            report = pipeline.run_aggregate(
                config(no_announcement_dates=(holiday,)),
                report_kind=pipeline.WEEKLY,
                period_start=period_start,
                period_end=period_end,
                daily_dir=daily_dir,
                output_dir=root / "weekly",
                generated_at=datetime(2026, 8, 30, 12, 0, tzinfo=UTC),
                analyzer=None,
                sleep_fn=lambda _delay: None,
            )

            self.assertEqual(report["status"], pipeline.NO_RELEVANT_PAPERS)
            self.assertNotIn("Coverage is incomplete", report["message"])

    def test_explicit_stored_weekend_failure_still_marks_coverage_incomplete(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            daily_dir = root / "daily"
            pipeline.persist_report(
                self.make_daily(
                    date(2026, 8, 28), pipeline.NO_RELEVANT_PAPERS, []
                ),
                daily_dir,
            )
            pipeline.persist_report(
                self.make_daily(
                    date(2026, 8, 29), pipeline.UPDATER_OFFLINE, []
                ),
                daily_dir,
            )

            report = pipeline.run_aggregate(
                config(),
                report_kind=pipeline.WEEKLY,
                period_start=date(2026, 8, 28),
                period_end=date(2026, 8, 30),
                daily_dir=daily_dir,
                output_dir=root / "weekly",
                generated_at=datetime(2026, 8, 30, 12, 0, tzinfo=UTC),
                analyzer=None,
                sleep_fn=lambda _delay: None,
            )

            self.assertEqual(report["status"], pipeline.UPDATE_NOT_CONFIRMED)
            self.assertIn(
                "1 of 2 stored daily report(s) were unconfirmed or offline",
                report["message"],
            )


class ConfigAndCliTests(unittest.TestCase):
    def test_defaults_and_canonical_env_names(self):
        with mock.patch.dict(
            os.environ,
            {
                "OPENAI_SCREENING_MODEL": "screen-env",
                "OPENAI_FULL_TEXT_MODEL": "full-env",
                "OPENAI_WEEKLY_MODEL": "weekly-env",
                "OPENAI_MONTHLY_MODEL": "monthly-env",
                "OPENAI_SCREENING_REASONING_EFFORT": "none",
                "OPENAI_PDF_DETAIL": "high",
                "OPENAI_RESPONSES_TIMEOUT_SECONDS": "90",
                "RESEARCH_DAILY_TIME_BUDGET_SECONDS": "1500",
                "OPENAI_SYNTHESIS_CHUNK_MAX_ITEMS": "12",
                "OPENAI_SYNTHESIS_CHUNK_MAX_BYTES": "180000",
            },
            clear=False,
        ):
            loaded = pipeline.load_pipeline_config(None)
        self.assertIn("q-fin.CP", loaded.categories)
        self.assertEqual(loaded.pdf_importance_threshold, 3)
        self.assertEqual(loaded.screen_model, "screen-env")
        self.assertEqual(loaded.full_model, "full-env")
        self.assertEqual(loaded.weekly_model, "weekly-env")
        self.assertEqual(loaded.monthly_model, "monthly-env")
        self.assertEqual(loaded.screen_reasoning_effort, "none")
        self.assertEqual(loaded.pdf_detail, "high")
        self.assertEqual(loaded.max_candidates, 100)
        self.assertEqual(loaded.retries, 3)
        self.assertEqual(loaded.openai_timeout, 90)
        self.assertEqual(loaded.daily_time_budget, 1500)
        self.assertEqual(loaded.synthesis_chunk_max_items, 12)
        self.assertEqual(loaded.synthesis_chunk_max_bytes, 180000)
        self.assertEqual(loaded.no_announcement_dates, ())

    def test_legacy_synthesis_env_is_fallback_and_period_env_wins(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "research.json"
            config_path.write_text(
                json.dumps(
                    {
                        "weeklyModel": "weekly-config",
                        "monthlyModel": "monthly-config",
                    }
                ),
                encoding="utf-8",
            )
            with mock.patch.dict(
                os.environ,
                {
                    "OPENAI_SYNTHESIS_MODEL": "legacy-env",
                    "OPENAI_WEEKLY_MODEL": "weekly-env",
                },
                clear=True,
            ):
                loaded = pipeline.load_pipeline_config(config_path)

        self.assertEqual(loaded.weekly_model, "weekly-env")
        self.assertEqual(loaded.monthly_model, "legacy-env")

    def test_invalid_pdf_detail_and_reasoning_types_are_configuration_errors(self):
        for bad_config in (
            {"pdfDetail": ["low"]},
            {"screenReasoningEffort": {"value": "low"}},
        ):
            with self.subTest(bad_config=bad_config):
                with tempfile.TemporaryDirectory() as directory:
                    config_path = Path(directory) / "research.json"
                    config_path.write_text(json.dumps(bad_config), encoding="utf-8")
                    with self.assertRaises(pipeline.ConfigurationError):
                        pipeline.load_pipeline_config(config_path)

    def test_invalid_resilience_and_chunk_limits_are_configuration_errors(self):
        for bad_config in (
            {"openaiTimeoutSeconds": 0},
            {"dailyTimeBudgetSeconds": 3000},
            {"synthesisChunkMaxItems": 0},
            {"synthesisChunkMaxBytes": 1000},
        ):
            with self.subTest(bad_config=bad_config):
                with tempfile.TemporaryDirectory() as directory:
                    config_path = Path(directory) / "research.json"
                    config_path.write_text(json.dumps(bad_config), encoding="utf-8")
                    with self.assertRaises(pipeline.ConfigurationError):
                        pipeline.load_pipeline_config(config_path)

    def test_configured_no_announcement_date_rolls_back_expected_batch(self):
        holiday = date(2026, 8, 31)
        checked_at = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)
        self.assertEqual(
            pipeline.expected_batch_date(checked_at, (holiday,)),
            date(2026, 8, 28),
        )
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "research.json"
            config_path.write_text(
                json.dumps({"noAnnouncementDates": [holiday.isoformat()]}),
                encoding="utf-8",
            )
            loaded = pipeline.load_pipeline_config(config_path)
        self.assertEqual(loaded.no_announcement_dates, (holiday,))

    def test_no_announcement_dates_reject_invalid_or_duplicate_dates(self):
        for values in (["not-a-date"], ["2026-08-31", "2026-08-31"]):
            with self.subTest(values=values), tempfile.TemporaryDirectory() as directory:
                config_path = Path(directory) / "research.json"
                config_path.write_text(
                    json.dumps({"noAnnouncementDates": values}), encoding="utf-8"
                )
                with self.assertRaises(pipeline.ConfigurationError):
                    pipeline.load_pipeline_config(config_path)

    def test_cli_has_daily_and_aggregate_subcommands(self):
        parser = pipeline.build_argument_parser()
        daily = parser.parse_args(["daily"])
        aggregate = parser.parse_args(
            ["aggregate", "--period", "weekly", "--period-end", "2026-08-30"]
        )
        self.assertEqual(daily.command, "daily")
        self.assertEqual(aggregate.command, "aggregate")
        self.assertEqual(aggregate.period_end, date(2026, 8, 30))


if __name__ == "__main__":
    unittest.main()
