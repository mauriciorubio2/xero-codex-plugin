# Xero Codex Plugin

Xero is a free, open source Codex plugin for connecting Codex to a Xero organisation through OAuth 2.0.

It gives Codex a local helper for Xero Accounting API reads, guarded writes, protected exports, reports, invoice trend analysis, overdue stats, top-customer and supplier summaries, and SVG charts.

## What It Can Do

- Connect to Xero with OAuth 2.0 authorization code + PKCE.
- Store local profiles and selected Xero tenants, while keeping OAuth tokens in macOS Keychain by default.
- Read common Accounting API resources: accounts, contacts, invoices, bills, payments, bank transactions, credit notes, purchase orders, quotes, tracking categories, users, and journals.
- Pull common reports including Profit and Loss, Balance Sheet, Trial Balance, Bank Summary, Aged Receivables, and Aged Payables.
- Export Xero records to JSON or CSV for analysis with private file permissions, Git worktree guards, and optional encryption.
- Build accounting snapshots with organisation, accounts, invoices, bank transactions, and invoice analytics.
- Analyze monthly sales, bills, net invoiced amount, overdue invoices, receivables, payables, top customers, and top suppliers.
- Generate simple SVG charts from exported Xero data.
- Prepare safe starter payloads for contacts, invoices, bills, and payments.
- Perform generic write requests only after an explicit `--yes`, with `--dry-run` available for review.
- Suppress raw financial data on stdout unless `--unsafe-stdout` is explicitly passed.

## Requirements

- A Xero account and access to the organisation you want to connect.
- A Xero developer app with a localhost redirect URI such as `http://localhost:45009/callback`.
- The app's client ID. If you use a confidential app, keep the client secret in an environment variable.

## Install

From a local checkout:

```bash
codex plugin marketplace add /path/to/xero-codex-plugin
codex plugin add xero@xero-codex-plugin
```

From GitHub:

```bash
codex plugin marketplace add https://github.com/mauriciorubio2/xero-codex-plugin.git
codex plugin add xero@xero-codex-plugin
```

The marketplace manifest is at `.agents/plugins/marketplace.json`, and the plugin package is at `plugins/xero`.

## Configure

Create a Xero developer app, add `http://localhost:45009/callback` as a redirect URI, then connect:

```bash
export XERO_CLIENT_ID="your-xero-client-id"

python3 plugins/xero/scripts/xero.py auth login \
  --client-id "$XERO_CLIENT_ID" \
  --redirect-uri "http://localhost:45009/callback"
```

The default scopes are read-oriented: identity, offline access, accounting settings read, contacts read, transactions read, and reports read. Write scopes are opt-in because Xero scopes are additive.

For guarded write workflows, reconnect with write scopes:

```bash
python3 plugins/xero/scripts/xero.py auth login \
  --client-id "$XERO_CLIENT_ID" \
  --redirect-uri "http://localhost:45009/callback" \
  --write-scopes
```

If your app has a client secret, keep it out of files:

```bash
export XERO_CLIENT_SECRET="your-secret"
python3 plugins/xero/scripts/xero.py auth login \
  --client-id "$XERO_CLIENT_ID" \
  --client-secret-env XERO_CLIENT_SECRET
```

Check status and tenants:

```bash
python3 plugins/xero/scripts/xero.py auth status --json
python3 plugins/xero/scripts/xero.py auth tenants --json
python3 plugins/xero/scripts/xero.py auth select "<tenant-id-or-exact-name>"
```

On macOS, OAuth tokens are stored in Keychain. If no secure token store is available, login fails unless you deliberately opt into development-only plaintext token storage:

```bash
export XERO_CODEX_ALLOW_PLAINTEXT_TOKENS=1
```

## Use

Ask Codex naturally:

- "Connect my Xero account."
- "Export invoices from last quarter and chart monthly sales."
- "Show overdue Xero invoices by customer."
- "Pull the Profit and Loss and Balance Sheet for March."
- "Prepare a draft Xero invoice payload for this customer."

Use the helper directly:

```bash
mkdir -p ~/xero-exports
python3 plugins/xero/scripts/xero.py list invoices --all-pages --from 2026-01-01 --to 2026-03-31 --out ~/xero-exports/invoices.xero.json
python3 plugins/xero/scripts/xero.py export contacts --format csv --out ~/xero-exports/contacts.xero.csv
python3 plugins/xero/scripts/xero.py report profit-and-loss --from 2026-01-01 --to 2026-03-31 --out ~/xero-exports/profit-and-loss.xero.json
python3 plugins/xero/scripts/xero.py snapshot --from 2026-01-01 --to 2026-03-31 --out ~/xero-exports/snapshot.xero.json
python3 plugins/xero/scripts/xero.py analyze --input ~/xero-exports/snapshot.xero.json --out ~/xero-exports/analysis.xero.json
python3 plugins/xero/scripts/xero.py chart --input ~/xero-exports/snapshot.xero.json --metric sales --out ~/xero-exports/monthly-sales.xero.svg
```

Raw financial data is not printed to stdout by default. If you intentionally want terminal output, pass `--unsafe-stdout`.

Encrypted export example:

```bash
export XERO_CODEX_EXPORT_PASSPHRASE="use-a-long-random-passphrase"
python3 plugins/xero/scripts/xero.py snapshot \
  --from 2026-01-01 \
  --to 2026-03-31 \
  --out ~/xero-exports/q1-snapshot.xero.enc \
  --encrypt
```

Prepare a write payload and dry-run it:

```bash
python3 plugins/xero/scripts/xero.py template sales-invoice > draft-invoice.json
python3 plugins/xero/scripts/xero.py request PUT Invoices --body-file draft-invoice.json --dry-run
```

Dry-run bodies are redacted by default so invoice details do not land in terminal logs. To review the full payload in a trusted terminal session, add `--include-body`.

Execute only after checking the tenant, endpoint, and payload hash or local payload file:

```bash
python3 plugins/xero/scripts/xero.py request PUT Invoices --body-file draft-invoice.json --yes
```

## Privacy And Safety

OAuth tokens are stored in macOS Keychain by default. The local config at `~/.config/codex-xero/accounts.json`, or `XERO_CODEX_CONFIG` if set, stores non-secret metadata such as Xero client IDs, selected tenants, scopes, token store type, and expiry metadata.

Do not store Xero client secrets in this file. Use `--client-secret-env` and an environment variable.

This plugin does not include telemetry or a hosted backend. API calls go from your machine to Xero, but this is not true zero-knowledge: Codex and the local helper must see the data you ask them to read, summarize, graph, or write.

Xero data is financial data, so exports should be treated as sensitive files. Output files are created with mode `0600`, blocked inside Git worktrees by default, and can be encrypted with `--encrypt`.

Write requests require `--yes`. Codex should use `--dry-run` first and get explicit user confirmation before creating, updating, deleting, sending, or otherwise changing records in Xero.

See [SECURITY.md](SECURITY.md) and [docs/SECURITY_ARCHITECTURE.md](docs/SECURITY_ARCHITECTURE.md) for the threat model and security architecture.

## Development

Version history is tracked in [CHANGELOG.md](CHANGELOG.md). This project follows semantic versioning: patch releases for fixes, minor releases for backwards-compatible features, and major releases for breaking changes.

Run the tests with:

```bash
python3 -m unittest discover -s tests
```

Validate the plugin manifest with the Codex plugin creator validator:

```bash
python3 ~/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py plugins/xero
```

## Contributing

Contributions are welcome. Please keep changes focused, tested, and aligned with the OAuth-first and confirmation-first design of this plugin.

When you open a PR, include an elevator pitch or short summary at the top that explains what changed and why.

Good PRs should explain user-visible behavior changes, include tests for helper behavior, update docs or skill instructions, avoid unnecessary dependencies, and keep accounting privacy and write safety in mind.

## License

MIT. See [LICENSE](LICENSE).
