# Rates & Execution — scheduled arXiv research

Rates & Execution is a privacy-first static archive of scheduled arXiv research
reviews across electronic trading, fixed income, market microstructure, and
quantitative methods. The public site is available at
[beautysamurai.github.io/public-page](https://beautysamurai.github.io/public-page/).

The displayed daily screens and weekly reviews come from the sanitized public
history in **content/chatgpt_scheduler_history.json**. An offline importer
validates that source and deterministically generates **site/data/**. GitHub
Actions validates and deploys the already-generated static site; deployment
does not call ChatGPT, arXiv, or any other AI service.

The interface is Japanese by default. The **日本語 / English** control stores
English in the shareable `?lang=en` URL and preserves the selected archive
edition. English editorial copy is a reviewed overlay; official arXiv titles,
authors, identifiers, dates, links, and numerical ratings remain unchanged.

The site also has a separate theory-note area at **site/theory/**. Its index and
model pages are ordinary static directory routes, so they work on direct GitHub
Pages reloads. Shared interface copy lives in **site/i18n.js**, while
**site/static-page.js** preserves the selected language across those routes.
The first model shell is **site/theory/hjb/index.html**; its article body is
intentionally empty until the HJB explanation is written and reviewed.

## Source of truth

~~~text
ChatGPT scheduled-task response history
  -> public wording and privacy review
  -> content/chatgpt_scheduler_history.json
  -> scripts/import_scheduler_history.py
  -> site/data/latest.json + immutable edition archives
  -> reviewed site/data/i18n/en.json editorial translation overlay
  -> reviewed commit and push
  -> GitHub Pages
~~~

The source preserves the scheduled review's narrative, rankings when they were
explicitly assigned, ratings, limitations, and recommendation order. It omits
private task/thread identifiers, internal citation tokens, tracking parameters,
prompts, and account details. Archive filenames use unique edition IDs, so a
daily screen and weekly review can coexist on the same date.

The deterministic keyword updater remains available as a local candidate feed.
It is useful for discovery and freshness checks, but it is not allowed to
overwrite the reviewed scheduler history shown on the public site.

## Generate and preview the public site

Requirements:

- Python 3.12 or another version supported by the importer and tests
- a static HTTP server for previewing the site (Python is sufficient)

From the repository root:

~~~bash
python scripts/import_scheduler_history.py
python scripts/import_scheduler_history.py --check
python -m unittest discover -s tests -v
python -m http.server 8000 --directory site
~~~

Open **http://localhost:8000**. Avoid opening **site/index.html** directly
because browser security rules can prevent fetch requests from loading JSON.
Use **http://localhost:8000/?lang=en** for English, or combine `lang=en` with
an archive `edition` query. The bilingual coverage tests require every source
edition, paper, and rating label to have a matching English overlay entry.

The importer is network-free, rejects unknown fields and unsafe public text,
derives canonical arXiv links from each arXiv ID, refuses to rewrite a different
immutable archive, and writes deterministic JSON. **--check** is read-only and
fails when the committed artifacts differ from the reviewed source.

## Edition status and provenance

Every scheduler-backed edition exposes:

- **editionId**, **editionDate**, and **editionKind** (daily or weekly);
- a sanitized source label and the time the edition was imported;
- expected and observed arXiv batch dates when the scheduled response stated
  them;
- a weekly coverage period when applicable;
- the scheduled narrative plus structured paper metadata and ratings; and
- one of these explicit states:
  - **UPDATE_CONFIRMED** — a reviewed batch contains selected papers;
  - **NO_RELEVANT_PAPERS** — a fresh batch was checked and none qualified;
  - **NO_NEW_BATCH_EXPECTED** — no new batch was expected at that check time;
  - **UPDATE_NOT_CONFIRMED** — freshness could not be confirmed, so no empty
    result was asserted;
  - **UPDATER_OFFLINE** — the scheduled check reported a fetch failure; or
  - **WEEKLY_REVIEW** — a multi-day review and reprioritization.

An empty paper list therefore does not automatically mean failure.

## Run the local candidate updater

The checked-in launchers fetch public arXiv metadata and write only under
**.local/candidate-data/**, with logs under **.local/logs/**. Both paths are
ignored by Git. They deliberately do not publish, commit, or push.

Run directly:

~~~bash
python scripts/arxiv_digest.py \
  --output .local/candidate-data/latest.json \
  --archive-dir .local/candidate-data/archive
~~~

Or use **scripts/run_update.sh** from Linux/WSL and
**scripts/run_update.ps1** from Windows Task Scheduler. The updater reads
**config/topics.json**, applies a deterministic keyword score, and makes stale,
empty, or offline states visible. Candidate output must be reviewed against the
actual scheduled response before anything is added to the public history.

## Publish with GitHub Pages

1. Add only sanitized scheduled-task results to
   **content/chatgpt_scheduler_history.json**.
2. Run the importer, its read-only **--check**, and the full test suite.
3. Inspect **git diff -- content site/data** and run the privacy checklist in
   [PRIVACY.md](PRIVACY.md).
4. Commit the intended files and push to **main**.

The Pages workflow uploads only **site/** after validation. The repository has
no GitHub cron job and no deployment credentials in tracked files.

## Public-repository privacy boundary

Assume every tracked file, generated edition, workflow log, and old Git commit
is public. In particular, the full sanitized scheduled narrative and editorial
ratings in **content/** and **site/data/** are public. Never commit raw ChatGPT
exports, task/thread IDs, internal citations, prompts, private annotations,
emails, workstation paths, credentials, or unpublished research.

Read [PRIVACY.md](PRIVACY.md) before publishing and
[SECURITY.md](SECURITY.md) before adding integrations or credentials.

## License

The project code is available under the [MIT License](LICENSE). arXiv paper
metadata and linked papers remain subject to their respective rights and terms.
