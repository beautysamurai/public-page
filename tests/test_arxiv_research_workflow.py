from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "arxiv-research.yml"


class ArxivResearchWorkflowTests(unittest.TestCase):
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

    def position(self, name: str) -> int:
        return self.workflow.index(f"      - name: {name}\n")

    def test_complete_preflight_precedes_paid_daily_run(self) -> None:
        preflight = self.step("Preflight before paid research")
        self.assertIn("python -m unittest discover -s tests -v", preflight)
        self.assertIn("node --test tests/test_model_math.cjs", preflight)
        self.assertIn("python scripts/import_scheduler_history.py --check", preflight)
        self.assertIn("git diff --check", preflight)
        self.assertLess(
            self.position("Preflight before paid research"),
            self.position("Run daily research"),
        )

    def test_shared_language_validator_is_protected_as_executable_code(self) -> None:
        verification = self.step("Verify the automation branch is data-only")
        self.assertIn("scripts/research_language.py", verification)
        self.assertIn('git ls-tree origin/main -- "$path"', verification)

    def test_daily_run_only_generates_research_and_classifies_completion(self) -> None:
        daily = self.step("Run daily research")
        self.assertIn("python scripts/research_pipeline.py", daily)
        self.assertNotIn("research_publication.py", daily)
        self.assertNotIn("git add", daily)
        self.assertIn("UPDATE_CONFIRMED|NO_RELEVANT_PAPERS|NO_NEW_BATCH_EXPECTED", daily)
        self.assertIn("UPDATE_NOT_CONFIRMED|UPDATER_OFFLINE", daily)
        self.assertIn('echo "report_path=$report_path"', daily)
        self.assertIn('echo "publishable=$publishable"', daily)

    def test_research_is_scanned_and_pushed_before_publication(self) -> None:
        expected_order = [
            "Run daily research",
            "Validate generated research boundary",
            "Persist research state and report",
            "Publish completed research report",
        ]
        positions = [self.position(name) for name in expected_order]
        self.assertEqual(positions, sorted(positions))

        boundary = self.step("Validate generated research boundary")
        self.assertIn('posix.parts[0] != "research"', boundary)
        self.assertIn("secret_patterns", boundary)
        self.assertIn("32 * 1024 * 1024", boundary)
        self.assertIn('"--exclude-standard", "--", "research"', boundary)
        self.assertIn('f"HEAD:{name}"', boundary)

        commit = self.step("Persist research state and report")
        self.assertIn("id: research_commit", commit)
        self.assertIn("git add -- research", commit)
        self.assertNotIn("content/chatgpt_scheduler_history.json", commit)
        self.assertNotIn("site/data", commit)
        self.assertIn('git push origin "HEAD:$AUTOMATION_BRANCH"', commit)

    def test_only_completed_reports_reach_publication(self) -> None:
        publication = self.step("Publish completed research report")
        self.assertIn("if: steps.research.outputs.publishable == 'true'", publication)
        self.assertIn("python scripts/research_publication.py", publication)
        self.assertIn('${{ steps.research.outputs.report_path }}', publication)

    def test_publication_is_fully_validated_and_committed_separately(self) -> None:
        expected_order = [
            "Publish completed research report",
            "Validate generated publication and change scope",
            "Reject private state and likely secrets before publication commit",
            "Commit generated publication",
        ]
        positions = [self.position(name) for name in expected_order]
        self.assertEqual(positions, sorted(positions))

        validation = self.step("Validate generated publication and change scope")
        self.assertIn("python -m unittest discover -s tests -v", validation)
        self.assertIn("node --test tests/test_model_math.cjs", validation)
        self.assertIn("python scripts/import_scheduler_history.py --check", validation)
        self.assertIn("git diff --check", validation)
        self.assertIn('name == "content/chatgpt_scheduler_history.json"', validation)
        self.assertIn('posix.parts[:2] == ("site", "data")', validation)

        privacy = self.step(
            "Reject private state and likely secrets before publication commit"
        )
        self.assertIn('"git", "ls-files", "-z", "--cached", "--others"', privacy)
        self.assertIn("private key block", privacy)
        self.assertIn("GitHub token", privacy)
        self.assertIn("AWS access key", privacy)
        self.assertIn("OpenAI-style key", privacy)

        commit = self.step("Commit generated publication")
        self.assertIn("id: publication_commit", commit)
        self.assertIn(
            "git add -- content/chatgpt_scheduler_history.json site/data", commit
        )
        self.assertNotIn("git add -- research", commit)
        self.assertIn('git push origin "HEAD:$AUTOMATION_BRANCH"', commit)

    def test_pr_runs_for_either_commit_even_after_a_later_failure(self) -> None:
        pull_request = self.step("Open or update the review pull request")
        self.assertIn("if: always()", pull_request)
        self.assertIn("steps.research_commit.outputs.changed == 'true'", pull_request)
        self.assertIn("steps.publication_commit.outputs.changed == 'true'", pull_request)


if __name__ == "__main__":
    unittest.main()
