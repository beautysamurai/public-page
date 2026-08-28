# Privacy policy and publishing checklist

## Scope

This repository produces a public static digest from sanitized scheduled-task
reviews and public arXiv metadata. It does not need user accounts, form
submissions, or a server-side database. The checked-in workflows validate files
and deploy **site/**; they do not run a scheduled task, query arXiv, or send
content to an AI service.

External sites linked from the digest, including arXiv, operate under their own
privacy policies.

## What becomes public

Assume all of the following can be read and copied by anyone:

- every tracked file and its Git history;
- **config/topics.json**, including categories, keywords, and thresholds;
- every sanitized scheduled narrative, rating, recommendation, and limitation
  in **content/chatgpt_scheduler_history.json**;
- generated paper metadata, status, provenance, and update timestamps in
  **site/data/**;
- GitHub Actions logs and the deployed GitHub Pages site.

The configuration and scheduled history reveal the digest's subject interests.
Keep only material that is appropriate to publish. Paper metadata is public at
its source, but selections, prose, rankings, and ratings still reveal editorial
choices.

## What must remain local

Do not commit:

- API keys, access tokens, cookies, passwords, or credential files;
- raw ChatGPT exports, task or conversation identifiers, internal citation
  tokens, private prompts, annotations, reading notes, unpublished work, or
  contact lists;
- personal email addresses, workstation usernames, or absolute local paths;
- raw debugging responses that contain request headers or local environment
  details;
- local scheduler logs, caches, databases, or experimental model output.

Place project-specific private state under **.local/**. That directory is ignored
by Git and checked by CI, but **.gitignore** is not an access-control mechanism.
Before adding any file, use **git status** and inspect the staged diff.

## Network behavior

Running **scripts/arxiv_digest.py** locally sends the configured public
categories and keywords as part of requests needed to retrieve arXiv metadata.
The local launchers write candidate data and operational logs under
**.local/**. Importing **content/chatgpt_scheduler_history.json** is offline.
GitHub Actions performs neither fetch and has no cron schedule.

The optional Windows startup sync uses the local Git and GitHub CLI credentials
to fetch the public base branch, push a timestamped review branch, and open a
pull request. Authentication is configured locally with `gh auth login`; no
GitHub token, account name, credential file, or workstation path belongs in the
repository. Startup logs, inbox files, completion manifests, processed bundles,
and temporary state remain local and are never intentionally staged.

The deployed site is static. Do not add analytics, remote fonts, tracking
pixels, comment widgets, or other third-party browser requests without first
documenting what is sent, why it is needed, how long it is retained, and how a
visitor can avoid it.

## Startup-sync publication boundary

The startup sync accepts only a producer-complete reviewed bundle created by
**scripts/stage_public_review_bundle.ps1**. The final ignored inbox contains:

- `chatgpt_scheduler_history.json` for the source/Japanese archive;
- `en.json` for the reviewed English editorial overlay;
- `bundle.complete.json`, which binds both files to exact byte lengths and
  SHA-256 digests.

Do not write, replace, or update files directly inside an existing
**.local/inbox/public-review/** directory. The staging helper copies the two
reviewed snapshots into a fresh sibling directory, creates and validates the
completion manifest, and only then atomically renames that completed directory
into the inbox. An incomplete or legacy two-file inbox is not claimed.

It does not scrape ChatGPT, read browser state, or convert raw task exports. A
separate trusted review/export step must create the two reviewed public
snapshots before they are passed to the staging helper.

Before a branch is pushed, the sync:

1. atomically claims the producer-complete inbox directory;
2. verifies its completion manifest and hashes before snapshotting;
3. copies the two JSON files and manifest with exclusive sharing and revalidates
   the exact immutable snapshot;
4. validates both public schemas and rejects unknown fields;
5. requires every existing public edition and translation to remain unchanged
   and in the same prefix order;
6. requires at least one new edition and exact source/translation alignment;
7. rejects unsafe English text, internal references, email addresses, local
   paths, and untranslated CJK copy;
8. regenerates deterministic archives and runs all tests;
9. permits staged changes only to the reviewed source, English overlay, latest
   snapshot, and JSON archive paths; and
10. opens a pull request rather than updating `main` or GitHub Pages directly.

The public site therefore remains on the last merged edition while the machine
is off, while no completed reviewed bundle exists, or while a pull request is
waiting for review. Failed claimed bundles remain local for inspection and are
not published.

## Before publishing an update

1. Inspect **git diff -- content site/data config/topics.json**.
2. Confirm the edition date, status, expected/observed batch dates, coverage
   period, provenance, and paper ratings accurately describe the response.
3. Confirm source text contains no raw citation markers, task/thread IDs,
   prompts, tracking links, credentials, private notes, email addresses,
   usernames, or local paths.
4. Run **python scripts/import_scheduler_history.py --check** and the tests.
5. Confirm that only intended source and generated snapshots are staged.
6. Push a review branch and merge only after the public preview is acceptable.

If private data or a secret is pushed, remove access or rotate the secret
immediately. Then clean the current tree and repository history as appropriate;
deleting it in a later commit does not erase earlier copies or downstream
clones.
