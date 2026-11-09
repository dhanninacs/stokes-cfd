"""What a results report contains, as a plain record the renderer turns into a file.

**Why the content is separate from the file**: a report is a set of FACTS - the run, its fields,
its residuals, its monitors - and a writer is one way to print them. Keeping the facts here means a
test can assert what a report says without parsing a document, and a second format needs no second
mapping.

**The brief's shape** (claude-win2's results item 5): *"a set of images + a numbers table"*. The
images are the caller's - the viewport screenshots - and are carried through as
`(caption, bytes)` so the report can name them without knowing how they were made.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ReportField:
    """One field of the run, as a report prints it."""

    name: str
    units: str
    min: float
    max: float


@dataclass(frozen=True)
class ReportImage:
    """A picture for the report, with the sentence that goes under it."""

    caption: str
    #: PNG or JPEG bytes, exactly as the caller made them.
    data: bytes


@dataclass(frozen=True)
class Report:
    """Everything a results report says, before any file is written."""

    title: str
    project: str
    run: str
    #: The run's state - `done`, `done_unconverged`, or whatever it was.
    state: str
    #: The solver's iterations, and whether it met its target.
    iterations: int
    converged: bool
    fields: tuple[ReportField, ...] = ()
    #: One row per monitor series: the name and its final value, with units.
    monitors: tuple[tuple[str, str], ...] = ()
    images: tuple[ReportImage, ...] = ()
    #: Anything the caller wants printed under the title - a note about the set-up, an author.
    notes: str = ""
    #: Extra key/value lines, in the order given, for facts this record does not name.
    facts: tuple[tuple[str, str], ...] = field(default_factory=tuple)


def report_from_run(
    *,
    project: str,
    run_id: str,
    record: Any,
    fields: dict[str, Any],
    residuals: dict[str, Any],
    images: tuple[ReportImage, ...] = (),
    notes: str = "",
) -> Report:
    """Build the report's facts from what the store already answers.

    **Every input is a dict the API already returns**, so a report cannot disagree with the panel:
    the fields come from `run.fields`, the residuals and iterations from `run.residuals`, and the
    state from the run's own record. A report that recomputed any of them would be a second answer
    to a question already answered.
    """
    report_fields = tuple(
        ReportField(
            name=str(f.get("name") or ""),
            units=str(f.get("units") or ""),
            min=float(f.get("min") or 0.0),
            max=float(f.get("max") or 0.0),
        )
        for f in (fields.get("fields") or [])
    )
    # **The count comes from the RUN, not from the residual log.** Measured: the audit's run has 64
    # iterations on its record and an EMPTY residual history, so counting the log's entries reported
    # "Iterations 0" beside a tree saying "converged, 64 iterations". The log says what the residual
    # plot drew; the record says what the solver did.
    counted = getattr(record, "iterations", None)
    from_log = len(residuals.get("iterations") or [])
    iterations = int(counted) if isinstance(counted, int) else from_log
    state = str(getattr(record, "state", "") or "")
    return Report(
        title=f"Results - {run_id}",
        project=project,
        run=run_id,
        state=state,
        iterations=iterations,
        converged=state == "done",
        fields=report_fields,
        images=images,
        notes=notes,
    )


def report_html(report: Report) -> str:
    """The report as **one self-contained HTML file**, images inlined as base64.

    **HTML, not PDF**: a PDF needs a renderer dependency the allow-list does not carry, and rule 2
    says a new dependency is a decision rather than an implementation detail. An HTML file opens in
    every browser, prints to PDF from the print dialog, and carries its own images - so it is a
    report a user can send without the project folder.

    **Every value is escaped**, because a project name and a field name are user text, and a report
    is read by people.
    """
    from base64 import b64encode
    from html import escape

    def row(label: str, value: str) -> str:
        return f"<tr><th>{escape(label)}</th><td>{escape(value)}</td></tr>"

    facts = "".join(
        row(label, value)
        for label, value in (
            ("Project", report.project),
            ("Run", report.run),
            ("State", report.state),
            ("Iterations", str(report.iterations)),
            ("Converged", "yes" if report.converged else "no"),
        )
    )
    body = [f"<h1>{escape(report.title)}</h1>", f"<table>{facts}</table>"]
    if report.notes:
        body.append(f"<p>{escape(report.notes)}</p>")
    if report.fields:
        rows = "".join(
            "<tr>"
            f"<td>{escape(f.name)}</td><td>{escape(f.units)}</td>"
            f"<td>{f.min:.6g}</td><td>{f.max:.6g}</td>"
            "</tr>"
            for f in report.fields
        )
        body.append(
            "<h2>Fields</h2><table><tr><th>Field</th><th>Units</th><th>Min</th><th>Max</th></tr>"
            f"{rows}</table>"
        )
    if report.monitors:
        rows = "".join(row(name, value) for name, value in report.monitors)
        body.append(f"<h2>Monitors</h2><table>{rows}</table>")
    for image in report.images:
        encoded = b64encode(image.data).decode("ascii")
        # **PNG, because that is what a viewport screenshot is**; a caller holding JPEG bytes would
        # need its own type, which is why the caption says which it is when it matters.
        body.append(
            f'<figure><img alt="{escape(image.caption)}" '
            f'src="data:image/png;base64,{encoded}" />'
            f"<figcaption>{escape(image.caption)}</figcaption></figure>"
        )
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8" />'
        f"<title>{escape(report.title)}</title>"
        "<style>body{font:14px/1.5 system-ui,sans-serif;margin:2rem auto;"
        "max-width:52rem;color:#111}"
        "table{border-collapse:collapse;margin:1rem 0}th,td{border:1px solid #ccc;padding:4px 8px;"
        "text-align:left}th{background:#f4f4f4}img{max-width:100%}figure{margin:1rem 0}</style>"
        f"</head><body>{''.join(body)}</body></html>"
    )
