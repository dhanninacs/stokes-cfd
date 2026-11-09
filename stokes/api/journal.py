"""Journals: an append-only JSON-lines log of executed commands, replayable."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from stokes.errors import ValidationError

if TYPE_CHECKING:
    from stokes.api.session import Session

MAX_RECORD_BYTES = 1024 * 1024


class Journal:
    """Appends one record per mutating command. ``path`` may be None for in-memory journals."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self.records: list[dict[str, Any]] = []

    def append(self, command: str, params: dict[str, Any]) -> dict[str, Any]:
        record = {
            "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "command": command,
            "params": params,
        }
        self.records.append(record)
        if self.path is not None:
            line = json.dumps(record, ensure_ascii=False, default=str)
            with self.path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(line + "\n")
        return record

    @staticmethod
    def read(path: Path) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        with path.open(encoding="utf-8") as handle:
            for number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                if len(line) > MAX_RECORD_BYTES:
                    raise ValidationError(f"journal line {number} exceeds {MAX_RECORD_BYTES} bytes")
                try:
                    record = json.loads(line)
                except ValueError as exc:
                    raise ValidationError(f"journal line {number} is not JSON: {exc}") from exc
                if not isinstance(record, dict) or "command" not in record:
                    raise ValidationError(f"journal line {number} has no command")
                records.append(record)
        return records


CHANGE_ONLY = frozenset({"parameter.set", "parameter.remove"})
"""Commands that only change the model, so one held run may cover a whole stretch of them.

They are exactly the edits an operation re-runs on, and neither reads an operation's output. A
command that does — an operation create, a query, an export — ends the held stretch, so it sees the
work it depends on. Unknown commands are treated as depending on everything, which is the safe
default."""


def replay(session: Session, records: list[dict[str, Any]]) -> list[Any]:
    """Execute journal records in order; stops at the first error. Between records the session
    settles (:meth:`Session.settle`): a record that follows an operation runs once the operation
    has, as it did when the journal was written (ADR-0022 decision 10.1).

    A run of change-only records is settled ONCE, at the end of the run, with the debounce held:
    the journal carries no timing, so a replay cannot tell from the file that a burst of changes
    arrived inside one debounce window, and walking the burst takes longer than the window, so each
    timer would otherwise fire during the next record and force its own run. Holding them collapses
    the burst the way the recording session did. Before this a three-change burst ran the operation
    FOUR times against TWO in the original, for the same output (T-092).
    """
    results = []
    index = 0
    while index < len(records):
        record = records[index]
        params = record.get("params") or {}
        if not isinstance(params, dict):
            raise ValidationError("journal record params must be an object")
        command = str(record["command"])
        if command in CHANGE_ONLY:
            # Take the whole stretch of change-only records. The FIRST one is applied and settled on
            # its own, as the recording session did, and only the rest are held: the first change
            # landed in a session where the debounce had already run out — that is why it got its
            # own re-run — and the ones behind it arrived inside a single window and collapsed into
            # one. Holding the first as well would make the replay do LESS work than the session it
            # is reproducing, which is a different answer, not a faster one.
            end = index
            while end < len(records) and str(records[end].get("command")) in CHANGE_ONLY:
                end += 1
            first = records[index]
            first_params = first.get("params") or {}
            if not isinstance(first_params, dict):
                raise ValidationError("journal record params must be an object")
            results.append(session.execute(str(first["command"]), **first_params))
            results_extra = records[index + 1 : end]
            if results_extra:
                with session.hold_changes():
                    for held in results_extra:
                        held_params = held.get("params") or {}
                        if not isinstance(held_params, dict):
                            raise ValidationError("journal record params must be an object")
                        results.append(session.execute(str(held["command"]), **held_params))
            session.settle(coalesce=True)
            index = end
            continue
        results.append(session.execute(command, **params))
        session.settle(coalesce=True)
        index += 1
    return results
