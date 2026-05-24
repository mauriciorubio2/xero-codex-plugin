# Changelog

## 0.2.0 - 2026-05-25

Security hardening release for sensitive financial data.

- Moved OAuth token storage out of plaintext config by default and into macOS Keychain when available.
- Added an explicit development-only plaintext token fallback through `XERO_CODEX_ALLOW_PLAINTEXT_TOKENS=1`.
- Added automatic migration of legacy plaintext tokens into Keychain on auth commands.
- Suppressed raw financial data on stdout by default for requests, lists, reports, snapshots, analysis, and charts.
- Redacted write dry-run bodies by default and added `--include-body` for explicit review.
- Added private `0600` file writes for sensitive outputs.
- Blocked sensitive output files inside Git worktrees unless `--allow-git-output` is explicitly passed.
- Added optional OpenSSL encrypted exports with `--encrypt`.
- Added security architecture and vulnerability reporting documentation.
- Expanded tests for redaction, output permissions, Git worktree guards, and token-store safety.

## 0.1.1 - 2026-05-25

- Fixed the local checkout install example to use a generic path instead of a maintainer-specific filesystem path.

## 0.1.0 - 2026-05-25

Initial experimental release of the Xero Codex Plugin.

- Added the Codex plugin manifest and marketplace entry.
- Added a Xero skill for OAuth setup, tenant selection, Accounting API reads, guarded writes, exports, reports, analysis, and charts.
- Added a dependency-light Python helper with OAuth 2.0 + PKCE login, token refresh, tenant listing and selection, generic API requests, resource listing, report fetching, JSON/CSV export, snapshots, invoice trend analysis, SVG chart generation, and safe write templates.
- Added README, contribution guidelines, MIT license, tests, and GitHub Actions.
