"""The session's design tables (ADR-0016 points 1 and 9): ``StudyStore``.

Design points are not nodes. One ``StudyStore`` per session (``session.extensions["study"]``)
holds every study's ``StudyTable`` by study id; ``study_table`` has the rows and the files under
``<name>.stokes/studies/<study_id>/``. Tables load lazily, on first access, from the open
project's folder; a project without a folder keeps them in memory until it is saved. Opening or
closing a project drops them (``attach``); a save to a new folder carries every study's table
there (``saved``). Every change to a table is written at once when the project has a folder.

The store follows the project through the session's extension hooks (``project_opened``,
``project_saved`` and ``project_closed``, ``stokes.api.session.SessionExtension``), which call
``attach`` and ``saved``. Save as does not depend on which commands ran: the hook is told the
folder the project came from, and tables nothing has loaded yet are read from there.

Threads: the store's lock guards the tables and the running updates, so an update may run on a
worker thread while commands read its table. Nothing here touches the project tree.

Import rule: the standard library and the study layer; no numpy, nothing from ``stokes.api``
(``stokes.model`` only for the type of a hook's argument).
"""

from __future__ import annotations

import threading
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

from stokes.errors import StokesError, ValidationError
from stokes.explore.evaluate import CancelToken, PointResult
from stokes.explore.study_table import (
    MAX_ERROR_CHARS,
    MAX_POINTS,
    PointRow,
    StudyTable,
    TableMeta,
    read_table,
    table_dir,
    utc_now,
    write_table,
)

if TYPE_CHECKING:
    from stokes.model.project import Project


class StudyBusy(StokesError):
    """The study is being updated; wait for ``study.updated`` or call ``study.cancel``."""

    code = "explore.study_busy"


# ---------------------------------------------------------------- the store


class StudyStore:
    """Every study's design table for one session, and its running updates."""

    def __init__(self, folder: Path | None = None) -> None:
        self._lock = threading.RLock()
        self._folder = folder
        self._tables: dict[str, StudyTable] = {}
        self._runs: dict[str, CancelToken] = {}

    @property
    def folder(self) -> Path | None:
        return self._folder

    # ------------------------------------------------------------ project lifecycle
    def project_opened(self, project: Project, path: Path | None) -> None:
        """Session hook: ``project`` replaced the open project, now in ``path`` (None: no folder
        yet). Its predecessor's tables are dropped and its updates stopped."""
        self.attach(path)

    def project_saved(self, project: Project, path: Path, previous: Path | None) -> None:
        """Session hook: ``project`` was written to ``path``, having been in ``previous``.

        ``previous`` is what the session says, not what this store last heard: a store made
        during the save already counts the new folder as current, and still has to carry the
        tables of the old one."""
        study_ids = [node.id for node in project.nodes() if node.type == "study"]
        with self._lock:
            self._folder = previous
            self.saved(path, study_ids)

    def project_copied(self, project: Project, path: Path, previous: Path | None = None) -> None:
        """Session hook: a snapshot exists at ``path``. **This store does nothing, on purpose.**

        It keeps a folder of its own (`self._folder`), so following the copy would send the next
        table the user writes from the ORIGINAL session into the snapshot - which is what sending
        `project_saved` here did (Opus F3). The copy's tables are already in the copied folder."""
        del project, path, previous

    def project_closed(self) -> None:
        """Session hook: no project is open; its tables are dropped and its updates stopped."""
        self.attach(None)

    def attach(self, folder: Path | None) -> None:
        """A project was opened (or created) in ``folder``, or closed (``None``): stop its
        updates and forget its tables; they load again on first access."""
        with self._lock:
            for token in self._runs.values():
                token.cancel()
            self._runs.clear()
            self._tables.clear()
            self._folder = folder

    def saved(self, folder: Path, study_ids: Iterable[str]) -> list[str]:
        """The project was saved to ``folder``: write the tables it lacks; on a save to a new
        folder, carry every listed study's table there. Returns the ids written."""
        ids = list(study_ids)
        written: list[str] = []
        with self._lock:
            previous, self._folder = self._folder, folder
            if previous is not None and previous.resolve() != folder.resolve():
                for study_id in ids:
                    if study_id not in self._tables:
                        table, existed = read_table(table_dir(previous, study_id))
                        table.dirty = existed
                        self._tables[study_id] = table
                    else:
                        self._tables[study_id].dirty = True
            for study_id in ids:
                found = self._tables.get(study_id)
                if found is not None and found.dirty:
                    write_table(table_dir(folder, study_id), found)
                    found.dirty = False
                    written.append(study_id)
        return written

    # ------------------------------------------------------------ reading
    def _table(self, study_id: str) -> StudyTable:
        table = self._tables.get(study_id)
        if table is None:
            table = StudyTable()
            if self._folder is not None:
                table, _ = read_table(table_dir(self._folder, study_id))
            self._tables[study_id] = table
        return table

    def is_loaded(self, study_id: str) -> bool:
        with self._lock:
            return study_id in self._tables

    def snapshot(self, study_id: str) -> StudyTable:
        """A copy of the table (loaded on first access)."""
        with self._lock:
            return self._table(study_id).copy()

    def status(self, study_id: str) -> str:
        with self._lock:
            return self._table(study_id).status(running=study_id in self._runs)

    # ------------------------------------------------------------ changing the table
    def replace(self, study_id: str, meta: TableMeta, rows: Sequence[PointRow]) -> StudyTable:
        """Swap in a new table (``study.generate``); the old one is not read first."""
        if len(rows) > MAX_POINTS:
            raise ValidationError(f"{len(rows)} points; a table holds at most {MAX_POINTS}")
        with self._lock:
            self._idle(study_id)
            table = StudyTable(meta, [r.copy() for r in rows], dirty=True)
            self._tables[study_id] = table
            self._persist(study_id, table)
            return table.copy()

    def add_point(self, study_id: str, inputs: Mapping[str, float], meta: TableMeta) -> PointRow:
        """Append a pending point; ``meta`` carries the current columns."""
        with self._lock:
            table = self._table(study_id)
            if len(table.rows) >= MAX_POINTS:
                raise ValidationError(f"the table already holds {MAX_POINTS} points")
            row = PointRow(table.next_index(), dict(inputs))
            table.rows.append(row)
            self._columns(table, meta)
            table.dirty = True
            self._persist(study_id, table)
            return row.copy()

    def clear(
        self, study_id: str, indices: Sequence[int] | None, *, remove: bool, meta: TableMeta
    ) -> list[int]:
        """Reset points to pending (or remove them); every point when ``indices`` is None."""
        with self._lock:
            self._idle(study_id)
            if indices is None and remove:
                table = self._tables.get(study_id)
                touched = [] if table is None else [r.index for r in table.rows]
                table = StudyTable(replace(meta, design={}), [], dirty=True)
                self._tables[study_id] = table
                self._persist(study_id, table)
                return touched
            table = self._table(study_id)
            chosen = [r.index for r in table.rows] if indices is None else list(indices)
            for index in chosen:
                table.row(index)  # unknown index: a ValidationError before anything changes
            if remove:
                drop = set(chosen)
                table.rows = [r for r in table.rows if r.index not in drop]
            else:
                for index in chosen:
                    row = table.row(index)
                    row.status, row.outputs, row.error, row.run = "pending", {}, None, None
            self._columns(table, meta)
            table.dirty = True
            self._persist(study_id, table)
            return chosen

    def select(
        self, study_id: str, points: Sequence[int] | None, responses: Sequence[str]
    ) -> list[PointRow]:
        """The points an update evaluates: with ``None`` (all) every one that is not done or
        lacks a current response, otherwise the listed indices in table order."""
        with self._lock:
            table = self._table(study_id)
            if points is None:
                chosen = [
                    r
                    for r in table.rows
                    if r.status != "done" or any(n not in r.outputs for n in responses)
                ]
            else:
                wanted = set(points)
                unknown = sorted(wanted - {r.index for r in table.rows})
                if unknown:
                    raise ValidationError(
                        f"the table has no points {unknown}", details={"points": unknown}
                    )
                chosen = [r for r in table.rows if r.index in wanted]
            return [r.copy() for r in chosen]

    # ------------------------------------------------------------ updates
    def begin(self, study_id: str) -> CancelToken:
        """Register an update of ``study_id``; one at a time per study."""
        with self._lock:
            if study_id in self._runs:
                raise StudyBusy(
                    "the study is updating; wait for study.updated or call study.cancel",
                    details={"study": study_id},
                )
            self._table(study_id)
            token = CancelToken()
            self._runs[study_id] = token
            return token

    def running(self, study_id: str) -> bool:
        with self._lock:
            return study_id in self._runs

    def cancel(self, study_id: str | None = None) -> list[str]:
        """Ask running updates (one study's, or all) to stop after the current point."""
        with self._lock:
            ids = list(self._runs) if study_id is None else [s for s in self._runs if s == study_id]
            for sid in ids:
                self._runs[sid].cancel()
            return ids

    def start_point(self, study_id: str, index: int) -> None:
        with self._lock:
            self._table(study_id).row(index).status = "running"

    def record(self, study_id: str, result: PointResult) -> PointRow:
        """Store one point's result."""
        with self._lock:
            row = self._table(study_id).row(result.index)
            row.status = result.status
            row.outputs = dict(result.outputs) if result.status == "done" else {}
            row.error = result.error[:MAX_ERROR_CHARS] if result.error else None
            row.run = result.run
            return row.copy()

    def end(self, study_id: str, meta: TableMeta, engine: str) -> StudyTable:
        """Close an update: points started but never finished go back to pending, the table is
        stamped and written."""
        with self._lock:
            self._runs.pop(study_id, None)
            table = self._table(study_id)
            for row in table.rows:
                if row.status == "running":
                    row.status = "pending"
            self._columns(table, meta)
            table.meta.engine = engine
            table.meta.updated = utc_now()
            table.dirty = True
            self._persist(study_id, table)
            return table.copy()

    # ------------------------------------------------------------ helpers
    def _idle(self, study_id: str) -> None:
        if study_id in self._runs:
            raise StudyBusy(
                "the study is updating; cancel it first (study.cancel)", details={"study": study_id}
            )

    @staticmethod
    def _columns(table: StudyTable, meta: TableMeta) -> None:
        table.meta.variables = list(meta.variables)
        table.meta.responses = list(meta.responses)

    def _persist(self, study_id: str, table: StudyTable) -> None:
        """Write the table when the project has a folder; otherwise it waits for a save."""
        if self._folder is None:
            return
        write_table(table_dir(self._folder, study_id), table)
        table.dirty = False
