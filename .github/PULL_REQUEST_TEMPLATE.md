## What

One paragraph: the change and the milestone step it belongs to (`docs/06-roadmap.md`).

## Why

The decision or requirement it serves (ADR number, roadmap row, issue).

## How it was verified

- [ ] `ruff check .` and `ruff format --check .`
- [ ] `mypy`
- [ ] `pytest -q` (incl. perf budgets)
- [ ] `python tools/check_forbidden_imports.py`
- [ ] `python tools/check_ip.py`
- [ ] `python tools/audit_licenses.py` and `pip-audit`
- [ ] Numbers attached for any hot path touched

## Docs

- [ ] Docs updated (which?)
- [ ] ADR added or amended (which?)
- [ ] `CHANGELOG.md` line added

## Security checklist (when inputs, paths, processes, network or secrets are touched)

- [ ] New input validated and size-limited
- [ ] Paths containment-checked
- [ ] Subprocess: argv list, cwd, env, timeout, captured output
- [ ] Listener/client: localhost or tunnel, token
- [ ] Secrets in the keychain, scrubbed from logs
- [ ] New dependency registered and audited
