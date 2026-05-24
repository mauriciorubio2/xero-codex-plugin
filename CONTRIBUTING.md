# Contributing

Thanks for helping improve the Xero Codex Plugin.

Before opening a pull request, please:

- Keep the change focused and easy to review.
- Run `python3 -m unittest discover -s tests`.
- Update documentation when behavior changes.
- Avoid unnecessary dependencies.
- Treat OAuth tokens, Xero exports, and accounting data as sensitive.
- Preserve the default security posture: no plaintext tokens unless explicitly requested for development, no raw stdout for financial records, private file permissions, and Git worktree output guards.

Every PR should begin with a short elevator pitch or summary of the change. Explain what you changed, why it matters, and anything the maintainer should know before merging.

For larger changes, open an issue first so the direction can be discussed.
