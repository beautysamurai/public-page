import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import research_publication as publication
from test_research_publication import completed_report


class ResearchLayoutTests(unittest.TestCase):
    def test_three_parts_preserve_all_narratives_and_public_identity(self):
        report = completed_report()
        before = copy.deepcopy(report)
        for english in (False, True):
            result = publication.adapt_research_report(report)
            edition = result.english_edition if english else result.source_edition
            headings = ["Overview", "Assessment", "Takeaways"] if english else ["概要", "判定結果", "まとめ"]
            positions = [edition["sourceText"].index("### " + label) for label in headings]
            self.assertEqual(positions, sorted(positions))
            analysis = report["papers"][0]["finalAnalysis"]
            if english:
                analysis = analysis["english"]
            for field in ("summary", "mainResult", "methodology", "practicalApplication", "limitations", "reason"):
                self.assertIn(publication._markdown_text(analysis[field]), edition["sourceText"])
        self.assertEqual(report, before)

    def test_recorded_usage_is_formatted_not_repriced_or_inferred(self):
        daily = json.loads((ROOT / "research/daily/2026-09-07.json").read_text(encoding="utf-8"))
        calls = copy.deepcopy(daily["papers"][0]["usage"])
        for english in (False, True):
            table = publication._usage_table(calls, english=english)
            self.assertNotIn("$", table)
            self.assertIn("USD", table)
            for c in calls:
                self.assertIn(f"{c['estimatedCostUsd']:.4f}", table)
                self.assertIn(f"{c['inputTokens']:,}", table)
            missing = copy.deepcopy(calls)
            for c in missing:
                c.update(model=None, inputTokens=None, outputTokens=None, reasoningTokens=None, totalTokens=None, cachedInputTokens=None, cacheWriteTokens=None, estimatedCostUsd=None)
            unknown = publication._usage_table(missing, english=english)
            self.assertIn("Not recorded" if english else "未記録", unknown)
            self.assertNotIn("0.0000", unknown)
        self.assertEqual(calls, daily["papers"][0]["usage"])

    def test_weekly_overview_uses_report_usage_once_not_twelve_times(self):
        report = json.loads((ROOT / "research/reviews/weekly/2026-09-04.json").read_text(encoding="utf-8"))
        overview = publication._usage_table(report["usage"], english=True, aggregate=True)
        self.assertEqual(overview.count("23,080"), 1)
        self.assertIn("| 1 |", overview)
        self.assertIn("shared across 12 papers", overview)


if __name__ == "__main__":
    unittest.main()
