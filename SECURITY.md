# Security Policy

## Supported versions

Security fixes are applied to the latest released version of CS-Scout. Before
reporting an issue, please confirm that it is still reproducible on the latest
release or the current `main` branch.

## Reporting a vulnerability

Use **Report a vulnerability** on this repository's **Security** tab to create a
private vulnerability report. If GitHub private vulnerability reporting is not
available, do not publish technical details. Open a details-free issue asking
the repository owner to provide a private contact route, or use an existing
private contact method listed on the owner's GitHub profile.

Please include only the information needed to reproduce and assess the issue:

- the affected release or commit;
- the security impact and affected component;
- minimal, redacted reproduction steps; and
- any suggested mitigation, if known.

Do not exploit the issue beyond what is necessary to demonstrate it, access
other users' data, or disclose it publicly before a fix can be prepared.

## Never include sensitive data in a public report

Do **not** post any of the following in a public issue, discussion, pull request,
commit, screenshot, log, or test fixture:

- `CS_SCOUT_SECRET_KEY`, Bearer authorization headers, GitHub tokens, or other
  credentials;
- `.env` files or deployment configuration containing secrets;
- downloaded CS2 `.dem` files or private Demo download URLs;
- player output JSON, Steam IDs, usernames, replay paths, or analysis results
  that have not been explicitly approved for public sharing; or
- server logs, IP addresses, or filesystem paths that identify a deployment or
  player.

If a credential may have been exposed, revoke or rotate it immediately before
continuing the report. Replace all sensitive values with clearly marked dummy
data, and share any necessary Demo or player data only through the private
vulnerability report after confirming it is required.

## Appropriate security reports

Examples include authentication bypasses, unauthorized result access, unsafe
Demo URL fetching, archive extraction or path traversal flaws, remote code
execution, denial-of-service conditions, and exposure of player or deployment
data. Ordinary bugs without security or privacy impact can be reported through
the public issue tracker, provided the report contains no sensitive data.
