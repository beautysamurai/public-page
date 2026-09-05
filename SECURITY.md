# Security policy

## Reporting a vulnerability

Please use the repository's **Security** tab and **Report a vulnerability** to
open a private GitHub Security Advisory. Do not include credentials, exploit
details, or private data in a public issue.

If private vulnerability reporting is not enabled, open a public issue that
contains only a non-sensitive request for a private contact channel. Do not post
the vulnerability details until a private channel is available.

Include the affected commit or page, reproduction conditions, impact, and any
suggested mitigation. There is no guaranteed response-time SLA, but reports
should be acknowledged and assessed before public disclosure whenever possible.

## Security model

- The deployed product is a static site. It has no account system, server-side
  application, or runtime repository credentials.
- arXiv and OpenAI processing can run locally or in the dedicated scheduled
  research workflow. That workflow has no pull-request trigger, receives the
  OpenAI key only from Actions secrets, and writes to a fixed review branch.
- The research workflow never pushes `main` directly. After all output tests,
  scope checks, and privacy scans pass, an isolated job may merge the exact
  validated bot-owned, same-repository data-only PR. It rechecks the base/head,
  file modes, and paths, and uses the normal merge API without a rules bypass.
  Code PRs remain outside automatic merging. Expected incomplete research may
  persist safe retry state, but incomplete editions are not published.
- The merge job receives no OpenAI key. Its additional `actions: write` grant
  is used to dispatch the existing Pages workflow after a bot-token merge.
- The validation workflow has read-only repository access.
- The Pages workflow has read-only repository access while verifying and
  building. Only its deployment job receives **pages: write** and
  **id-token: write**; no repository-content write permission is granted.
- Local-only logs, caches, credentials, and experiments belong in **.local/**,
  which is ignored by Git. Sanitized reports under tracked **research/** are
  public review artifacts, not private storage.

These boundaries reduce exposure but do not make committed data private. Review
generated **site/data/** files before every push.

## Credential handling

The Responses API pipeline requires `OPENAI_API_KEY` when candidate papers need
analysis:

1. For local use, store it in ignored `.env` or the operating-system credential
   store and pass it only at runtime.
2. For scheduled Actions, store it only as the repository secret named
   `OPENAI_API_KEY`; never place it in variables, workflow text, artifacts, or
   pull-request content.
3. Do not serialize credentials, request headers, or raw API responses into
   reports, state, site data, or logs.

If a credential is committed, assume it is compromised: revoke or rotate it
first, then remove it from the current tree and Git history. Adding the path to
**.gitignore** afterward is not sufficient.

## Dependency and content considerations

Keep GitHub Actions pinned to trusted publishers and review version changes.
Treat arXiv titles, author names, and abstracts as untrusted text: render them as
text, not HTML. Treat PDF content as untrusted model input as well: document
instructions cannot override the fixed developer prompt or strict schema. The
pipeline requests `store=false`, revalidates structured output locally, and
keeps arXiv identity fields outside model control. External links should use
safe browser attributes when opening a new tab. Do not weaken the site's
content-security posture to support an unreviewed script or analytics provider.
