"""Token-saving requests must retain evidence, output contracts and old records."""
import copy
import json
import os
import tempfile
import unittest
from dataclasses import replace
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from tests.test_research_pipeline import pipeline as p, config, entry, analysis, RecordingResponses


class ResearchEfficiencyTests(unittest.TestCase):
    def test_active_defaults_agree_and_keep_reasoning_for_research(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            configs = [p.PipelineConfig(), p.load_pipeline_config(None),
                       p.load_pipeline_config(Path(__file__).resolve().parents[1] / "config/research.json")]
        for cfg in configs:
            self.assertEqual((cfg.screen_model, cfg.screen_reasoning_effort), ("gpt-6-luna", "low"))
            self.assertEqual((cfg.full_model, cfg.full_reasoning_effort), ("gpt-6.1-sol", "medium"))
            self.assertEqual((cfg.weekly_model, cfg.weekly_reasoning_effort), ("gpt-6.1-sol", "medium"))
            self.assertEqual((cfg.monthly_model, cfg.monthly_reasoning_effort), ("gpt-6-astra", "high"))
            self.assertEqual(cfg.text_verbosity, "low")
            self.assertEqual(cfg.pdf_importance_threshold, 3)
            self.assertEqual(cfg.pdf_detail, "low")

    def test_verbosity_override_and_invalid_values(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text('{"textVerbosity":"high"}', encoding="utf-8")
            with mock.patch.dict(os.environ, {}, clear=True):
                self.assertEqual(p.load_pipeline_config(path).text_verbosity, "high")
            with mock.patch.dict(os.environ, {"OPENAI_TEXT_VERBOSITY": "medium"}, clear=True):
                self.assertEqual(p.load_pipeline_config(path).text_verbosity, "medium")
            for bad in (None, True, [], "", "minimal"):
                with self.subTest(bad=bad), self.assertRaises(p.ConfigurationError):
                    p.PipelineConfig(text_verbosity=bad)
            path.write_text('{"textVerbosity":null}', encoding="utf-8")
            with mock.patch.dict(os.environ, {}, clear=True), self.assertRaises(p.ConfigurationError):
                p.load_pipeline_config(path)
            with mock.patch.dict(os.environ, {"OPENAI_TEXT_VERBOSITY": "invalid"}, clear=True), self.assertRaises(p.ConfigurationError):
                p.load_pipeline_config(None)

    def test_compact_synthesis_retains_all_primary_evidence_without_mutation(self):
        primary = analysis(importance=4)
        primary["limitations"] = "50ページ超のため抄録とIntroductionのみ。実験全体は未検証です。"
        original = [{"metadata": p.metadata_from_entry(entry("2608.12345")), "finalAnalysis": primary}]
        before = copy.deepcopy(original)
        for kind in (p.WEEKLY, p.MONTHLY):
            prompt = p._synthesis_prompt(original, kind, date(2026, 8, 1), date(2026, 8, 28))
            source = json.loads(prompt[len(p._SYNTHESIS_PROMPT_PREFIX):])
            sent = source["papers"][0]
            self.assertEqual(sent["metadata"], before[0]["metadata"])
            self.assertEqual(sent["finalAnalysis"], {k: v for k, v in primary.items() if k != "english"})
            self.assertEqual(sent["sourceCoverage"], [{"scope": "unknown", "pdfPages": None}])
            self.assertNotIn("english", sent["finalAnalysis"])
            self.assertEqual(original, before)
            bilingual = copy.deepcopy(source)
            bilingual["papers"][0]["finalAnalysis"] = primary
            self.assertLess(len(json.dumps(source).encode()), len(json.dumps(bilingual).encode()))

    def test_compact_requests_preserve_strict_bilingual_schema_and_caps(self):
        responses = RecordingResponses([analysis(importance=4), {"papers": []}])
        adapter = p.ResponsesAnalyzer(config(text_verbosity="low"), SimpleNamespace(responses=responses))
        candidate = p.PaperCandidate(entry("2608.12345"), ("new",), ("q-fin.TR",))
        adapter.analyze_abstract(candidate)
        adapter.synthesize([], p.WEEKLY, date(2026, 8, 1), date(2026, 8, 28))
        for call in responses.calls:
            self.assertEqual(call["text"]["verbosity"], "low")
            self.assertTrue(call["text"]["format"]["strict"])
            self.assertEqual(call["truncation"], "disabled")
            self.assertFalse(call["store"])
            self.assertGreaterEqual(call["max_output_tokens"], p.ABSTRACT_MAX_OUTPUT_TOKENS)
        schema = responses.calls[0]["text"]["format"]["schema"]
        self.assertIn("english", schema["required"])
        self.assertIn("limitations", schema["required"])
        self.assertIn("methodology", schema["required"])
        prompt = responses.calls[0]["input"][0]["content"][0]["text"]
        payload = json.loads(prompt[len(p._ABSTRACT_PROMPT_PREFIX):])
        self.assertEqual(payload["abstract"], candidate.entry.abstract)

    def test_verbosity_changes_request_fingerprint(self):
        cfg = config()
        self.assertNotEqual(p._checkpoint_fingerprint(cfg, date(2026, 8, 28), {}),
                            p._checkpoint_fingerprint(replace(cfg, text_verbosity="high"), date(2026, 8, 28), {}))

    def test_new_model_prices_keep_usage_and_legacy_rates_intact(self):
        for model, expected in (("gpt-6-luna", .000241), ("gpt-6.1-sol", .00481),
                                ("gpt-5.6-sol", .00964)):
            response = {"model": model, "status": "completed", "usage": {
                "input_tokens": 1000, "output_tokens": 300, "total_tokens": 1300,
                "input_tokens_details": {"cached_tokens": 100, "cache_write_tokens": 0},
                "output_tokens_details": {"reasoning_tokens": 100}}}
            call = p.research_usage.record(response, model=model, effort="medium", stage="paper",
                                           paper_ids=["2608.12345v1"], scope="full_text", pages=20)
            self.assertAlmostEqual(call["estimatedCostUsd"], expected)
            self.assertEqual(call["totalTokens"], 1300)
            self.assertEqual(call["reasoningTokens"], 100)
            self.assertEqual(call["pricingDate"], "2026-09-06" if model == "gpt-5.6-sol" else "2026-10-03")


if __name__ == "__main__":
    unittest.main()
