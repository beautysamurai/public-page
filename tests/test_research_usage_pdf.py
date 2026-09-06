import io
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from scripts import research_pipeline as p
from scripts import research_pdf as pdf
from scripts import research_usage as usage
from tests.test_research_pipeline import config, entry, analysis, RecordingResponses, CHECKED_AT, listing_html


class ResearchUsagePdfTests(unittest.TestCase):
    def response(self, **changes):
        value = dict(model="gpt-5.6-sol", status="completed", output_text=json.dumps(analysis(importance=4)),
                     usage=SimpleNamespace(input_tokens=1000, output_tokens=300, total_tokens=1300,
                         input_tokens_details=SimpleNamespace(cached_tokens=100, cache_write_tokens=0),
                         output_tokens_details=SimpleNamespace(reasoning_tokens=100)))
        value.update(changes)
        return SimpleNamespace(**value)

    def test_actual_response_usage_is_not_invented_and_reasoning_is_not_double_counted(self):
        c = usage.record(self.response(), model="gpt-5.6-sol", effort="medium", stage="paper",
                         paper_ids=["2608.12345v1"], scope="full_text", pages=20)
        self.assertEqual(c["totalTokens"], 1300)
        self.assertEqual(c["reasoningTokens"], 100)
        self.assertAlmostEqual(c["estimatedCostUsd"], .00964)
        empty = usage.record(None, model="gpt-5.6-sol", effort="medium", stage="paper",
                             paper_ids=["2608.12345v1"], scope="full_text", pages=20)
        self.assertIsNone(empty["inputTokens"])
        self.assertIsNone(empty["model"])
        self.assertIsNone(empty["estimatedCostUsd"])
        with self.assertRaises(ValueError):
            usage.validate([{**c, "secret": "never publish"}])
        with self.assertRaises(ValueError):
            usage.validate([{**c, "totalTokens": 1400}])

    def test_51_pages_sends_only_abstract_and_intro_and_discloses_source_scope(self):
        responses = SimpleNamespace(create=mock.Mock(return_value=self.response()))
        adapter = p.ResponsesAnalyzer(config(), SimpleNamespace(responses=responses))
        candidate = p.PaperCandidate(entry("2608.12345"), ("new",), ("q-fin.TR",))
        with mock.patch.object(p, "fetch_pdf_for_inline_input", return_value=b"%PDF-secret-full-text"), mock.patch.object(p, "inspect_pdf", return_value=(51, "Introduction evidence only")):
            adapter.analyze_pdf(candidate)
        payload = responses.create.call_args.kwargs
        serialized = json.dumps(payload)
        self.assertNotIn("input_file", serialized)
        self.assertNotIn("file_data", serialized)
        self.assertNotIn("secret-full-text", serialized)
        self.assertIn("Introduction evidence only", serialized)
        self.assertIn(candidate.entry.abstract, serialized)
        self.assertIn("full paper was NOT", serialized)
        self.assertEqual(adapter.usage_calls[0]["sourceScope"], "abstract_introduction")
        self.assertIn("50ページ超", usage.describe(adapter.usage_calls))

    def test_pdf_inspection_failure_cannot_send_any_paid_input(self):
        responses = SimpleNamespace(create=mock.Mock())
        adapter = p.ResponsesAnalyzer(config(), SimpleNamespace(responses=responses))
        with mock.patch.object(p, "fetch_pdf_for_inline_input", return_value=b"%PDF-broken"), mock.patch.object(p, "inspect_pdf", side_effect=p.PdfInspectionError("not found")):
            with self.assertRaises(p.PdfInspectionError):
                adapter.analyze_pdf(p.PaperCandidate(entry("2608.12345"), ("new",), ("q-fin.TR",)))
        responses.create.assert_not_called()

    def test_real_pdf_page_count_50_51_and_malformed_pdf(self):
        from pypdf import PdfWriter
        for n in (50, 51):
            writer = PdfWriter()
            for _ in range(n):
                writer.add_blank_page(width=100, height=100)
            stream = io.BytesIO()
            writer.write(stream)
            if n == 50:
                self.assertEqual(pdf.inspect_pdf(stream.getvalue()), (50, None))
            else:
                with self.assertRaises(pdf.PdfInspectionError):
                    pdf.inspect_pdf(stream.getvalue())
        with self.assertRaises(pdf.PdfInspectionError):
            pdf.inspect_pdf(b"not a PDF")

    def test_introduction_has_definite_boundaries(self):
        content = "Evidence and motivation. " * 15
        for title, end in [("1 Introduction", "2 Model"), ("I. INTRODUCTION", "II. RESULTS"), ("Introduction", "Background")]:
            self.assertEqual(pdf.extract_introduction("Abstract\nnot sent\n" + title + "\n" + content + "\n" + end + "\nDO NOT SEND"), content.strip())
        for text in ("No introduction", "1 Introduction\n" + content):
            with self.assertRaises(pdf.PdfInspectionError):
                pdf.extract_introduction(text)

    def test_daily_provenance_survives_checkpoint_and_completed_reuse(self):
        responses = SimpleNamespace(create=mock.Mock(side_effect=[self.response(model="gpt-5.6-luna"), self.response()]))
        adapter = p.ResponsesAnalyzer(config(), SimpleNamespace(responses=responses))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args = dict(state_path=root / "state.json", output_dir=root / "daily", checked_at=CHECKED_AT,
                        list_fetcher=lambda _: listing_html(new=("2608.12345",)),
                        metadata_fetcher=lambda ids: {k: entry(k) for k in ids}, analyzer=adapter)
            with mock.patch.object(p, "fetch_pdf_for_inline_input", return_value=b"%PDF-fixture"), mock.patch.object(p, "inspect_pdf", return_value=(51, "Only introduction")):
                report = p.run_daily(config(), **args)
                again = p.run_daily(config(), **args)
            self.assertEqual(report, again)
            self.assertEqual(len(report["usage"]), 2)
            self.assertEqual(report["papers"][0]["usage"], report["usage"])
            self.assertEqual(responses.create.call_count, 2)
            self.assertIn("50ページ超", (root / "daily/2026-08-28.md").read_text(encoding="utf-8"))
            from scripts import research_publication as publish
            normalized = publish.validate_research_report(report)
            self.assertEqual(normalized["usage"], report["usage"])
            self.assertIn("50ページ超", publish._render_source_text(normalized, english=False, public_status="UPDATE_CONFIRMED"))

    def test_period_usage_is_shared_and_keeps_daily_limited_source(self):
        c = usage.record(self.response(), model="gpt-5.6-sol", effort="medium", stage="paper",
                         paper_ids=["2608.12345v1"], scope="abstract_introduction", pages=80)
        paper = {"metadata": p.metadata_from_entry(entry("2608.12345")), "finalAnalysis": analysis(importance=4), "usage": [c]}
        report = p._report(report_kind=p.DAILY, report_date=CHECKED_AT.date(), generated_at=CHECKED_AT,
                           status=p.UPDATE_CONFIRMED, message="Confirmed.", expected_batch_date=CHECKED_AT.date(),
                           observed_batch_date=CHECKED_AT.date(), period_start=None, period_end=None, papers=[paper], usage=[c])
        responses = SimpleNamespace(create=mock.Mock(return_value=self.response(model="gpt-6-astra",
            output_text=json.dumps({"papers": [{"arxivId": "2608.12345v1", "finalAnalysis": analysis(importance=4)}]}))))
        adapter = p.ResponsesAnalyzer(config(), SimpleNamespace(responses=responses))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            p.persist_report(report, root / "daily")
            result = p.run_aggregate(config(), report_kind=p.WEEKLY, period_start=CHECKED_AT.date(), period_end=CHECKED_AT.date(),
                daily_dir=root / "daily", output_dir=root / "weekly", generated_at=CHECKED_AT, analyzer=adapter)
        self.assertEqual(len(result["usage"]), 1, "period total does not rebill daily usage")
        self.assertEqual(result["usage"][0]["model"], "gpt-6-astra")
        self.assertEqual(len(result["papers"][0]["usage"]), 2)
        prompt = responses.create.call_args.kwargs["input"][0]["content"][0]["text"]
        self.assertIn('"scope":"abstract_introduction"', prompt)
        self.assertIn("共有", usage.describe(result["usage"]))
        self.assertIn("80ページ", usage.describe(result["papers"][0]["usage"]))

    def test_usage_for_invalid_output_is_checkpointed_before_retry(self):
        responses = SimpleNamespace(create=mock.Mock(side_effect=[self.response(), self.response(output_text="invalid JSON")]))
        adapter = p.ResponsesAnalyzer(config(), SimpleNamespace(responses=responses))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args = dict(state_path=root / "state.json", output_dir=root / "daily", checked_at=CHECKED_AT,
                        list_fetcher=lambda _: listing_html(new=("2608.12345",)),
                        metadata_fetcher=lambda ids: {k: entry(k) for k in ids})
            with mock.patch.object(p, "fetch_pdf_for_inline_input", return_value=b"%PDF-fixture"), mock.patch.object(p, "inspect_pdf", return_value=(50, None)):
                failed = p.run_daily(config(), **args, analyzer=adapter)
                checkpoint = json.loads((root / "checkpoints/2026-08-28.json").read_text(encoding="utf-8"))
                self.assertEqual(failed["status"], p.UPDATE_NOT_CONFIRMED)
                self.assertEqual(len(checkpoint["usage"]), 2)
                self.assertEqual(failed["usage"], checkpoint["usage"])
                failed_adapter = p.ResponsesAnalyzer(config(), SimpleNamespace(responses=SimpleNamespace(
                    create=mock.Mock(return_value=self.response(output_text="invalid JSON")))))
                failed_again = p.run_daily(config(), **args, analyzer=failed_adapter)
                self.assertEqual(failed_again["status"], p.UPDATE_NOT_CONFIRMED)
                self.assertEqual(len(failed_again["usage"]), 3)
                persisted = json.loads((root / "daily/2026-08-28.json").read_text(encoding="utf-8"))
                self.assertEqual(persisted["usage"], failed_again["usage"])
                self.assertEqual((root / "daily/2026-08-28.md").read_text(encoding="utf-8"),
                                 p.report_to_markdown(failed_again))
                self.assertIn("合計 2,600", p.report_to_markdown(failed_again))
                adapter2 = p.ResponsesAnalyzer(config(), SimpleNamespace(responses=SimpleNamespace(create=mock.Mock(return_value=self.response()))))
                completed = p.run_daily(config(), **args, analyzer=adapter2)
            self.assertEqual(len(completed["usage"]), 4)
            self.assertEqual(completed["status"], p.UPDATE_CONFIRMED)

    def test_synthesis_preserves_abstract_only_and_unknown_legacy_coverage(self):
        c = usage.record(self.response(model="gpt-5.6-luna"), model="gpt-5.6-luna", effort="low",
                         stage="screen", paper_ids=["2608.12345v1"], scope="abstract", pages=None)
        paper = {"metadata": p.metadata_from_entry(entry("2608.12345")), "finalAnalysis": analysis(importance=2), "usage": [c, c]}
        self.assertEqual(p._source_coverage(paper), [{"scope": "abstract", "pdfPages": None}])
        for kind in (p.WEEKLY, p.MONTHLY):
            prompt = p._synthesis_prompt([paper], kind, CHECKED_AT.date(), CHECKED_AT.date())
            self.assertIn('"sourceCoverage":[{"scope":"abstract","pdfPages":null}]', prompt)
            self.assertIn("abstract means only the abstract", prompt)
        paper.pop("usage")
        self.assertEqual(p._source_coverage(paper), [{"scope": "unknown", "pdfPages": None}])
        self.assertIn('"scope":"unknown"', p._synthesis_prompt([paper], p.WEEKLY, CHECKED_AT.date(), CHECKED_AT.date()))

    def test_pending_usage_enrichment_does_not_replace_completed_or_conflicting_usage(self):
        c = usage.record(self.response(), model="gpt-5.6-sol", effort="medium", stage="paper",
                         paper_ids=["2608.12345v1"], scope="full_text", pages=20)
        report = p._report(report_kind=p.DAILY, report_date=CHECKED_AT.date(), generated_at=CHECKED_AT,
                           status=p.UPDATE_NOT_CONFIRMED, message="Pending.", expected_batch_date=CHECKED_AT.date(),
                           observed_batch_date=CHECKED_AT.date(), period_start=None, period_end=None, papers=[])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            p.persist_report(report, root)
            enriched = p.persist_report({**report, "usage": [c]}, root)
            self.assertEqual(enriched["usage"], [c])
            conflicting = {**report, "usage": [{**c, "effort": "high"}, c]}
            self.assertEqual(p.persist_report(conflicting, root), enriched)
            completed = p.persist_report({**enriched, "status": p.NO_RELEVANT_PAPERS}, root)
            self.assertEqual(p.persist_report({**completed, "usage": [c, c]}, root), completed)
