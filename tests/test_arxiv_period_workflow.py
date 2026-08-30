from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "arxiv-research.yml"


class ArxivPeriodWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow = WORKFLOW_PATH.read_text(encoding="utf-8")

    def step(self, name: str) -> str:
        marker = f"      - name: {name}\n"
        start = self.workflow.index(marker)
        end = self.workflow.find("\n      - name:", start + len(marker))
        if end == -1:
            end = len(self.workflow)
        return self.workflow[start:end]

    def test_all_schedules_use_jst_and_one_shared_queue(self) -> None:
        self.assertIn('- cron: "30 15 * * *"\n      timezone: "Asia/Tokyo"', self.workflow)
        self.assertIn('- cron: "0 8 * * 0"\n      timezone: "Asia/Tokyo"', self.workflow)
        self.assertIn('- cron: "0 8 1 * *"\n      timezone: "Asia/Tokyo"', self.workflow)
        self.assertIn("group: automated-arxiv-research\n  queue: max", self.workflow)
        self.assertNotIn("cancel-in-progress:", self.workflow)

    def test_manual_dispatch_selects_mode_and_recovery_date(self) -> None:
        self.assertIn("run_mode:", self.workflow)
        self.assertIn("period_end:", self.workflow)
        planner = self.step("Plan review mode")
        self.assertIn('"0 8 * * 0": "weekly"', planner)
        self.assertIn('"0 8 1 * *": "monthly"', planner)
        self.assertIn("weekly period_end must be a Friday", planner)
        self.assertIn("monthly period_end must be the final day of a month", planner)

    def test_paid_period_call_reuses_daily_json_and_skips_completed_review(self) -> None:
        inspect = self.step("Inspect existing period review")
        self.assertIn('echo "complete=$complete"', inspect)
        self.assertIn("report_to_markdown", inspect)
        self.assertNotIn("OPENAI_API_KEY", inspect)

        aggregate = self.step("Run weekly or monthly review")
        self.assertIn("steps.period_review.outputs.complete != 'true'", aggregate)
        self.assertIn("OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}", aggregate)
        self.assertIn("aggregate \\", aggregate)
        self.assertIn("--daily-dir research/daily", aggregate)
        self.assertIn("--output-dir research/reviews", aggregate)
        self.assertNotIn("arxiv.org", aggregate)

    def test_daily_and_period_api_keys_are_scoped_to_paid_steps(self) -> None:
        self.assertEqual(self.workflow.count("OPENAI_API_KEY:"), 2)
        self.assertIn(
            "if: steps.plan.outputs.mode == 'daily'",
            self.step("Run daily research"),
        )
        self.assertNotIn(
            "OPENAI_API_KEY",
            self.step("Preflight before paid research"),
        )

    def test_reconciliation_repairs_all_completed_period_reports(self) -> None:
        publication = self.step("Reconcile completed research reports")
        self.assertIn('for kind in ("weekly", "monthly")', publication)
        self.assertIn('"--report"', publication)
        self.assertIn('incomplete = {"UPDATE_NOT_CONFIRMED", "UPDATER_OFFLINE"}', publication)
        self.assertIn("--daily-report-dir research/daily", publication)
        self.assertIn("--regenerate-site", publication)


if __name__ == "__main__":
    unittest.main()
