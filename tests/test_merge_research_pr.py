from __future__ import annotations

import subprocess
import unittest
from unittest.mock import call, patch

from scripts import merge_research_pr as merger


REPOSITORY = "owner/research"
HEAD = "a" * 40
BASE = "b" * 40
MERGED = "c" * 40
RAW_DIFF = ":100644 100644 1111111 2222222 M\0research/state.json\0"


def pull_request() -> dict:
    return {
        "number": 42,
        "user": {"login": "github-actions[bot]"},
        "draft": False,
        "state": "open",
        "merged": False,
        "mergeable": True,
        "base": {"ref": "main", "sha": BASE, "repo": {"full_name": REPOSITORY}},
        "head": {
            "ref": merger.AUTOMATION_BRANCH,
            "sha": HEAD,
            "repo": {"full_name": REPOSITORY},
        },
    }


class ResearchMergeBoundaryTests(unittest.TestCase):
    def test_cli_uses_string_fields_and_accepts_empty_dispatch_response(self) -> None:
        with patch.object(merger.subprocess, "check_output", return_value=b"") as command:
            self.assertEqual(merger.api(REPOSITORY, "dispatch", method="POST", ref="main"), {})
        self.assertEqual(command.call_args.args[0], ["gh", "api", "repos/owner/research/dispatch", "--method", "POST", "--raw-field", "ref=main"])

    def test_only_the_expected_bot_pr_is_accepted(self) -> None:
        merger.validate_identity(pull_request(), REPOSITORY, 42, HEAD)
        changes = (
            {"number": 99},
            {"user": {"login": "a-human"}},
            {"draft": True},
            {"state": "closed"},
        )
        for change in changes:
            with self.subTest(change=change), self.assertRaises(ValueError):
                merger.validate_identity(pull_request() | change, REPOSITORY, 42, HEAD)
        for side in ("head", "base"):
            for change in ({"ref": "other"}, {"repo": {"full_name": "fork/research"}}, {"repo": None}):
                pr = pull_request()
                pr[side].update(change)
                with self.subTest(side=side, change=change), self.assertRaises(ValueError):
                    merger.validate_identity(pr, REPOSITORY, 42, HEAD)
        with self.assertRaises(ValueError):
            merger.validate_identity(pull_request(), REPOSITORY, 42, BASE)

    def test_only_regular_public_data_changes_are_accepted(self) -> None:
        paths = (
            "research/state.json", "research/daily/2026-09-05.md",
            "research/pending-periods/weekly/2026-08-28.json",
            "site/data/archive.json", "content/chatgpt_scheduler_history.json",
        )
        for path in paths:
            for modes, status in (("000000 100644", "A"), ("100644 100644", "M"), ("100644 000000", "D")):
                with self.subTest(path=path, status=status):
                    merger.validate_data_diff(f":{modes} 1111111 2222222 {status}\0{path}\0")
        for path in ("scripts/research_pipeline.py", ".github/workflows/pages.yml", "site/app.js", "research/tool.py", "research/.env.json", "research/.local/cache.json", "content/other.json"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                merger.validate_data_diff(RAW_DIFF.replace("research/state.json", path))

    def test_executables_links_renames_empty_and_malformed_diffs_fail_closed(self) -> None:
        for raw in (
            "", "broken", "header\0path\0",
            RAW_DIFF.replace("100644", "120000", 1),
            RAW_DIFF.replace("100644", "100755"),
            RAW_DIFF.replace(" M\0", " R100\0"),
            RAW_DIFF.replace(" M\0", " T\0"),
            RAW_DIFF.replace("100644 100644", "100644 000000"),
        ):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                merger.validate_data_diff(raw)


class ResearchMergeExecutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.api = self.enterContext(patch.object(merger, "api"))
        self.git = self.enterContext(patch.object(merger, "git"))
        self.ancestor = self.enterContext(patch.object(merger, "require_ancestor"))
        self.sleep = self.enterContext(patch.object(merger.time, "sleep"))
        self.enterContext(patch.dict(merger.os.environ, {"GITHUB_STEP_SUMMARY": ""}))
        self.api.side_effect = [pull_request(), pull_request(), {"merged": True, "sha": MERGED}, {}]
        self.git.side_effect = self.git_result

    @staticmethod
    def git_result(*args: str) -> str:
        if args == ("rev-parse", "origin/main"):
            return BASE + "\n"
        if args == ("rev-parse", "FETCH_HEAD"):
            return HEAD + "\n"
        if args[0] == "diff":
            return RAW_DIFF
        return ""

    def run_merge(self) -> str:
        return merger.merge_and_publish(REPOSITORY, 42, HEAD, BASE)

    def assert_no_writes(self) -> None:
        self.assertFalse(any(c.kwargs.get("method") in {"PUT", "POST"} for c in self.api.call_args_list))

    def test_exact_validated_head_merges_then_dispatches_pages(self) -> None:
        self.assertEqual(self.run_merge(), MERGED)
        self.ancestor.assert_called_once_with(BASE, HEAD)
        self.assertEqual(self.api.call_args_list[-2:], [
            call(REPOSITORY, "pulls/42/merge", method="PUT", sha=HEAD, merge_method="merge"),
            call(REPOSITORY, "actions/workflows/pages.yml/dispatches", method="POST", ref="main"),
        ])

    def test_new_main_or_branch_commit_requires_revalidation(self) -> None:
        for ref in ("origin/main", "FETCH_HEAD"):
            self.api.reset_mock(side_effect=True)
            self.api.side_effect = [pull_request()]
            self.git.side_effect = lambda *args: MERGED if args == ("rev-parse", ref) else self.git_result(*args)
            with self.subTest(ref=ref), self.assertRaises(ValueError):
                self.run_merge()
            self.assert_no_writes()

    def test_late_pr_changes_do_not_merge(self) -> None:
        for side in ("base", "head"):
            changed = pull_request()
            changed[side]["sha"] = MERGED
            self.api.reset_mock(side_effect=True)
            self.api.side_effect = [pull_request(), changed]
            with self.subTest(side=side), self.assertRaises(ValueError):
                self.run_merge()
            self.assert_no_writes()

    def test_changed_code_does_not_merge(self) -> None:
        self.git.side_effect = lambda *args: RAW_DIFF.replace("research/state.json", "scripts/run.py") if args[0] == "diff" else self.git_result(*args)
        with self.assertRaises(ValueError):
            self.run_merge()
        self.assert_no_writes()

    def test_main_must_be_an_ancestor_of_validated_head(self) -> None:
        self.ancestor.side_effect = subprocess.CalledProcessError(1, ["git"])
        with self.assertRaises(subprocess.CalledProcessError):
            self.run_merge()
        self.assert_no_writes()

    def test_conflict_or_unknown_mergeability_does_not_merge(self) -> None:
        for mergeable in (False, None):
            pr = pull_request() | {"mergeable": mergeable}
            self.api.reset_mock(side_effect=True)
            self.api.return_value = pr
            self.sleep.reset_mock()
            with self.subTest(mergeable=mergeable), self.assertRaises(ValueError):
                self.run_merge()
            self.assertLessEqual(self.sleep.call_count, 5)
            self.assert_no_writes()

    def test_pending_mergeability_is_read_again(self) -> None:
        self.api.side_effect = [pull_request(), pull_request() | {"mergeable": None}, pull_request(), {"merged": True, "sha": MERGED}, {}]
        self.run_merge()
        self.sleep.assert_called_once_with(2)

    def test_rejected_or_uncertain_merge_does_not_deploy(self) -> None:
        for response in ({"merged": False}, subprocess.CalledProcessError(1, ["gh"])):
            self.api.reset_mock(side_effect=True)
            self.api.side_effect = [pull_request(), pull_request(), response]
            with self.subTest(response=response), self.assertRaises((ValueError, subprocess.CalledProcessError)):
                self.run_merge()
            self.assertFalse(any(c.kwargs.get("method") == "POST" for c in self.api.call_args_list))

    def test_already_merged_retry_only_dispatches_pages_after_ancestry_check(self) -> None:
        pr = pull_request() | {"merged": True, "state": "closed", "merge_commit_sha": MERGED}
        self.api.side_effect = [pr, {}]
        self.git.side_effect = lambda *args: MERGED if args == ("rev-parse", "origin/main") else self.git_result(*args)
        self.assertEqual(self.run_merge(), MERGED)
        self.assertEqual(self.ancestor.call_args_list, [call(HEAD, MERGED), call(MERGED, MERGED)])
        self.assertFalse(any(c.kwargs.get("method") == "PUT" for c in self.api.call_args_list))

    def test_missing_merge_ancestry_blocks_a_retry(self) -> None:
        self.api.side_effect = [pull_request() | {"merged": True, "merge_commit_sha": MERGED}]
        self.ancestor.side_effect = subprocess.CalledProcessError(1, ["git"])
        with self.assertRaises(subprocess.CalledProcessError):
            self.run_merge()
        self.assert_no_writes()

    def test_invalid_inputs_do_not_access_github(self) -> None:
        for repository, number, head, base in (("../bad", 42, HEAD, BASE), (REPOSITORY, 0, HEAD, BASE), (REPOSITORY, 42, "main", BASE), (REPOSITORY, 42, HEAD, "")):
            with self.subTest(repository=repository, number=number), self.assertRaises(ValueError):
                merger.merge_and_publish(repository, number, head, base)
        self.api.assert_not_called()

if __name__ == "__main__":
    unittest.main()
