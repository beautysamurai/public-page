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
a fixed review branch and opens or updates a pull request. Only a reviewed merge
to `main` starts the existing GitHub Pages deployment.

## Architecture

~~~text
official arXiv new-list pages + validated export metadata
  -> abstract screening with strict Responses API JSON Schema
  -> full-PDF analysis only when importance >= configured threshold
  -> durable state + daily JSON/Markdown
  -> weekly/monthly synthesis from stored daily JSON
  -> scripts/research_publication.py
  -> content/chatgpt_scheduler_history.json + English overlay
  -> deterministic site/data generation
  -> review pull request
  -> merge to main
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

## Install and configure

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
The default PDF threshold is importance 3. These optional environment variables
override the configured models:

- `OPENAI_SCREENING_MODEL` for abstract screening;
- `OPENAI_FULL_TEXT_MODEL` for full-paper analysis; and
- `OPENAI_SYNTHESIS_MODEL` for weekly/monthly synthesis.

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
failure. If a scheduled weekday run was missed, or the new-list page advances
past a pending date, the pipeline first recovers the earliest unprocessed date
from arXiv's bounded past-week listing. It then queues the next missed
announcement date. If the pending date is no longer recoverable, state remains
incomplete rather than falsely advancing completion.

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
node --test tests/test_model_math.cjs
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

The **Automated arXiv research** workflow runs every day at 06:30 UTC (15:30
JST) and also supports manual dispatch.

1. Open repository **Settings → Secrets and variables → Actions**.
2. Add the repository secret `OPENAI_API_KEY`.
3. Under **Settings → Actions → General**, allow Actions and enable
   **Allow GitHub Actions to create and approve pull requests**.
4. Run the workflow once manually and review the pull request it creates.

The workflow persists `research/state.json`, daily JSON/Markdown, and due
Friday weekly or calendar-month-end monthly reviews on the fixed
`automation/openai-arxiv-research` branch. Reusing this branch lets an
unconfirmed batch survive to the next run. Completed reports are converted to
public editions; incomplete JSON/Markdown remains visible on the review branch
but is not appended to the public archive. The workflow runs the complete
Python and Node test suite before pushing and never pushes `main` directly.

Merging the review pull request triggers the unchanged **Deploy GitHub Pages**
workflow. The Pages job uploads only **site/** and never receives the OpenAI
key.

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
