# Security Architecture

This plugin handles sensitive financial data. Its security model is local-first, least-scope, and leak-resistant by default.

## Threat Model

The plugin is designed to reduce accidental exposure through:

- plaintext OAuth token files
- terminal scrollback and shell logs
- output files written into Git repositories
- dry-run payloads pasted into chats or issue trackers
- overly broad OAuth scopes
- accidental commits of exports or generated charts

It does not protect against a fully compromised local machine, malicious local processes running as the same user, or a user intentionally sharing exported Xero data.

## Zero-Knowledge Limits

There is no hosted service in this plugin, no telemetry, and no plugin-operated cloud storage. API calls go directly from the user's machine to Xero.

That is "local only", but it is not true zero-knowledge. Codex and the local helper must see the data the user asks them to read, summarize, graph, or write. The plugin therefore focuses on minimizing stored secrets, suppressing raw stdout, and making exports explicit and protected.

## OAuth And Scopes

The helper uses OAuth 2.0 authorization code with PKCE for desktop-style login. Default scopes are read-oriented:

- `offline_access`
- `openid`
- `profile`
- `email`
- `accounting.settings.read`
- `accounting.contacts.read`
- `accounting.transactions.read`
- `accounting.reports.read`

Write scopes are opt-in through `--write-scopes`. Because Xero scopes are additive, users should start read-only and reconnect with write scopes only when they need guarded write operations.

## Token Storage

OAuth access and refresh tokens are not stored in the JSON config by default.

On macOS, tokens are stored in the user's Keychain under service `codex-xero` and account `codex-xero:<profile>`. The config file stores only non-secret metadata such as client ID, redirect URI, scopes, selected tenant, token store type, and expiry metadata.

If no secure store is available, login fails unless the user explicitly sets:

```bash
export XERO_CODEX_ALLOW_PLAINTEXT_TOKENS=1
```

That fallback is intended for short-lived development only. It stores tokens in `~/.config/codex-xero/accounts.json` with file mode `0600`.

Legacy plaintext tokens are migrated to Keychain automatically when an auth command runs on macOS.

## Output Controls

Raw Xero records, reports, snapshots, analysis, and charts are treated as sensitive output.

Defaults:

- Raw stdout is suppressed unless `--unsafe-stdout` is passed.
- Output files are created with mode `0600`.
- Existing output files are not overwritten unless `--overwrite` is passed.
- Output inside a Git worktree is refused unless `--allow-git-output` is passed.
- Common Xero export filename patterns are ignored by `.gitignore`.

Use a private directory such as:

```bash
mkdir -p ~/xero-exports
python3 plugins/xero/scripts/xero.py export invoices --out ~/xero-exports/invoices.xero.json
```

## Encrypted Exports

For exports that may leave the machine, use:

```bash
export XERO_CODEX_EXPORT_PASSPHRASE="use-a-long-random-passphrase"
python3 plugins/xero/scripts/xero.py snapshot \
  --from 2026-01-01 \
  --to 2026-03-31 \
  --out ~/xero-exports/q1-snapshot.xero.enc \
  --encrypt
```

The helper uses the system `openssl` command with AES-256-CBC, PBKDF2, a salt, and SHA-256. This protects confidentiality at rest. It is not a replacement for a managed key vault or a full encrypted document workflow with tamper-evident signatures.

## Write Safety

All mutating API calls require `--yes`. Dry runs redact payload bodies by default and show a body summary and SHA-256 digest instead.

To review the full body locally:

```bash
python3 plugins/xero/scripts/xero.py request PUT Invoices \
  --body-file draft-invoice.json \
  --dry-run \
  --include-body
```

Only use `--include-body` in a trusted terminal session.

## Operational Guidance

- Keep Xero developer app client secrets in environment variables.
- Prefer short, named profiles for separate organisations or use cases.
- Use narrow date ranges for reports and exports.
- Do not commit export directories.
- Revoke Xero connections from Xero if a token or exported dataset may have been exposed.
- Treat generated SVG charts as sensitive if they contain amounts, trends, customer names, or supplier names.
