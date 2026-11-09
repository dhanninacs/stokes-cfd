"""Write the capture setup scripts of the Solve panel (T-217).

Each is `tools/fake_run.js` followed by `.mailbox/shots/run-panel-shot.js` with one state set.

    python tools/make_run_shots.py

makes `.mailbox/shots/run-panel-<state>.js` for each state, ready for

    python -m stokes.app --theme dark --screenshot .mailbox/shots/run-panel-running.png \
      --setup .mailbox/shots/run-panel-running.js --exit-after 14000
"""

from __future__ import annotations

from pathlib import Path

STATES = ("idle", "ready", "running", "done", "log")


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    fake = (root / "tools" / "fake_run.js").read_text(encoding="utf-8")
    template = (root / ".mailbox" / "shots" / "run-panel-shot.js").read_text(encoding="utf-8")
    marker = 'const STATE = "running";'
    if marker not in template:
        raise SystemExit("run-panel-shot.js has no STATE line to change")
    for state in STATES:
        out = root / ".mailbox" / "shots" / f"run-panel-{state}.js"
        out.write_text(
            fake + "\n" + template.replace(marker, f'const STATE = "{state}";'), encoding="utf-8"
        )
        print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
