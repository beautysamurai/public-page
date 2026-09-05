"""Merge only the exact data-only PR validated by the trusted research job.

Uses the runner's GitHub CLI and short-lived GITHUB_TOKEN, not an API key/PAT.
Repository rules are enforced by the normal merge API; no admin bypass is used.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import time
from pathlib import Path, PurePosixPath


AUTOMATION_BRANCH = "automation/openai-arxiv-research"
BASE_BRANCH = "main"
BOT_LOGIN = "github-actions[bot]"


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args]).decode("utf-8")


def api(repository: str, endpoint: str, *, method: str = "GET", **fields: str) -> dict:
    command = ["gh", "api", f"repos/{repository}/{endpoint}", "--method", method]
    for key, value in fields.items():
        command.extend(["--raw-field", f"{key}={value}"])
    output = subprocess.check_output(command).decode("utf-8")
    return json.loads(output) if output.strip() else {}


def validate_sha(value: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{40}", value):
        raise ValueError("Expected a full lowercase Git commit SHA")
    return value


def validate_identity(pr: dict, repository: str, number: int, head: str) -> None:
    if pr.get("number") != number or pr.get("user", {}).get("login") != BOT_LOGIN:
        raise ValueError("Only the research bot's pull request can auto-merge")
    if pr.get("draft") is not False:
        raise ValueError("Draft pull requests cannot auto-merge")
    for side, branch in (("base", BASE_BRANCH), ("head", AUTOMATION_BRANCH)):
        ref = pr.get(side, {})
        if (ref.get("repo") or {}).get("full_name", "").casefold() != repository.casefold():
            raise ValueError("The pull request must stay inside this repository")
        if ref.get("ref") != branch:
            raise ValueError("Unexpected research pull request branch")
    if pr["head"].get("sha") != head:
        raise ValueError("Pull request head changed after validation")
    if not pr.get("merged") and pr.get("state") != "open":
        raise ValueError("Research pull request is closed without merging")


def validate_data_diff(raw_diff: str) -> None:
    """Inspect both modes, including deletions; never follow links or renames."""
    records = raw_diff.split("\0")
    if records[-1] != "" or len(records) % 2 != 1:
        raise ValueError("Malformed data diff")
    if len(records) == 1:
        raise ValueError("No research changes to merge")
    for index in range(0, len(records) - 1, 2):
        header, name = records[index:index + 2]
        fields = header.removeprefix(":").split()
        if not header.startswith(":") or len(fields) != 5:
            raise ValueError("Malformed data diff entry")
        old_mode, new_mode, _, _, status = fields
        expected_modes = {
            "A": ("000000", "100644"),
            "M": ("100644", "100644"),
            "D": ("100644", "000000"),
        }
        if expected_modes.get(status) != (old_mode, new_mode):
            raise ValueError("Research changes must be regular non-executable files")
        path = PurePosixPath(name)
        allowed = name == "content/chatgpt_scheduler_history.json" or (
            path.suffix in {".json", ".md"}
            and (
                len(path.parts) >= 2 and path.parts[0] == "research"
                or len(path.parts) >= 3 and path.parts[:2] == ("site", "data")
            )
        )
        if not allowed or any(part.startswith(".") for part in path.parts):
            raise ValueError(f"Non-data change prevents automatic merge: {name}")


def require_ancestor(ancestor: str, descendant: str) -> None:
    subprocess.run(["git", "merge-base", "--is-ancestor", ancestor, descendant], check=True)


def merge_and_publish(repository: str, number: int, head: str, base: str) -> str:
    if (
        not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository)
        or any(part in {".", ".."} for part in repository.split("/"))
        or number < 1
    ):
        raise ValueError("Invalid repository or pull request number")
    validate_sha(head)
    validate_sha(base)
    endpoint = f"pulls/{number}"
    pr = api(repository, endpoint)
    validate_identity(pr, repository, number, head)
    git("fetch", "--no-tags", "origin", f"{BASE_BRANCH}:refs/remotes/origin/{BASE_BRANCH}")
    current_base = git("rev-parse", f"origin/{BASE_BRANCH}").strip()

    if pr.get("merged"):
        # A previous attempt may have merged successfully but failed to dispatch
        # Pages. Retrying this job does not create another merge or call OpenAI.
        merged_sha = validate_sha(pr.get("merge_commit_sha", ""))
        require_ancestor(head, merged_sha)
        require_ancestor(merged_sha, current_base)
    else:
        if current_base != base:
            raise ValueError("main changed after validation; the next research run must revalidate")
        git("fetch", "--no-tags", "origin", f"refs/heads/{AUTOMATION_BRANCH}")
        if git("rev-parse", "FETCH_HEAD").strip() != head:
            raise ValueError("Automation branch changed after validation")
        require_ancestor(base, head)
        validate_data_diff(git("diff", "--raw", "-z", "--no-renames", base, head, "--"))

        # GitHub calculates mergeability asynchronously. Only retry reads, never
        # a merge with an ambiguous result; a job retry handles already-merged PRs.
        for attempt in range(6):
            pr = api(repository, endpoint)
            validate_identity(pr, repository, number, head)
            if pr.get("merged") or pr["base"].get("sha") != base:
                raise ValueError("Pull request base/state changed; retry after revalidation")
            if pr.get("mergeable") is not None:
                break
            if attempt < 5:
                time.sleep(2)
        if pr.get("mergeable") is not True:
            raise ValueError("Pull request is conflicting or mergeability is not confirmed")
        # Keep ancestry for the durable branch. Squashing would replay old data
        # on later runs. GitHub rejects changed heads and unmet repository rules.
        result = api(repository, f"{endpoint}/merge", method="PUT", sha=head, merge_method="merge")
        if result.get("merged") is not True:
            raise ValueError("GitHub did not confirm the research merge")
        merged_sha = validate_sha(result.get("sha", ""))

    # GITHUB_TOKEN merges do not trigger push workflows. Explicit dispatch does,
    # and pages.yml independently revalidates main with read-only permissions.
    api(repository, "actions/workflows/pages.yml/dispatches", method="POST", ref=BASE_BRANCH)
    message = (
        f"Merged research PR #{number} at {merged_sha}. "
        "Dispatched Deploy GitHub Pages on main."
    )
    print(message)
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(summary).open("a", encoding="utf-8") as handle:
            handle.write(message + "\n")
    return merged_sha


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--pr", required=True, type=int)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--base-sha", required=True)
    args = parser.parse_args()
    merge_and_publish(args.repository, args.pr, args.head_sha, args.base_sha)


if __name__ == "__main__":
    main()
