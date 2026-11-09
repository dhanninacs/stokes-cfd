# Contributing to Stokes

**Stub.** The owner has not yet decided whether outside contributions are accepted, or on what
terms. Until this file says otherwise, please open an issue before sending a pull request.

Stokes is licensed under the PolyForm Noncommercial License 1.0.0 (`LICENSE`). Forks for personal,
noncommercial use are welcome under those terms.

If contributions are accepted, they follow the repository's own rules:

- `CLAUDE.md` is the constitution: the non-negotiables (no GPL code in the application, the
  dependency allow-list, no employer IP, API first, headless core) apply to every change.
- Coding, testing and workflow standards are in `docs/engineering/`.
- Every gate in `CLAUDE.md` §9 must pass: ruff, mypy, pytest, `tools/check_forbidden_imports.py`,
  `tools/check_ip.py`, `tools/audit_licenses.py` and pip-audit.
- Commits use conventional commit messages; user-visible changes add a line to `CHANGELOG.md`.

<!-- Owner to decide: contributor licence terms (for example a CLA or a DCO sign-off), since the
     copyright holder needs to be able to grant commercial licences that cover contributed code. -->
