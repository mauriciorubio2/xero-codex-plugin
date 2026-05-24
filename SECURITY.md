# Security Policy

## Supported Versions

This project is pre-1.0. Security fixes are released on the latest version only.

## Reporting A Vulnerability

Please report security issues privately by opening a GitHub security advisory on this repository, or by contacting the maintainer through the GitHub profile listed in the plugin manifest.

Do not open a public issue with OAuth tokens, tenant IDs, exported accounting data, invoices, reports, screenshots, logs, or other sensitive material.

## Sensitive Data Rules

- Never commit Xero exports, OAuth tokens, config files containing tokens, screenshots of accounting data, or customer/supplier financial records.
- Revoke the Xero app connection immediately if a refresh token may have been exposed.
- Rotate any client secret that was copied into shell history, logs, or a public repository.
- Prefer encrypted exports for anything that leaves the user's machine.

## Architecture Notes

See [docs/SECURITY_ARCHITECTURE.md](docs/SECURITY_ARCHITECTURE.md) for the plugin threat model, token storage design, export controls, and zero-knowledge limitations.
