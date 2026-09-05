# Rates & Execution — automated arXiv research

Rates & Execution is a privacy-first automated research pipeline and static
archive for arXiv papers on electronic trading, market microstructure, fixed
income, interest-rate models, rates, and yield curves. The public site is
[beautysamurai.github.io/public-page](https://beautysamurai.github.io/public-page/).

The pipeline checks official arXiv new-list pages, screens every candidate from
its abstract with the OpenAI Responses API, and sends only papers at or above
the configured importance threshold to a second full-PDF analysis. It stores
strict bilingual JSON and Markdown reports, keeps incomplete batches pending,
and reuses completed daily reports for weekly and monthly reviews.

Research automation and Pages deployment remain separate. Automation writes to
a fixed review branch and opens or updates a pull request. After generation,
publication, and privacy validation pass, an isolated job merges that exact
data-only PR into `main` and starts the existing GitHub Pages deployment.

## Architecture

~~~text
official arXiv new-list pages + validated export metadata
  -> abstract screening with GPT-5.6 Luna and strict Responses API JSON Schema
  -> GPT-5.6 Terra full-PDF analysis only when importance >= threshold
  -> durable state + daily JSON/Markdown
  -> GitHub Actions weekly Terra / monthly Sol synthesis from stored daily JSON
  -> scripts/research_publication.py
  -> content/chatgpt_scheduler_history.json + English overlay
  -> deterministic site/data generation
  -> review pull request
  -> automatic merge of the validated data-only head to main
  -> existing GitHub Pages workflow
~~~

The public-history importer remains offline and deterministic. It rejects
unknown fields and unsafe public text, derives canonical arXiv links from each
validated identifier, and refuses to rewrite a different immutable archive.
Historical scheduled-task editions and new Responses API editions coexist in
the same append-only schema.

The interface is Japanese by default. The **日本語 / English** control stores
English in the shareable `?lang=en` URL and preserves the selected archive
edition. The separate theory-note area under **site/theory/** continues to use
ordinary static routes that work on direct GitHub Pages reloads.

The archive can be filtered by daily, weekly, or monthly edition and by an
inclusive date range. Daily editions use their edition date; weekly and monthly
reviews use the end of their coverage period. Filter state stays in the URL
when an edition is opened or the language changes. Each paper card also
provides optional Web and X searches built from its public title and
version-free arXiv identifier; these
links make no third-party request until clicked.

Choose **論文から探す · 評価 / タグ / Find papers** in the archive to search
papers across all saved editions, filter by minimum rating and topic tag, and
sort by rating or review date. Date and review-type filters also apply in this
view; leaving dates blank searches all time. Versioned appearances of the same
arXiv paper are grouped into one card. Its rating and tags come from the newest
matching edition, with links to the individual reviews. Missing ratings are not
treated as zero. Scores on older scales are converted to 10 only for comparison;
the original rating is shown and the stored reviews are never re-scored.

The offline importer regenerates the compact `site/data/papers.json` catalogue
alongside the existing archive index. Browsing fetches this single static file
only when the paper view is opened; no OpenAI calls or new research are involved.
The existing publication, validation, synchronization and Pages flows include it.
Review TeX is rendered into native MathML without third-party scripts or unsafe
HTML insertion; unsupported commands remain visible as literal fallback text.

## Install and configure

### Optional personal bookmarks and search presets (Supabase)

Public research remains on GitHub Pages and needs no login. **マイライブラリ /
My library** adds email-code login, bookmarks shared across a paper's versions,
and named filter presets. Stars appear in both the issue and archive paper cards.
**全期間のブックマークを表示 / Show bookmarks across all dates** starts across
all review types/dates; rating, tag and keyword filters can then be combined.
Presets save conditions, not a fixed list of results. Leave dates empty to match
future papers. Applying a preset preserves the page language; deleting a preset
does not delete its papers or reviews. Names are not unique: saving creates a new
preset, avoiding silent overwrites of a similarly named preset on another device.

The only local persistence is the Supabase login session. Personal records live
in Supabase with per-user RLS policies; they are never published to GitHub or
sent to OpenAI. Sync occurs after sign-in, after each write, when returning to
the visible page (throttled to 30 seconds), or via **同期する / Sync now**. It is
not continuous realtime streaming. Offline writes are not queued; failures stay
unconfirmed until a successful sync. Edits operate on individual rows rather
than replacing the whole library; simultaneous bookmark edits follow the last
accepted server operation. Other device sessions remain logged in after logout.

#### 1. Supabase setup (one-time)

1. Create a Supabase project in your own account. A Free project can be used;
   this repository does not create a subscription or upgrade a plan. Review the
   current [quotas and inactivity policy](https://supabase.com/pricing).
2. In its SQL Editor, run
   **supabase/migrations/202609050001_personal_library.sql** once. Alternatively,
   apply it with the Supabase CLI migration workflow. The migration creates
   `research_bookmarks` and `research_presets`, user foreign keys, size checks,
   grants and ownership policies together. Do not disable RLS. It deliberately
   fails if applied a second time; future schema changes need new migrations.
3. Enable the Email provider and allow new signups in Authentication. Configure
   BOTH the **Confirm signup** and **Magic Link / OTP** email templates to show
   the code, for example `Your Rates & Execution code: {{ .Token }}`. Existing
   and new users can use different templates. This site accepts 6–10 digits and
   calls `verifyOtp` with `type: "email"`; it does not consume magic-link tokens
   or credentials from a URL. Set the Site URL to
   `https://beautysamurai.github.io/public-page/`.
4. Configure **custom SMTP** before offering sign-in to general visitors.
   Supabase's built-in sender is for testing, sends only to your organization
   team's addresses, and has a low rate limit (currently two emails/hour).
   SMTP credentials stay in Supabase, never GitHub/browser configuration. Your
   email provider may have its own charges and quotas. Set appropriate Auth
   rate limits and review signup/email abuse before public launch. See
   [SMTP setup](https://supabase.com/docs/guides/auth/auth-smtp) and
   [email code sign-in](https://supabase.com/docs/guides/auth/auth-email-passwordless).
5. Copy the project's HTTPS **Project URL** and **publishable key** beginning
   `sb_publishable_`. Only these two public values are needed. Do NOT use a
   `sb_secret_` key, legacy `service_role` JWT, database password, or OpenAI key.
   This implementation intentionally accepts only the hosted `*.supabase.co`
   project URL and modern publishable keys. See [key types](https://supabase.com/docs/guides/getting-started/api-keys).

#### 2. Local preview

Add `SUPABASE_URL` and `SUPABASE_PUBLISHABLE_KEY` to the ignored root `.env`
(see `.env.example`). Leave both blank for the public-only mode. The Node.js
22+ build reads `.env` without exposing other values, bundles the pinned official
SDK locally, and generates a separate `.local/site` directory. It never edits
the committed disabled configuration or the immutable review data.

~~~powershell
npm ci --ignore-scripts
npm test
npm run build
python -m http.server 8765 --bind 127.0.0.1 --directory .local/site
~~~

Open `http://127.0.0.1:8765/` and expand My library. Use the built directory, not
raw `site/`, to test configured login. Both values absent means a visible
"not configured" message, not a fake local save. A partial/invalid configuration
fails the build rather than silently enabling a broken connection.

#### 3. GitHub Pages settings

In **Settings → Secrets and variables → Actions → Variables**, add these
repository variables (not research secrets):

- `SUPABASE_URL`: the project's HTTPS URL;
- `SUPABASE_PUBLISHABLE_KEY`: its public browser key.

Then run **Deploy GitHub Pages** on `main`, or allow the next merged change to
deploy it. No database administrator credential belongs in Actions. The Pages
workflow uploads only `.local/site`, with the exact project's HTTPS origin added
to the homepage's `connect-src`. Scripts/styles still come from this site;
there is no CDN dependency, wildcard connection permission or analytics.
The existing daily/weekly/monthly research workflow and automatic data-only merge
rules are unchanged. Every subsequent Pages deployment uses the same variables.
To disable personal sync, remove BOTH variables and redeploy; cloud data remains.

#### 4. Verify before inviting users

`npm test` includes controller race tests, DOM-level login/bookmark/preset flows,
and the exact SQL migration on a local PostgreSQL engine with simulated auth
claims. Tests cover anonymous access denial, A/B user separation, ownership
reassignment denial, own CRUD, cascade deletion, failed requests and late
responses after logout. Tests make no real Supabase/OpenAI requests.

After setup, separately verify real email delivery for a new user and a returning
user; save on device A, sign in as the same user on device B and sync; then sign
in as a different user and verify an empty independent library. Also test
cross-user API reads/writes using those users' own sessions (never a service-role
key). A hosted two-user end-to-end check cannot be completed without your project
and working email delivery. Review [PRIVACY.md](PRIVACY.md) before public signup;
configure provider-log/backup retention and a user deletion contact/process.

To export, use **保存内容をダウンロード / Download saved library**. Remove
individual stars/presets in the UI. Account deletion is an operator action in
Supabase Authentication and cascades to both personal tables; it does not delete
public research. Export files are private and must not be added to this repository.

### Research pipeline setup

Requirements:

- Python 3.12 or another supported Python 3 release;
- an OpenAI API key on days with candidate papers; and
- Node.js 22 for the complete site test suite.

Create a virtual environment, install the SDK, and copy the ignored environment
template:

~~~bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
~~~

On Windows PowerShell, activate the environment explicitly before installing:

~~~powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
~~~

Add the key to `.env` and never commit that file:

~~~dotenv
OPENAI_API_KEY=your-key-here
~~~

Public model and category defaults live in **config/research.json**. The default
categories are q-fin.TR, q-fin.MF, q-fin.CP, q-fin.PR, q-fin.RM, and q-fin.EC.
The default PDF threshold is importance 3. PDF page images use explicit `low`
detail by default; extracted PDF text is still included. These optional
environment variables override the configured models and PDF detail:

- `OPENAI_SCREENING_MODEL` for abstract screening;
- `OPENAI_FULL_TEXT_MODEL` for full-paper analysis;
- `OPENAI_WEEKLY_MODEL` for weekly synthesis;
- `OPENAI_MONTHLY_MODEL` for monthly synthesis; and
- `OPENAI_PDF_DETAIL` (`low`, `high`, or `auto`) for PDF page images.

Runtime safety limits also have optional local overrides:

- `OPENAI_RESPONSES_TIMEOUT_SECONDS` bounds each Responses API attempt;
- `RESEARCH_DAILY_TIME_BUDGET_SECONDS` sets the daily soft deadline; and
- `OPENAI_SYNTHESIS_CHUNK_MAX_ITEMS` and
  `OPENAI_SYNTHESIS_CHUNK_MAX_BYTES` bound each weekly/monthly request.

The checked-in reasoning defaults are `low` for Luna screening, `medium` for
Terra PDF and weekly analysis, and `high` for the monthly Sol synthesis. Each
has a matching `OPENAI_*_REASONING_EFFORT` override shown in `.env.example`.

`OPENAI_SYNTHESIS_MODEL` remains a backward-compatible fallback when neither
period-specific override is set.

`noAnnouncementDates` contains the official arXiv no-announcement dates for
the current calendar year. Refresh this small list from arXiv's
[availability schedule](https://info.arxiv.org/help/availability.html) when a
new year's schedule is published; these dates are treated like weekends rather
than outages.

The model contract contains classification, summary, main result, practical
application, methodology, limitations, importance 1–5, recommendation, reason,
and tags in Japanese and English. Paper identity, authors, dates, categories,
and links always come from validated arXiv metadata, not model output. Responses
are requested with storage disabled and validated locally again before use.
The API boundary follows OpenAI's [Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs)
and [file inputs](https://developers.openai.com/api/docs/guides/file-inputs)
contracts.

## Run the daily pipeline

From the repository root:

~~~bash
python scripts/research_pipeline.py \
  --config config/research.json \
  --env-file .env \
  daily \
  --state .local/research/state.json \
  --output-dir .local/research/daily
~~~

This produces matching `YYYY-MM-DD.json` and `YYYY-MM-DD.md` files under
**.local/research/daily/**. It updates state only after both files are written.
Re-running the same completed date is byte-stable; an incomplete attempt may be
replaced only by the later completed result for that date.

Validated abstract and PDF stages are atomically checkpointed under
**.local/research/checkpoints/**. If the 30-minute default soft deadline or a
remote failure interrupts a busy batch, the report remains incomplete and the
another run of that same latest batch resumes at the unfinished stage instead
of repeating completed API calls. Once a newer batch is expected, scheduled
daily runs move on to it. Each Responses attempt has a separate 120-second default timeout and SDK
retries are disabled; the pipeline's explicit retry policy is the only retry
layer. The daily budget plus the longest configured request timeout must leave
at least ten minutes inside the 90-minute Actions limit. Checkpoints keep
per-paper input fingerprints. A model/settings change or the addition of a new
candidate does not discard already validated analyses. If an analyzed paper's
source changes, or a checkpoint is malformed/incompatible, it is preserved and
the attempt stops without silently paying to analyze it again. Valid legacy
checkpoints can migrate only when their original whole-batch fingerprint matches.

Completed checkpoints are retained as the record of screened-out as well as
selected papers. Daily runs exclude paper IDs already present in older reports,
older checkpoint decisions (including old unfinished PDF stages), or the
validated public history supplied by `--published-history`. Matching is by base
arXiv ID, so later cross-listings/revisions do not trigger a daily re-evaluation.
A completed daily JSON is reused before any network/API call, even if state is
stale. Ratings and review prose are not regenerated. Publication reconciliation
only checks/repairs deterministic saved artifacts; it does not call OpenAI.

Daily states have distinct meanings:

- `UPDATE_CONFIRMED`: the expected batch was checked and selected papers were
  fully processed;
- `NO_RELEVANT_PAPERS`: the expected batch was checked successfully but no
  paper qualified;
- `NO_NEW_BATCH_EXPECTED`: the latest scheduled batch was already completed,
  such as on a weekend;
- `UPDATE_NOT_CONFIRMED`: the expected arXiv batch or structured analysis could
  not be validated, so completion does not advance; and
- `UPDATER_OFFLINE`: a required remote service was unavailable, so the batch
  remains pending.

Thus, an empty successful screen is never inferred from an arXiv or OpenAI
failure. The default daily target is the latest expected announcement batch,
not the oldest unfinished date. Old incomplete JSON/checkpoints are left intact
and never marked complete just because a newer review succeeded. Their missing
coverage remains visible in weekly/monthly reports. Historical recovery requires
an explicit local `daily --recover-pending` request (within arXiv's bounded
past-week coverage); the scheduled workflow never enables it. After a new batch
is completed, `lastCompletedBatchDate` records that latest completion, not a
claim that every intervening date was reviewed.

The checked-in launchers provide locking and local logs:

- Linux/WSL: `scripts/run_update.sh`
- Windows: `scripts/run_update.ps1`

They write only under ignored **.local/research/** and **.local/logs/** and do
not publish, commit, or push.

If the environment is not activated, run the shell launcher as
`PYTHON_BIN=.venv/bin/python scripts/run_update.sh` or pass the absolute venv
Python path to the PowerShell launcher's `-Python` option.

## Generate weekly and monthly reviews

Period reviews read stored daily JSON and do not fetch or analyze PDFs again:

~~~bash
python scripts/research_pipeline.py --env-file .env aggregate \
  --period weekly --period-end 2026-08-28 \
  --daily-dir .local/research/daily \
  --output-dir .local/research/reviews

python scripts/research_pipeline.py --env-file .env aggregate \
  --period monthly --period-end 2026-08-31 \
  --daily-dir .local/research/daily \
  --output-dir .local/research/reviews
~~~

Any incomplete daily report or missing configured announcement-date report
makes the period review `UPDATE_NOT_CONFIRMED`; completed daily papers may still
be reused, but the aggregate cannot overstate its coverage. Missing
weekend/holiday reports do not create a false research gap because no batch was
due, while an explicit failed report on one of those days still blocks
completion.

All stored papers are partitioned deterministically into bounded synthesis
chunks before any API request. The defaults are at most 20 papers and 200,000
UTF-8 prompt bytes per request. A single stored paper that exceeds the byte
budget fails locally before an API call rather than being truncated or silently
dropped. Chunk responses may contain only IDs from their own input chunk, and
the merged report is sorted independently of chunk order. Input truncation is
explicitly disabled and every response also has a bounded output-token budget.

## Publish a completed report locally

Append one completed report to the bilingual public history and regenerate the
site artifacts:

~~~bash
python scripts/research_publication.py \
  --report .local/research/daily/2026-08-28.json \
  --regenerate-site
~~~

The adapter is strict, append-only, and idempotent. A reused edition ID with
different content fails. Do not publish `UPDATE_NOT_CONFIRMED` or
`UPDATER_OFFLINE`; the scheduled workflow enforces this automatically.

Validate and preview the result:

~~~bash
python scripts/import_scheduler_history.py --check
python -m unittest discover -s tests -v
node --test tests/test_model_math.cjs tests/test_archive_ui.cjs
python -m http.server 8000 --directory site
~~~

Open **http://localhost:8000**. Use `?lang=en` for English and combine it with
an `edition` query to open a particular archive edition. Do not open
`site/index.html` directly because browser security rules can block its JSON
requests.

## Windows scheduling and reviewed-bundle sync

The Windows startup sync runs the local research pipeline, then checks for an
optional producer-complete reviewed public bundle. It never writes to `main`.
When a bundle exists, it validates exact hashes, creates an isolated worktree,
runs all tests, pushes a review branch, and opens a pull request.

Local prerequisites are Git, Python, Node.js, the OpenAI SDK, and the GitHub
CLI. Authenticate GitHub once, then install the task:

~~~powershell
gh auth login
$venvPython = (Resolve-Path .venv\Scripts\python.exe).Path
powershell -ExecutionPolicy Bypass `
  -File scripts/install_startup_sync_task.ps1 `
  -Python $venvPython
~~~

The default triggers are logon and 15:30 local time every day, with missed
starts enabled. Alternatives:

~~~powershell
# Only the logon trigger
powershell -ExecutionPolicy Bypass -File scripts/install_startup_sync_task.ps1 -StartupOnly

# Install and run now
powershell -ExecutionPolicy Bypass -File scripts/install_startup_sync_task.ps1 -RunNow

# Remove the task
powershell -ExecutionPolicy Bypass -File scripts/install_startup_sync_task.ps1 -Uninstall
~~~

A separate reviewed producer may still stage complete Japanese/source and
English snapshots through the existing hash-bound handoff:

~~~powershell
powershell -ExecutionPolicy Bypass `
  -File scripts/stage_public_review_bundle.ps1 `
  -HistoryPath <path-to-reviewed-history.json> `
  -TranslationPath <path-to-reviewed-en.json>
~~~

The consumer refuses incomplete, rewritten, reordered, untranslated, or unsafe
public bundles and opens a pull request rather than updating Pages directly.

## Enable GitHub Actions automation

The **Automated arXiv research** workflow runs entirely on GitHub-hosted
runners in the `Asia/Tokyo` time zone:

- daily at 15:30 JST for arXiv discovery, abstract screening, and selected-PDF
  analysis;
- weekly on Sunday at 08:00 JST for the seven days ending on the most recent
  Friday; and
- monthly on the first day at 08:00 JST for the complete previous calendar
  month.

The local computer and Codex app do not need to be running. GitHub may start a
scheduled workflow slightly after its nominal time. Overlapping daily, weekly,
and monthly runs share one queue so none is replaced by a later scheduled run.

1. Open repository **Settings → Secrets and variables → Actions**.
2. Add the repository secret `OPENAI_API_KEY`.
3. Under **Settings → Actions → General**, allow Actions and enable
   **Allow GitHub Actions to create and approve pull requests**.
4. Run the workflow once manually on `main` and confirm the **merge-and-publish**
   job and subsequent **Deploy GitHub Pages** run succeed.

No additional PAT or GitHub App secret is required. The separate merge job uses
the short-lived `GITHUB_TOKEN` with `contents: write`, `pull-requests: write`, and
`actions: write`; it receives no OpenAI key. The repository's **Allow auto-merge**
toggle is not needed: this job performs a normal, SHA-checked merge after the
trusted workflow's own validation, without bypassing repository rules. If you
later require a human approval or additional PR checks in branch rules, those
requirements will block automatic merging until they are satisfied.

Manual dispatch supports `daily`, `weekly`, and `monthly`. For recovery,
an optional explicit period end may be supplied: it must be a Friday for a
weekly review or the final calendar day for a monthly review.

The workflow persists `research/state.json`, candidate-stage checkpoints, and
daily JSON/Markdown on the fixed `automation/openai-arxiv-research` branch.
An incomplete daily attempt is persisted and offered in the review PR first,
then the Actions job is marked failed so a stalled batch is visible instead of
appearing as a successful empty review. The next daily run retries it
automatically.
Weekly and monthly outputs are stored under
`research/reviews/weekly/YYYY-MM-DD.{json,md}` and
`research/reviews/monthly/YYYY-MM-DD.{json,md}`. Period jobs reuse the daily
JSON and never fetch arXiv metadata or PDFs again, but they do call the
Responses API for synthesis using the configured weekly Terra/medium and
monthly Sol/high settings. A completed period review is detected before the
paid step and is reused without another API call. Each scheduled period run also
carries forward up to the two oldest incomplete reviews of the same kind,
alongside the current period, so transient failures are retried automatically
without relying on a manual reminder. Before any paid period synthesis, compact
queue markers are committed under `research/pending-periods/`. A runner timeout
therefore leaves a durable target for the next same-kind scheduled run. The
marker is removed only after a validated terminal aggregate report has been
persisted; an incomplete review keeps its marker and remains queued.

Reusing the durable branch lets the current daily batch resume without repeating
completed model calls. An older pending batch cannot block a newer batch, and
the workflow's public-history ID filter excludes past papers before metadata or
model calls. Every run reconciles all durable
completed daily, weekly, and monthly reports into public editions, so an
interruption after the research push is repaired later. Incomplete
JSON/Markdown remains visible in repository research state but is never appended to the
public archive.

The workflow runs the complete Python and Node test suite before paid research,
validates and pushes safe research state before attempting publication, then
validates generated public data again. It never pushes `main` directly. It
opens or updates one review pull request. Automatic merging is limited to
`github-actions[bot]` PRs from this repository's fixed automation branch into
`main`, containing only regular non-executable research/publication data files.
The validated head and base must still match; changed code, conflicts, a moved
base/head, or any failed validation leave the PR unmerged. Ordinary code PRs
remain outside this automation. Merge commits preserve the durable branch's
ancestry, so do not squash or delete that branch as part of the scheduled flow.

Expected arXiv/period incompleteness still marks the research job failed, but
only after all output checks have passed and a validated candidate has been
recorded. The separate merge job can persist that safe retry state and any
completed editions; it never publishes an incomplete edition as complete.

GitHub may show approval-required duplicate PR checks for a PR created with
`GITHUB_TOKEN`. The automatic path uses the tests and privacy checks inside the
trusted scheduled/manual research workflow, so those duplicate checks do not
need manual approval unless repository rules explicitly require them. See
[GitHub's token event behavior](https://docs.github.com/en/actions/concepts/security/github_token).

Because a `GITHUB_TOKEN` merge does not trigger a `push` workflow, the merge job
explicitly dispatches the unchanged **Deploy GitHub Pages** workflow on `main`.
Pages independently validates the repository, uploads only **site/**, and never
receives the OpenAI key. Human merges still deploy through the existing `push`
trigger. If merge succeeds but dispatch fails, rerunning **merge-and-publish**
verifies the existing merge and retries dispatch without merging twice or
calling OpenAI.

## Privacy and security boundary

Assume every tracked file, generated research report, workflow log, public
rating, and old Git commit is public. Never commit `.env`, API keys, raw API
responses, private prompts, task/thread IDs, internal citation tokens, emails,
workstation paths, private annotations, or unpublished research.

The research workflow sends public arXiv titles, abstracts, validated metadata,
and high-importance PDF URLs to OpenAI. It does not send repository credentials
or local private state. The deployed site remains static and contains no
analytics, tracking, accounts, or runtime secret.

Read [PRIVACY.md](PRIVACY.md) before publishing and
[SECURITY.md](SECURITY.md) before changing integrations or credentials.

## License

The project code is available under the [MIT License](LICENSE). arXiv paper
metadata and linked papers remain subject to their respective rights and terms.
