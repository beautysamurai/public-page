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

    def test_paid_period_call_reuses_daily_json_and_carries_incomplete_reviews(self) -> None:
        targets = self.step("Plan period review targets")
        self.assertIn("report_to_markdown", targets)
        self.assertIn("pending[:2]", targets)
        self.assertIn("candidates = [current_end, *pending[:2]]", targets)
        self.assertIn("if explicit_end:", targets)
        self.assertIn('Path("research/pending-periods")', targets)
        self.assertIn('"schemaVersion": 1', targets)
        self.assertNotIn("OPENAI_API_KEY", targets)

        marker_commit = self.step("Persist period retry markers")
        self.assertIn("id: period_marker_commit", marker_commit)
        self.assertIn("git add -A -- research/pending-periods", marker_commit)
        self.assertIn("reject_duplicate_keys", marker_commit)
        self.assertIn("marker_path.stat().st_size > 4096", marker_commit)
        self.assertIn("set(marker) != fields", marker_commit)
        self.assertIn("period marker identity mismatch", marker_commit)
        self.assertNotIn("OPENAI_API_KEY", marker_commit)
        self.assertIn('git push origin "HEAD:$AUTOMATION_BRANCH"', marker_commit)

        aggregate = self.step("Run weekly or monthly reviews")
        self.assertIn("steps.period_targets.outputs.count != '0'", aggregate)
        self.assertIn("OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}", aggregate)
        self.assertIn('done < "$targets_path"', aggregate)
        self.assertIn('echo "failed=$failed"', aggregate)
        self.assertIn("terminal_statuses", aggregate)
        self.assertIn('if persisted["status"] in terminal_statuses:', aggregate)
        self.assertIn("marker_path.unlink()", aggregate)
        self.assertGreater(
            aggregate.index("marker_path.unlink()"),
            aggregate.index('if persisted["status"] in terminal_statuses:'),
        )
        self.assertIn("keeping retry marker for incomplete", aggregate)
        self.assertIn("remains queued", aggregate)
        self.assertNotIn("persist_report", aggregate)
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

    def test_period_failures_are_reported_after_persistence_and_pr_update(self) -> None:
        marker_position = self.workflow.index(
            "      - name: Persist period retry markers\n"
        )
        aggregate_position = self.workflow.index(
            "      - name: Run weekly or monthly reviews\n"
        )
        commit_position = self.workflow.index(
            "      - name: Persist research state and report\n"
        )
        pull_request_position = self.workflow.index(
            "      - name: Open or update the review pull request\n"
        )
        failure_position = self.workflow.index(
            "      - name: Report period review failures\n"
        )
        self.assertLess(marker_position, aggregate_position)
        self.assertLess(aggregate_position, commit_position)
        self.assertLess(commit_position, pull_request_position)
        pull_request = self.step("Open or update the review pull request")
        self.assertIn("steps.period_marker_commit.outputs.changed == 'true'", pull_request)
        self.assertLess(pull_request_position, failure_position)
        failure = self.step("Report period review failures")
        self.assertIn("steps.aggregate.outputs.failed == 'true'", failure)
        self.assertIn("exit 1", failure)

    def test_reconciliation_repairs_all_completed_period_reports(self) -> None:
        publication = self.step("Reconcile completed research reports")
        self.assertIn('for kind in ("weekly", "monthly")', publication)
        self.assertIn('"--report"', publication)
        self.assertIn('incomplete = {"UPDATE_NOT_CONFIRMED", "UPDATER_OFFLINE"}', publication)
        self.assertIn("--daily-report-dir research/daily", publication)
        self.assertIn("--regenerate-site", publication)


if __name__ == "__main__":
    unittest.main()
