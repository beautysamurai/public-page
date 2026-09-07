from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import textwrap
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
        self.assertIn("tests/test_archive_ui.cjs", preflight)
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

    def test_durable_branch_configures_bot_identity_before_merging_main(self) -> None:
        continuation = self.step("Continue the durable automation branch")
        self.assertIn('git config user.name "github-actions[bot]"', continuation)
        self.assertIn(
            'git config user.email "41898282+github-actions[bot]@users.noreply.github.com"',
            continuation,
        )
        self.assertLess(
            continuation.index("git config user.name"),
            continuation.index("git merge --no-edit"),
        )

    def test_generated_commits_opt_out_of_duplicate_ci_only(self) -> None:
        commits = re.findall(r'^\s+git commit .*$', self.workflow, re.MULTILINE)
        self.assertEqual(len(commits), 4)
        self.assertTrue(all('[skip ci]' in command for command in commits))
        self.assertNotIn('--amend', self.workflow)
        self.assertIn('[skip ci]', self.step('Continue the durable automation branch'))
        triggers = self.workflow.split('\npermissions:', 1)[0]
        self.assertIn('  schedule:', triggers)
        self.assertIn('  workflow_dispatch:', triggers)
        self.assertNotIn('  pull_request:', triggers)
        validation = (ROOT / '.github/workflows/validate.yml').read_text(encoding='utf-8')
        self.assertIn('  pull_request:\n', validation)
        self.assertNotIn('paths-ignore:', validation)
        self.assertNotIn('pull_request_target:', validation)
        self.assertNotIn('[skip ci]', validation)
        self.assertIn('actions/workflows/pages.yml/dispatches', (ROOT / 'scripts/merge_research_pr.py').read_text(encoding='utf-8'))

    def legacy_marker_script(self) -> str:
        step = self.step('Open or update the review pull request')
        start = step.index('          # Legacy pending heads')
        end = step.index('          # Also persist', start)
        self.assertLess(step.index('git diff --quiet origin/main HEAD'), start)
        self.assertLess(end, step.index('git push origin'))
        return 'set -euo pipefail\n' + textwrap.dedent(step[start:end])

    def local_git_fixture(self) -> tuple[Path, str]:
        git_exe = shutil.which('git')
        if not git_exe:
            self.skipTest('Git is needed for the isolated workflow fixture')
        bash = str(Path(git_exe).resolve().parents[1] / 'bin/bash.exe') if os.name == 'nt' else shutil.which('bash')
        if not bash or not Path(bash).is_file():
            self.skipTest('Bash is needed to execute the actual workflow snippet')
        directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.fixture_git(directory, 'init', '-b', 'main')
        self.fixture_git(directory, 'config', 'user.name', 'Workflow Test')
        self.fixture_git(directory, 'config', 'user.email', 'test@example.test')
        self.fixture_git(directory, 'config', 'commit.gpgsign', 'false')
        self.fixture_git(directory, 'commit', '--allow-empty', '-m', 'Existing research state')
        return directory, bash

    def fixture_git(self, directory: Path, *args: str) -> str:
        return subprocess.check_output(['git', '-C', str(directory), *args], stderr=subprocess.PIPE).decode('utf-8').strip()

    def test_legacy_head_gets_one_empty_commit_without_rewriting_history(self) -> None:
        directory, bash = self.local_git_fixture()
        previous = self.fixture_git(directory, 'rev-parse', 'HEAD')
        tree = self.fixture_git(directory, 'rev-parse', 'HEAD^{tree}')
        for _ in range(2):
            subprocess.run([bash, '-c', self.legacy_marker_script()], cwd=directory, check=True, capture_output=True)
            self.assertEqual(self.fixture_git(directory, 'rev-parse', 'HEAD^'), previous)
            self.assertEqual(self.fixture_git(directory, 'rev-parse', 'HEAD^{tree}'), tree)
            self.assertIn('[skip ci]', self.fixture_git(directory, 'log', '-1', '--format=%B'))

    def test_legacy_marker_cannot_commit_leftover_staged_output(self) -> None:
        directory, bash = self.local_git_fixture()
        previous = self.fixture_git(directory, 'rev-parse', 'HEAD')
        (directory / 'unvalidated.json').write_text('{}', encoding='utf-8')
        self.fixture_git(directory, 'add', 'unvalidated.json')
        result = subprocess.run([bash, '-c', self.legacy_marker_script()], cwd=directory, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b'uncommitted staged output', result.stdout)
        self.assertEqual(self.fixture_git(directory, 'rev-parse', 'HEAD'), previous)
        self.assertEqual(self.fixture_git(directory, 'diff', '--cached', '--name-only'), 'unvalidated.json')

    def test_merge_only_head_keeps_ancestry_and_duplicate_ci_marker(self) -> None:
        directory, bash = self.local_git_fixture()
        self.fixture_git(directory, 'checkout', '-b', 'automation/openai-arxiv-research')
        self.fixture_git(directory, 'commit', '--allow-empty', '-m', 'Pending research')
        research = self.fixture_git(directory, 'rev-parse', 'HEAD')
        self.fixture_git(directory, 'checkout', 'main')
        self.fixture_git(directory, 'commit', '--allow-empty', '-m', 'New main change')
        main = self.fixture_git(directory, 'rev-parse', 'HEAD')
        self.fixture_git(directory, 'update-ref', 'refs/remotes/origin/main', main)
        self.fixture_git(directory, 'checkout', 'automation/openai-arxiv-research')
        command = next(line.strip() for line in self.step('Continue the durable automation branch').splitlines() if line.strip().startswith('git merge '))
        subprocess.run([bash, '-c', command], cwd=directory, check=True, capture_output=True)
        self.assertEqual(self.fixture_git(directory, 'rev-parse', 'HEAD^1'), research)
        self.assertEqual(self.fixture_git(directory, 'rev-parse', 'HEAD^2'), main)
        self.assertIn('[skip ci]', self.fixture_git(directory, 'log', '-1', '--format=%B'))

    def test_daily_run_only_generates_research_and_classifies_completion(self) -> None:
        daily = self.step("Run daily research")
        self.assertIn("python scripts/research_pipeline.py", daily)
        self.assertNotIn("research_publication.py", daily)
        self.assertNotIn("git add", daily)
        self.assertIn("UPDATE_CONFIRMED|NO_RELEVANT_PAPERS|NO_NEW_BATCH_EXPECTED", daily)
        self.assertIn("UPDATE_NOT_CONFIRMED|UPDATER_OFFLINE", daily)
        self.assertIn('echo "report_path=$report_path"', daily)
        self.assertIn('echo "publishable=$publishable"', daily)
        self.assertIn("--published-history content/chatgpt_scheduler_history.json", daily)
        self.assertIn('recovery_args=()', daily)
        self.assertIn('if [[ "$RECOVER_PENDING" == "true" ]]; then', daily)
        self.assertIn('recovery_args+=(--recover-pending)', daily)
        self.assertIn('"${recovery_args[@]}"', daily)

    def test_historical_recovery_is_manual_daily_opt_in(self) -> None:
        planner = self.step("Plan review mode")
        self.assertIn('requested_recovery = os.environ.get("REQUESTED_RECOVERY", "false")', planner)
        self.assertIn('event_name != "workflow_dispatch" or mode != "daily"', planner)
        self.assertIn('historical recovery requires an explicit manual daily run', planner)
        self.assertIn('default: false\n        type: boolean', self.workflow)

    def test_research_is_scanned_and_pushed_before_publication(self) -> None:
        expected_order = [
            "Run daily research",
            "Validate generated research boundary",
            "Persist research state and report",
            "Reconcile completed research reports",
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

    def test_every_durable_completed_report_is_reconciled(self) -> None:
        publication = self.step("Reconcile completed research reports")
        self.assertNotIn("steps.research.outputs.publishable", publication)
        self.assertIn("python scripts/research_publication.py", publication)
        self.assertIn("--daily-report-dir research/daily", publication)
        self.assertNotIn('${{ steps.research.outputs.report_path }}', publication)
        self.assertIn("--regenerate-site", publication)

    def test_publication_is_fully_validated_and_committed_separately(self) -> None:
        expected_order = [
            "Reconcile completed research reports",
            "Validate generated publication and change scope",
            "Reject private state and likely secrets before publication commit",
            "Commit generated publication",
        ]
        positions = [self.position(name) for name in expected_order]
        self.assertEqual(positions, sorted(positions))

        validation = self.step("Validate generated publication and change scope")
        self.assertNotIn("steps.research.outputs.publishable", validation)
        self.assertIn("python -m unittest discover -s tests -v", validation)
        self.assertIn("node --test tests/test_model_math.cjs", validation)
        self.assertIn("tests/test_archive_ui.cjs", validation)
        self.assertIn("python scripts/import_scheduler_history.py --check", validation)
        self.assertIn("git diff --check", validation)
        self.assertIn('name == "content/chatgpt_scheduler_history.json"', validation)
        self.assertIn('posix.parts[:2] == ("site", "data")', validation)

        privacy = self.step(
            "Reject private state and likely secrets before publication commit"
        )
        self.assertNotIn("steps.research.outputs.publishable", privacy)
        self.assertIn('"git", "ls-files", "-z", "--cached", "--others"', privacy)
        self.assertIn("private key block", privacy)
        self.assertIn("GitHub token", privacy)
        self.assertIn("AWS access key", privacy)
        self.assertIn("OpenAI-style key", privacy)

        commit = self.step("Commit generated publication")
        self.assertIn("id: publication_commit", commit)
        self.assertNotIn("steps.research.outputs.publishable", commit)
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

    def test_incomplete_daily_run_fails_only_after_state_and_pr_are_persisted(self) -> None:
        reporter = self.step("Report incomplete daily research")
        self.assertIn("if: always()", reporter)
        self.assertIn("steps.plan.outputs.mode == 'daily'", reporter)
        self.assertIn("steps.research.outputs.status == 'UPDATE_NOT_CONFIRMED'", reporter)
        self.assertIn("steps.research.outputs.status == 'UPDATER_OFFLINE'", reporter)
        self.assertIn("exit 1", reporter)
        self.assertLess(
            self.position("Persist research state and report"),
            self.position("Report incomplete daily research"),
        )
        self.assertLess(
            self.position("Open or update the review pull request"),
            self.position("Report incomplete daily research"),
        )

    def test_merge_candidate_requires_all_generation_and_validation_to_succeed(self) -> None:
        candidate = self.step("Record the fully validated merge candidate")
        self.assertIn("if: success() && steps.review_pr.outputs.number != ''", candidate)
        self.assertIn("git status --porcelain --untracked-files=all", candidate)
        self.assertIn("json.loads", candidate)
        self.assertIn("git rev-parse HEAD", candidate)
        self.assertIn("git rev-parse origin/main", candidate)
        self.assertLess(self.position("Commit generated publication"), self.position("Record the fully validated merge candidate"))
        self.assertLess(self.position("Record the fully validated merge candidate"), self.position("Report incomplete daily research"))
        self.assertIn("scripts/merge_research_pr.py", self.step("Verify the automation branch is data-only"))

    def test_existing_validated_pr_can_merge_without_new_generated_changes(self) -> None:
        pull_request = self.step("Open or update the review pull request")
        self.assertIn("steps.publication_commit.outcome == 'success'", pull_request)
        self.assertIn("git diff --quiet origin/main HEAD", pull_request)
        self.assertIn('git push origin "HEAD:$AUTOMATION_BRANCH"', pull_request)
        self.assertIn('echo "number=$existing"', pull_request)

    def test_merge_is_isolated_from_research_secrets_and_requires_a_validated_candidate(self) -> None:
        merge_job = self.workflow.split("\n  merge-and-publish:\n", 1)[1]
        self.assertIn("needs: research", merge_job)
        self.assertIn("always() && !cancelled()", merge_job)
        self.assertIn("github.ref == 'refs/heads/main'", merge_job)
        self.assertIn("needs.research.outputs.merge_ready == 'true'", merge_job)
        self.assertIn("ref: ${{ github.sha }}", merge_job)
        self.assertIn("actions: write", merge_job)
        self.assertNotIn("OPENAI_API_KEY", merge_job)
        self.assertNotIn("research_pipeline.py", merge_job)
        self.assertIn("--head-sha", merge_job)
        self.assertIn("--base-sha", merge_job)


if __name__ == "__main__":
    unittest.main()
