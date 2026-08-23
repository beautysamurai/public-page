# Privacy policy and publishing checklist

## Scope

This repository produces a public static digest from public arXiv metadata. It
does not need user accounts, form submissions, or a server-side database. The
checked-in workflows validate files and deploy **site/**; they do not run the
updater, query arXiv, or send content to an AI service.

External sites linked from the digest, including arXiv, operate under their own
privacy policies.

## What becomes public

Assume all of the following can be read and copied by anyone:

- every tracked file and its Git history;
- **config/topics.json**, including categories, keywords, and thresholds;
- generated paper metadata, relevance scores, status, and update timestamps in
  **site/data/**;
- GitHub Actions logs and the deployed GitHub Pages site.

The configuration therefore reveals the digest's subject interests. Keep only
terms that are appropriate to publish. Paper metadata is public at its source,
but selections and scores still reveal editorial choices.

## What must remain local

Do not commit:

- API keys, access tokens, cookies, passwords, or credential files;
- private prompts, annotations, reading notes, unpublished work, or contact
  lists;
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
The local launchers write operational logs under **.local/logs/**. GitHub Actions
does not perform this fetch and has no cron schedule.

The deployed site is static. Do not add analytics, remote fonts, tracking
pixels, comment widgets, or other third-party browser requests without first
documenting what is sent, why it is needed, how long it is retained, and how a
visitor can avoid it.

## Before publishing an update

1. Inspect **git diff -- site/data config/topics.json**.
2. Confirm the timestamps, status, expected batch date, and observed batch date
   accurately describe the update.
3. Search the staged content for credentials, private notes, email addresses,
   usernames, and local paths.
4. Confirm that only intended generated snapshots are staged.
5. Push only after the public preview is acceptable.

If private data or a secret is pushed, remove access or rotate the secret
immediately. Then clean the current tree and repository history as appropriate;
deleting it in a later commit does not erase earlier copies or downstream
clones.
