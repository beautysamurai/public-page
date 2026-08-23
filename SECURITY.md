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
- arXiv access and any future AI/API processing run locally, never on a GitHub
  Actions schedule.
- The validation workflow has read-only repository access.
- The Pages workflow has read-only repository access while verifying and
  building. Only its deployment job receives **pages: write** and
  **id-token: write**; no repository-content write permission is granted.
- Local-only logs, caches, credentials, and experiments belong in **.local/**,
  which is ignored by Git.

These boundaries reduce exposure but do not make committed data private. Review
generated **site/data/** files before every push.

## Credential handling

The current updater does not require a repository secret. If a future local
integration needs credentials:

1. Store them outside tracked files, preferably in the operating system's
   credential store or in a file under **.local/** with restrictive permissions.
2. Pass them to the local process at runtime; do not serialize them into the
   generated snapshot or logs.
3. Do not add them to GitHub Actions unless the architecture and threat model
   are deliberately changed and documented.

If a credential is committed, assume it is compromised: revoke or rotate it
first, then remove it from the current tree and Git history. Adding the path to
**.gitignore** afterward is not sufficient.

## Dependency and content considerations

Keep GitHub Actions pinned to trusted publishers and review version changes.
Treat arXiv titles, author names, and abstracts as untrusted text: render them as
text, not HTML. External links should use safe browser attributes when opening a
new tab. Do not weaken the site's content-security posture to support an
unreviewed script or analytics provider.
