# Rates & Execution — arXiv Daily

Rates & Execution — arXiv Daily is a static, GitHub Pages-ready digest for selected arXiv topics. A
local Python updater fetches and scores public arXiv metadata, writes a JSON
snapshot under **site/data/**, and the browser renders that snapshot. GitHub
Actions only validates the repository and deploys the already-generated static
site; it does not run the updater, call arXiv, or call an AI service.

## How publishing works

~~~text
local scheduled task
  -> scripts/arxiv_digest.py
  -> site/data/latest.json (+ dated archive files)
  -> reviewed git commit and push
  -> GitHub Pages deployment
~~~

This split is intentional. Search terms, network access, and any future model
credentials stay under the control of the local machine. The public repository
contains only the configuration and generated content that you choose to
commit.

## Run locally

Requirements:

- Python 3.12 or another version supported by the updater and tests
- network access to arXiv when running the updater
- a static HTTP server for previewing the site (Python is sufficient)

From the repository root:

~~~bash
python scripts/arxiv_digest.py \
  --output site/data/latest.json \
  --archive-dir site/data/archive
python -m unittest discover -s tests -v
python -m http.server 8000 --directory site
~~~

Open **http://localhost:8000**. Avoid opening **site/index.html** directly
because browser security rules can prevent fetch requests from loading the JSON
data.

The updater reads **config/topics.json** when that file is present. Its public
configuration contains:

- **categories**: arXiv category identifiers to consider
- **keywords**: deterministic relevance terms
- **minimumScore**: the inclusion threshold
- **maxResults**: the maximum feed results examined
- **staleAfterDays**: maximum tolerated calendar-day gap between the expected
  and observed weekday batch dates; this public configuration uses **0** so an
  older observed batch is never presented as a confirmed no-results day

Unknown configuration keys are rejected so that misspellings do not silently
change the digest.

## Freshness and status

The generated snapshot makes update health visible rather than implying that an
old page is current:

- **checkedAt** and **generatedAt** are RFC 3339 UTC timestamps ending in **Z**.
- **expectedBatchDate** is the latest weekday on or before the UTC check date.
- **observedBatchDate** is the newest publication date seen in the Atom feed, or
  **null** when it cannot be determined.
- **status** is one of:
  - **UPDATE_CONFIRMED**: a fresh confirmed batch has qualifying papers;
  - **NO_RELEVANT_PAPERS**: a fresh confirmed batch has no qualifying papers;
  - **UPDATE_NOT_CONFIRMED**: the feed is stale, empty, malformed, or otherwise
    cannot confirm the expected batch;
  - **UPDATER_OFFLINE**: a network, timeout, or operating-system fetch failure
    prevented the check.

The UI displays status and timestamps. An empty relevant-paper list is therefore
not automatically an error.

## Daily scheduling

The checked-in launchers update local files and append logs under
**.local/logs/**. They deliberately do not commit or push. Review the generated
diff before publishing it.

### Windows Task Scheduler with WSL

Use this route when the checkout lives inside WSL:

1. Open **Task Scheduler** and choose **Create Task**.
2. Add a daily trigger at the desired local time. arXiv publication timing can
   vary around weekends and holidays, so the page's batch dates remain the
   source of truth.
3. Add an action with program **C:\Windows\System32\wsl.exe**.
4. Use arguments like the following, replacing the distribution and repository
   path with local values:

~~~text
-d Ubuntu-24.04 -- bash -lc "cd /home/<linux-user>/path/to/public-page && bash scripts/run_update.sh"
~~~

5. Enable **Run task as soon as possible after a scheduled start is missed** and
   select **Do not start a new instance** if the task is already running.

Run the same command once interactively before relying on the task. Check
**.local/logs/** and the visible timestamp/status in the site after each run.
The shell launcher uses **python3** by default; set **PYTHON_BIN** in the task
command if a different executable is required.

### Windows Task Scheduler with a Windows checkout

Use program **powershell.exe** with arguments:

~~~text
-NoProfile -ExecutionPolicy Bypass -File "C:\path\to\public-page\scripts\run_update.ps1"
~~~

Set **Start in** to the repository root. If the Python launcher is not named
**python**, pass it explicitly, for example **-Python py**.

### cron on Linux or WSL

Use an absolute repository path and call the script through Bash. Replace the
schedule and path with local values:

~~~cron
15 9 * * * cd /home/<linux-user>/path/to/public-page && /usr/bin/bash scripts/run_update.sh
~~~

The example time is illustrative, not a claim about arXiv availability.

## Publish with GitHub Pages

1. Run the updater locally and inspect **git diff -- site/data**.
2. Confirm that the snapshot contains no private notes, credentials, or local
   paths.
3. Commit the intended files and push to the repository's **main** branch.
4. In the GitHub repository settings, set Pages **Source** to **GitHub Actions**.

The Pages workflow uploads only **site/**. It calls the reusable validation
workflow first, retains read-only repository access during preparation, and
receives **pages: write** plus **id-token: write** only in the deployment job.
There is no scheduled GitHub workflow.

## Public-repository privacy boundary

Assume every tracked file, generated snapshot, workflow log, and old Git commit
is public. In particular:

- **config/topics.json** reveals the published digest's interests.
- Paper titles, authors, abstracts, scores, and links in **site/data/** are
  public.
- Never place API keys, cookies, prompts, private annotations, email addresses,
  workstation paths, or unpublished research in tracked files.
- Put local logs, caches, credentials, and experiments under **.local/**, which
  is ignored by Git. Ignoring a file does not protect it after it has been
  committed.

Read [PRIVACY.md](PRIVACY.md) before publishing and
[SECURITY.md](SECURITY.md) before adding integrations or credentials.

## License

The project code is available under the [MIT License](LICENSE). arXiv paper
metadata and linked papers remain subject to their respective rights and terms.
