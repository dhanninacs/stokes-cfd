"""Edit the text of an imported case's files (ADR-0029 4; the in-app text editor).

An imported case's copy never changes. What the user edits is the node's `file_edits`: a list of
`{file, text}`, the whole text of a file as it should stage. A text edit wins over the key edits
(`overrides`) of the same file, so saving one drops those, in the same undo step.

**The text is linted before it is stored**, by the rules the agent applies when the case is staged
(`_checks`: the `external` profile), so the editor is not a way around them: a `#codeStream`, an
unprobed `libs` or a symbolic link is refused here with the findings, and never reaches a run. A
file must exist in the copy, be text (UTF-8, no NUL) and be at most `MAX_EDIT_BYTES`.

Commands: `case.file_read`, `case.file_write`, `case.file_revert`. Codes: `case.file_missing`,
`case.file_binary`, `case.file_too_large`, `case.file_lint`, `case.override_file`.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from stokes.api.commands.case import CASE_REF, _case_node, _checks
from stokes.api.commands.case_settings import FILE_REF
from stokes.api.registry import CommandContext, ParamDef, command
from stokes.errors import ValidationError
from stokes.model import Composite, Node, SetParam

#: The most text one edit may hold, in bytes.
MAX_EDIT_BYTES = 256 * 1024
#: The most files one case may carry an edit for.
MAX_EDITS = 64


def _edits(target: Node) -> list[dict[str, Any]]:  # Any: JSON
    value = target.params.get("file_edits") or []
    return [item for item in value if isinstance(item, dict)]


def _text(raw: bytes, file: str) -> str:
    if b"\x00" in raw:
        raise ValidationError(
            f"{file} is not a text file", code="case.file_binary", details={"file": file}
        )
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValidationError(
            f"{file} is not UTF-8 text", code="case.file_binary", details={"file": file}
        ) from exc


def _baseline(ctx: CommandContext, target: Node, file: str) -> bytes:
    """The file as it stages without a text edit: the copy with the key edits written over it."""
    from stokes.api.commands.case_settings import _parsed, _staged

    return _staged(ctx, target, file, _parsed(target))


@command("case.file_read", params=(CASE_REF, FILE_REF), mutates=False)
def case_file_read(ctx: CommandContext, node: str, file: str) -> dict[str, Any]:  # Any: JSON
    """One file of an imported case as text: the saved text edit, else what stages now.

    Returns `{file, text, edited, bytes}`; `edited` is true when a text edit is saved for it.
    """
    from stokes.cases.overrides import check_file

    target = _case_node(ctx, node)
    name = check_file(file)
    for item in _edits(target):
        if item.get("file") == name:
            text = str(item.get("text", ""))
            return {"file": name, "text": text, "edited": True, "bytes": len(text.encode())}
    raw = _baseline(ctx, target, name)
    if len(raw) > MAX_EDIT_BYTES:
        raise ValidationError(
            f"{name} is larger than the editor takes ({MAX_EDIT_BYTES // 1024} KB)",
            code="case.file_too_large",
            details={"file": name, "bytes": len(raw)},
        )
    return {"file": name, "text": _text(raw, name), "edited": False, "bytes": len(raw)}


@command(
    "case.file_write",
    params=(
        CASE_REF,
        FILE_REF,
        ParamDef("text", "string", description="The whole text of the file as it should stage."),
    ),
)
def case_file_write(
    ctx: CommandContext, node: str, file: str, text: str
) -> dict[str, Any]:  # Any: JSON
    """Record the text of a file as it should stage, as one undo step.

    The text is linted by the rules the agent applies at staging; a finding refuses it with
    `case.file_lint` and the first findings in `details.findings`. The key edits (`case.set`) of
    the same file are dropped, since the text now says everything the file holds.
    """
    from stokes.cases.overrides import check_file, remove

    project = ctx.session.require_project()
    target = _case_node(ctx, node)
    name = check_file(file)
    data = text.encode("utf-8")
    if len(data) > MAX_EDIT_BYTES or "\x00" in text:
        raise ValidationError(
            f"the text is too large or not text (limit {MAX_EDIT_BYTES // 1024} KB)",
            code="case.file_too_large",
            details={"file": name, "bytes": len(data)},
        )
    _baseline(ctx, target, name)  # the file must exist in the copy: case.file_missing otherwise
    lint, _ = _checks(ctx)
    with tempfile.TemporaryDirectory(prefix="stokes-edit-") as scratch:
        path = Path(scratch) / Path(name).name
        path.write_bytes(data)
        findings = lint(name, path)
    if findings:
        raise ValidationError(
            f"{name} is refused: {findings[0].get('message', 'a finding')}",
            code="case.file_lint",
            details={"file": name, "findings": findings[:5]},
        )
    edits = [item for item in _edits(target) if item.get("file") != name]
    edits.append({"file": name, "text": text})
    if len(edits) > MAX_EDITS:
        raise ValidationError(f"more than {MAX_EDITS} edited files", code="case.file_too_large")
    overrides = remove(
        [item for item in (target.params.get("overrides") or []) if isinstance(item, dict)], name
    )
    cmd = Composite(
        "edit file text",
        [SetParam(target.id, "file_edits", edits), SetParam(target.id, "overrides", overrides)],
    )
    ctx.session.undo.push(cmd, project)
    ctx.session.emit("node.changed", node_id=target.id, path=target.path, param="file_edits")
    ctx.session.emit("node.changed", node_id=target.id, path=target.path, param="overrides")
    return {"file": name, "bytes": len(data), "edits": len(edits)}


@command("case.file_revert", params=(CASE_REF, FILE_REF))
def case_file_revert(ctx: CommandContext, node: str, file: str) -> dict[str, Any]:  # Any: JSON
    """Drop the saved text of a file, as one undo step; the file stages from the copy again."""
    from stokes.cases.overrides import check_file

    project = ctx.session.require_project()
    target = _case_node(ctx, node)
    name = check_file(file)
    before = _edits(target)
    edits = [item for item in before if item.get("file") != name]
    if len(edits) != len(before):
        ctx.session.undo.push(SetParam(target.id, "file_edits", edits), project)
        ctx.session.emit("node.changed", node_id=target.id, path=target.path, param="file_edits")
    return {"file": name, "reverted": len(edits) != len(before), "edits": len(edits)}
