"""Design-table rows and files (ADR-0016 points 1 and 9): ``PointRow``, ``TableMeta``,
``StudyTable`` and the three files of ``<name>.stokes/studies/<study_id>/``:

``points.jsonl``  one design point per line, in table order (the row format below)
``points.csv``    a mirror for spreadsheets: index, status, a column per variable and response
                  with its SI unit in the heading, then retain, run and error
``table.json``    how the table was made: format version, study type, sampler, seed, count,
                  the sampler's own metadata, the engine, timestamps, and the columns

A row::

    {"index": 0, "inputs": {"U_in": 1.0}, "outputs": {"dp_dyn": 499.0, "Q": 0.0028},
     "status": "done", "run": null, "retain": false, "error": null}

``index`` names a point for life (removing points leaves gaps); ``inputs`` and ``outputs`` are SI
values keyed by variable and response name; ``status`` is pending, running, done or failed;
``run`` is the run bundle a point produced (null for the ``function`` engine); ``retain`` asks to
keep that bundle (ADR-0016 point 3); ``error`` says why a point failed.

Each file is written atomically. Reads are size-limited, unknown row keys are refused and every
row is validated (``StudyTableError`` names the file and line); a row left running by an
interrupted update reads as pending. ``study_store`` keeps the tables of a session.

Import rule: the standard library and the study layer; no numpy.
"""

from __future__ import annotations

import json
import math
import os
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from stokes.errors import StokesError, ValidationError
from stokes.explore.definition import Column
from stokes.model.ids import is_id

ROW_STATUSES: tuple[str, ...] = ("pending", "running", "done", "failed")
FORMAT_VERSION = 1
STUDIES_DIR = "studies"
POINTS_FILE = "points.jsonl"
CSV_FILE = "points.csv"
TABLE_FILE = "table.json"
MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_LINE_BYTES = 1024 * 1024
MAX_POINTS = 100_000
"""Largest design table; a full factorial or a sample beyond it is refused."""
MAX_ERROR_CHARS = 2000
_ROW_KEYS = frozenset({"index", "inputs", "outputs", "status", "run", "retain", "error"})
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


class StudyTableError(StokesError):
    """A table file is unreadable or malformed, or cannot be written."""

    code = "explore.study_table"


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


# ---------------------------------------------------------------- rows and tables


def _numbers(value: Any, what: str) -> dict[str, float]:  # Any: untrusted JSON
    if not isinstance(value, dict):
        raise StudyTableError(f"{what} must be an object")
    out: dict[str, float] = {}
    for key, number in value.items():
        if number is None:
            continue
        if (
            not isinstance(key, str)
            or isinstance(number, bool)
            or not isinstance(number, int | float)
            or not math.isfinite(number)
        ):
            raise StudyTableError(f"{what} must map names to finite numbers")
        out[key] = float(number)
    return out


@dataclass
class PointRow:
    """One design point; see the module docstring for the JSON form."""

    index: int
    inputs: dict[str, float]
    outputs: dict[str, float] = field(default_factory=dict)
    status: str = "pending"
    run: str | None = None
    retain: bool = False
    error: str | None = None

    def to_json(self) -> dict[str, Any]:  # Any: JSON
        return {
            "index": self.index,
            "inputs": dict(self.inputs),
            "outputs": dict(self.outputs),
            "status": self.status,
            "run": self.run,
            "retain": self.retain,
            "error": self.error,
        }

    def copy(self) -> PointRow:
        return replace(self, inputs=dict(self.inputs), outputs=dict(self.outputs))

    @classmethod
    def from_json(cls, data: Any) -> PointRow:  # Any: untrusted JSON
        if not isinstance(data, dict):
            raise StudyTableError("a row must be an object")
        unknown = sorted(set(data) - _ROW_KEYS)
        if unknown:
            raise StudyTableError(f"a row has unknown keys {unknown}")
        index = data.get("index")
        if isinstance(index, bool) or not isinstance(index, int) or index < 0:
            raise StudyTableError("a row needs an integer index of 0 or more")
        status = data.get("status", "pending")
        if status not in ROW_STATUSES:
            raise StudyTableError(f"a row's status must be one of {', '.join(ROW_STATUSES)}")
        run, error, retain = data.get("run"), data.get("error"), data.get("retain", False)
        if run is not None and not isinstance(run, str):
            raise StudyTableError("a row's run must be a string or null")
        if error is not None and not isinstance(error, str):
            raise StudyTableError("a row's error must be a string or null")
        if not isinstance(retain, bool):
            raise StudyTableError("a row's retain flag must be true or false")
        return cls(
            index=index,
            inputs=_numbers(data.get("inputs", {}), "inputs"),
            outputs=_numbers(data.get("outputs", {}), "outputs"),
            # A row still running in a file is what an interrupted update leaves behind.
            status="pending" if status == "running" else str(status),
            run=run,
            retain=retain,
            error=error[:MAX_ERROR_CHARS] if error else None,
        )


def _column_json(columns: Sequence[Column]) -> list[dict[str, Any]]:  # Any: JSON
    return [c.to_json() for c in columns]


def _columns(raw: Any) -> list[Column]:  # Any: untrusted JSON
    if not isinstance(raw, list):
        return []
    found: list[Column] = []
    for item in raw:
        if isinstance(item, dict) and isinstance(item.get("name"), str):
            found.append(
                Column(
                    str(item["name"]),
                    str(item.get("dimension", "dimensionless")),
                    str(item.get("unit", "")),
                    str(item.get("source", "")),
                    str(item.get("label", "")),
                )
            )
    return found


def json_safe(value: Any) -> Any:  # Any: JSON in, JSON out
    """``value`` with non-finite floats as null, so that strict JSON parsers accept it."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [json_safe(v) for v in value]
    return value


@dataclass
class TableMeta:
    """How a table was made, and its columns as of the last change."""

    type: str = "sweep"
    sampler: str | None = None
    seed: int | None = None
    count: int | None = None
    design: dict[str, Any] = field(default_factory=dict)  # Any: the sampler's JSON metadata
    engine: str | None = None
    generated: str | None = None
    updated: str | None = None
    variables: list[Column] = field(default_factory=list)
    responses: list[Column] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:  # Any: JSON
        return {
            "version": FORMAT_VERSION,
            "type": self.type,
            "sampler": self.sampler,
            "seed": self.seed,
            "count": self.count,
            "design": json_safe(self.design),
            "engine": self.engine,
            "generated": self.generated,
            "updated": self.updated,
            "variables": _column_json(self.variables),
            "responses": _column_json(self.responses),
        }

    @classmethod
    def from_json(cls, data: Any) -> TableMeta:  # Any: untrusted JSON
        if not isinstance(data, dict):
            raise StudyTableError(f"{TABLE_FILE} must hold an object")
        version = data.get("version")
        if not isinstance(version, int) or version > FORMAT_VERSION:
            raise StudyTableError(
                f"{TABLE_FILE} has format {version!r}; this Stokes reads up to {FORMAT_VERSION}"
            )

        def text(key: str) -> str | None:
            value = data.get(key)
            return value if isinstance(value, str) else None

        def integer(key: str) -> int | None:
            value = data.get(key)
            return value if isinstance(value, int) and not isinstance(value, bool) else None

        design = data.get("design")
        return cls(
            type=text("type") or "sweep",
            sampler=text("sampler"),
            seed=integer("seed"),
            count=integer("count"),
            design=dict(design) if isinstance(design, dict) else {},
            engine=text("engine"),
            generated=text("generated"),
            updated=text("updated"),
            variables=_columns(data.get("variables")),
            responses=_columns(data.get("responses")),
        )


@dataclass
class StudyTable:
    meta: TableMeta = field(default_factory=TableMeta)
    rows: list[PointRow] = field(default_factory=list)
    dirty: bool = False

    def copy(self) -> StudyTable:
        meta = replace(
            self.meta,
            design=dict(self.meta.design),
            variables=list(self.meta.variables),
            responses=list(self.meta.responses),
        )
        return StudyTable(meta, [r.copy() for r in self.rows], self.dirty)

    def counts(self) -> dict[str, int]:
        counts = dict.fromkeys(ROW_STATUSES, 0)
        for row in self.rows:
            counts[row.status] += 1
        return counts

    def status(self, *, running: bool = False) -> str:
        """The study ``status`` this table implies."""
        if running:
            return "running"
        if not self.rows:
            return "idle"
        counts = self.counts()
        if counts["failed"]:
            return "failed"
        return "done" if counts["done"] == len(self.rows) else "generated"

    def row(self, index: int) -> PointRow:
        for row in self.rows:
            if row.index == index:
                return row
        raise ValidationError(f"the table has no point {index}", details={"index": index})

    def next_index(self) -> int:
        return max((r.index for r in self.rows), default=-1) + 1

    def to_json(self) -> dict[str, Any]:  # Any: JSON
        return {**self.meta.to_json(), "points": [r.to_json() for r in self.rows]}


# ---------------------------------------------------------------- files


def table_dir(folder: Path, study_id: str) -> Path:
    """``<folder>/studies/<study_id>``; the id is checked, so no path comes from user text."""
    if not is_id(study_id):
        raise StudyTableError(f"{study_id!r} is not a node id", details={"study": study_id})
    root = (folder / STUDIES_DIR).resolve()
    directory = (root / study_id).resolve()
    if not directory.is_relative_to(folder.resolve()):
        raise StudyTableError(f"{directory} lies outside the project folder")
    return directory


def write_text(path: Path, text: str) -> None:
    """Write ``text`` atomically (a temporary file in the same folder, then a replace)."""
    import tempfile  # lazy, as in stokes.model.persist: it pulls in shutil, bz2 and lzma

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.stem}-", suffix=path.suffix, dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        Path(temporary).replace(path)
    except OSError as exc:
        Path(temporary).unlink(missing_ok=True)
        raise StudyTableError(f"cannot write {path}: {exc}") from exc


def _safe_text(text: str) -> str:
    """A text cell that no spreadsheet will take for a formula."""
    return f"'{text}" if text.startswith(_FORMULA_START) else text


def _heading(column: Column) -> str:
    return f"{column.name} [{column.unit}]" if column.unit else column.name


def _cell(value: float | None) -> str:
    return "" if value is None else repr(float(value))


def csv_text(
    rows: Sequence[PointRow], variables: Sequence[Column], responses: Sequence[Column]
) -> str:
    """The table as CSV: SI values, units in the headings."""
    import csv
    import io

    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(
        ["index", "status"]
        + [_heading(c) for c in variables]
        + [_heading(c) for c in responses]
        + ["retain", "run", "error"]
    )
    for row in rows:
        writer.writerow(
            [str(row.index), row.status]
            + [_cell(row.inputs.get(c.name)) for c in variables]
            + [_cell(row.outputs.get(c.name)) for c in responses]
            + [
                "true" if row.retain else "false",
                _safe_text(row.run or ""),
                _safe_text(row.error or ""),
            ]
        )
    return buffer.getvalue()


def write_table(directory: Path, table: StudyTable) -> None:
    lines = "".join(
        json.dumps(row.to_json(), ensure_ascii=False, allow_nan=False) + "\n" for row in table.rows
    )
    write_text(directory / POINTS_FILE, lines)
    write_text(
        directory / CSV_FILE, csv_text(table.rows, table.meta.variables, table.meta.responses)
    )
    write_text(
        directory / TABLE_FILE,
        json.dumps(table.meta.to_json(), indent=2, ensure_ascii=False, allow_nan=False) + "\n",
    )


def _read_bytes(path: Path) -> str:
    try:
        size = path.stat().st_size
        if size > MAX_FILE_BYTES:
            raise StudyTableError(f"{path} is {size} bytes; the limit is {MAX_FILE_BYTES}")
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise StudyTableError(f"cannot read {path}: {exc}") from exc


def read_table(directory: Path) -> tuple[StudyTable, bool]:
    """The table in ``directory`` and whether it had one; an empty table when it does not."""
    points, meta_file = directory / POINTS_FILE, directory / TABLE_FILE
    if not points.is_file() and not meta_file.is_file():
        return StudyTable(), False
    meta = TableMeta()
    if meta_file.is_file():
        try:
            meta = TableMeta.from_json(json.loads(_read_bytes(meta_file)))
        except ValueError as exc:
            raise StudyTableError(f"{meta_file} is not JSON: {exc}") from exc
    rows: list[PointRow] = []
    seen: set[int] = set()
    text = _read_bytes(points) if points.is_file() else ""
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        where = f"{points} line {number}"
        if len(line) > MAX_LINE_BYTES:
            raise StudyTableError(f"{where} is longer than {MAX_LINE_BYTES} bytes")
        try:
            row = PointRow.from_json(json.loads(line))
        except ValueError as exc:
            raise StudyTableError(f"{where} is not JSON: {exc}") from exc
        except StudyTableError as exc:
            raise StudyTableError(f"{where}: {exc.message}") from exc
        if row.index in seen:
            raise StudyTableError(f"{where} repeats point {row.index}")
        if len(rows) >= MAX_POINTS:
            raise StudyTableError(f"{points} holds more than {MAX_POINTS} points")
        seen.add(row.index)
        rows.append(row)
    return StudyTable(meta, rows), True
