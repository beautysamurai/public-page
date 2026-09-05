# Privacy policy and publishing checklist

## Scope

This repository produces a public static digest from sanitized scheduled-task
reviews, automated Responses API reviews, and public arXiv metadata. It does not
require an account for public reading. An optional personal library uses
Supabase Auth and its database, separately from public research. The dedicated
research workflow queries arXiv and OpenAI on a schedule; the separate Pages
workflow validates files and builds **site/** into **.local/site/** for deployment.

External sites linked from the digest, including arXiv, Google Search, and X,
operate under their own privacy policies.

## Optional personal library

Anonymous reading makes no Supabase request. Choosing email sign-in sends the
email address and confirmation code to the configured Supabase project. The
project's email provider delivers the code. Supabase also receives ordinary
connection metadata such as IP address and browser information; the provider's
logs and retention settings apply. Returning signed-in visitors contact
Supabase to restore their login and load their library.

Bookmarks store the version-free arXiv ID. Presets store the chosen name and
search filters (including any keyword typed by the user). These records stay
in Supabase, not GitHub, the public archive, or the OpenAI research pipeline.
Ownership policies restrict users to their own records. The project operator
and privileged database administrators can still access them; this is not
end-to-end encryption. Do not save confidential work information in keywords.

The browser stores a Supabase login session in localStorage, scoped to this
project and site path. Personal records are only held in page memory. Pages
under the same GitHub Pages origin remain in the same browser security boundary;
do not host untrusted scripts under that origin. Sign out on shared devices.
Use public reading without signing in to avoid this optional data collection.

Bookmarks and presets remain until removed by the user or project operator.
Use the star again to remove a bookmark, Delete for a preset, or Download saved
library for a local JSON copy (keep that download private). The project owner
can delete the Auth user in Supabase to cascade-delete their library. This does
not erase pre-existing provider logs or backups; configure and document those
retention periods before opening public registration. Logging out removes this
browser's session, not the cloud library or logins on other devices.

The official Supabase SDK is bundled and served from the same origin, not a
remote CDN. Only the exact configured project HTTPS origin is added to the
homepage's connection policy. Public URL/publishable key values are intentionally
visible; service-role/secret keys must never be used by this site.

## What becomes public

Assume all of the following can be read and copied by anyone:

- every tracked file and its Git history;
- **config/topics.json** and **config/research.json**, including categories,
  keywords, thresholds, and model names;
- every sanitized scheduled narrative, rating, recommendation, and limitation
  in **content/chatgpt_scheduler_history.json**;
- every committed state and sanitized JSON/Markdown report under **research/**;
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

Running **scripts/research_pipeline.py** locally or in the research workflow
sends configured public categories to arXiv. It sends public titles, abstracts,
validated metadata, and only high-importance canonical PDF URLs to OpenAI. It
does not send the repository, local notes, browser state, or Git credentials.
The local launchers write state, reports, and logs under **.local/**. Importing
**content/chatgpt_scheduler_history.json** remains offline.

The scheduled research workflow runs at 06:30 UTC, stores the API key only in
the process environment, and writes sanitized results to a fixed review branch.
GitHub Actions logs and pull-request diffs are public to the same extent as the
repository, so prompts, raw responses, headers, and credentials must never be
printed or committed.

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

Paper cards provide optional outbound Web and X search links for following a
paper after publication. The static page makes no request to Google or X while
it loads. Only clicking one of those links sends the paper title and canonical,
version-free arXiv identifier as a search query to the selected service, in a
new tab without a referrer.

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

1. Inspect **git diff -- content research site/data config**.
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
