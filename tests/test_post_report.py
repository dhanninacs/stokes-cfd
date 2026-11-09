"""The results report's content and its HTML (claude-win2's results item 5).

**Two properties are worth testing and the rest is layout**: that the report's FACTS come from the
API's own answers rather than being recomputed, and that **user text is escaped** - a project name
is arbitrary characters and a report is read by people, so an unescaped name is a way to put markup
into a document.
"""

from __future__ import annotations

from types import SimpleNamespace

from stokes.post.report import Report, ReportField, ReportImage, report_from_run, report_html


def _record(state: str = "done") -> SimpleNamespace:
    return SimpleNamespace(state=state)


def test_the_facts_come_from_the_API_answers_not_a_recomputation() -> None:
    """**The report cannot disagree with the panel.** Fields, iterations and state are read from
    what `run.fields`, `run.residuals` and the run's record already say."""
    report = report_from_run(
        project="duct",
        run_id="r_abc",
        record=_record("done"),
        fields={"fields": [{"name": "U_mag", "units": "m/s", "min": 0.0, "max": 0.158}]},
        residuals={"iterations": [1, 2, 3]},
    )
    assert report.project == "duct"
    assert report.run == "r_abc"
    assert report.state == "done"
    assert report.converged is True
    # **Three iterations in the answer, three in the report** - not a stored count that could drift.
    assert report.iterations == 3
    assert [(f.name, f.units, f.max) for f in report.fields] == [("U_mag", "m/s", 0.158)]


def test_an_unconverged_run_says_so() -> None:
    """`done_unconverged` is a finished run that did not meet its target - a different fact from
    `done`, and a report that called it converged would be wrong where it matters most."""
    report = report_from_run(
        project="duct", run_id="r", record=_record("done_unconverged"), fields={}, residuals={}
    )
    assert report.converged is False
    assert report.state == "done_unconverged"


def test_every_value_is_ESCAPED() -> None:
    """A project name and a field name are user text: unescaped, they put markup in the document."""
    report = Report(
        title="<script>alert(1)</script>",
        project="a & b <b>",
        run="r_1",
        state="done",
        iterations=1,
        converged=True,
        fields=(ReportField(name="<i>x</i>", units="m/s", min=0.0, max=1.0),),
        notes="<img src=x onerror=alert(2)>",
    )
    html = report_html(report)
    # **The raw markup is nowhere in the output**, and the escaped form is. The first version passed
    # against an unescaped title because the escaped copy elsewhere in the page satisfied the
    # `in` check - so each is asserted ABSENT, which an unescaped value cannot pass.
    for raw in ("<script>", "</script>", "<b>", "<i>", "<img src=x"):
        assert raw not in html, f"unescaped {raw!r} reached the report"
    assert "&lt;script&gt;" in html
    assert "&amp; b &lt;b&gt;" in html


def test_an_image_is_inlined_so_the_file_stands_alone() -> None:
    """A report a user can send without the project folder: the pictures travel inside it."""
    report = Report(
        title="t",
        project="p",
        run="r",
        state="done",
        iterations=1,
        converged=True,
        images=(ReportImage(caption="the duct", data=b"\x89PNG\r\n"),),
    )
    html = report_html(report)
    assert "data:image/png;base64," in html
    assert "<figcaption>the duct</figcaption>" in html


def test_a_report_with_no_fields_or_images_still_renders() -> None:
    """The smallest report is a run's facts, and it must not need anything else to be valid."""
    report = Report(title="t", project="p", run="r", state="done", iterations=0, converged=True)
    html = report_html(report)
    assert html.startswith("<!doctype html>")
    assert "<h1>t</h1>" in html
    assert "Fields" not in html


def test_the_iteration_count_comes_from_the_RUN_not_the_residual_log() -> None:
    """**Measured on the audit's duct**: 64 iterations on the record, an EMPTY residual history.

    Counting the log's entries reported *"Iterations 0"* beside a tree reading *"converged, 64
    iterations"*. The log says what the residual plot drew; the record says what the solver did, and
    quoting the wrong one was wrong in the most visible line of the page.
    """
    report = report_from_run(
        project="duct",
        run_id="r",
        record=SimpleNamespace(state="done", iterations=64),
        fields={},
        residuals={"iterations": []},  # the log is empty; the run still ran 64
    )
    assert report.iterations == 64


def test_the_log_is_the_fallback_when_the_record_has_no_count() -> None:
    """A record from before the field existed still reports something rather than a false zero."""
    report = report_from_run(
        project="p",
        run_id="r",
        record=SimpleNamespace(state="done"),
        fields={},
        residuals={"iterations": [1, 2, 3]},
    )
    assert report.iterations == 3
