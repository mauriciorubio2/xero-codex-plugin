---
name: xero
description: Connect Codex to Xero through OAuth 2.0, select Xero organisations, read Accounting API resources and reports, perform guarded writes, export data, analyze invoice trends, and generate charts.
---

# Xero

Use this skill when the user asks Codex to connect to Xero, inspect accounting data, export Xero data, produce reports, analyze financial trends, graph sales or bills, create or update Xero records, or prepare accountant-friendly summaries.

This plugin uses Xero OAuth 2.0 and the Accounting API through the bundled helper at `../../scripts/xero.py` relative to this `SKILL.md`. It stores OAuth tokens locally and never asks for the user's Xero password.

## First Steps

Check connection state before using Xero:

```bash
python3 ../../scripts/xero.py auth status --json
```

If the user has not connected yet, they need a Xero developer app client ID and a localhost redirect URI registered in that app. Start with read-only scopes unless the user clearly needs write access:

```bash
python3 ../../scripts/xero.py auth login \
  --client-id "$XERO_CLIENT_ID" \
  --redirect-uri "http://localhost:45009/callback"
```

For write workflows, reconnect with write scopes:

```bash
python3 ../../scripts/xero.py auth login \
  --client-id "$XERO_CLIENT_ID" \
  --redirect-uri "http://localhost:45009/callback" \
  --write-scopes
```

List and select organisations:

```bash
python3 ../../scripts/xero.py auth tenants --json
python3 ../../scripts/xero.py auth select "<tenant-id-or-exact-name>"
```

## Read Workflows

Prefer `--json` for Codex parsing and use concrete date ranges for financial analysis.

Common resources:

```bash
python3 ../../scripts/xero.py list invoices --all-pages --from 2026-01-01 --to 2026-03-31 --json
python3 ../../scripts/xero.py list sales-invoices --all-pages --from 2026-01-01 --to 2026-03-31 --out sales.json
python3 ../../scripts/xero.py list contacts --all-pages --out contacts.csv --format csv
python3 ../../scripts/xero.py list bank-transactions --all-pages --from 2026-01-01 --to 2026-03-31 --json
```

Reports:

```bash
python3 ../../scripts/xero.py report profit-and-loss --from 2026-01-01 --to 2026-03-31 --json
python3 ../../scripts/xero.py report balance-sheet --date 2026-03-31 --json
python3 ../../scripts/xero.py report aged-receivables --json
python3 ../../scripts/xero.py report aged-payables --json
```

Snapshot and analysis:

```bash
python3 ../../scripts/xero.py snapshot --from 2026-01-01 --to 2026-03-31 --out xero-snapshot.json
python3 ../../scripts/xero.py analyze --input xero-snapshot.json --json
python3 ../../scripts/xero.py chart --input xero-snapshot.json --metric sales --out monthly-sales.svg
```

## Write Workflows

Treat all creates, updates, sends, deletes, and status changes as high-impact accounting actions.

- Use a template or user-provided JSON payload.
- Run `request ... --dry-run` first and show the user the method, path, tenant, and payload.
- Only execute the write with `--yes` after the user explicitly confirms.
- Keep writes narrow. Avoid broad updates and avoid changing approved records unless the user names the exact record and desired change.

Examples:

```bash
python3 ../../scripts/xero.py template sales-invoice > draft-invoice.json
python3 ../../scripts/xero.py request PUT Invoices --body-file draft-invoice.json --dry-run
python3 ../../scripts/xero.py request PUT Invoices --body-file draft-invoice.json --yes
```

Generic Accounting API calls:

```bash
python3 ../../scripts/xero.py request GET Contacts --param 'where=Name=="Example Customer"' --json
python3 ../../scripts/xero.py request POST Contacts --body-file contact.json --dry-run
```

## Safety Rules

- Do not ask for or store a Xero account password.
- Keep client secrets in environment variables, not config files.
- Do not mix currencies in narrative totals without calling that out.
- Prefer exported JSON or CSV for analysis before making claims about trends.
- For accountant-facing summaries, state the date range, source resources, tenant, and any data gaps.
- If OAuth scopes are insufficient, explain the missing permission and reconnect with the narrowest needed scopes.
