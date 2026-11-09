"""The `overrides` of a `foam_case` node: recorded edits to its settings (ADR-0029 4, plan T-308).

## The stored form

`overrides` is an ordered JSON list of `{file, path, value}`:

* `file` - a case-relative path in the copy's `source` area, `/`-separated, such as
  `system/controlDict` or `0/U`;
* `path` - the key path inside the file, such as `["PISO", "nCorrectors"]`;
* `value` - a typed token, never raw text:
  a number, a boolean, a word (`"Euler"`), a list (`[1, 0, 0]` is `(1 0 0)`),
  `{"tokens": [...]}` for several values side by side (`{"tokens": ["uniform", [1, 0, 0]]}` is
  `uniform (1 0 0)`), or for a known physical key (`stokes.cases.keys`)
  `{"quantity": "0.5 [s]", "si": 0.5}`: the text as the user typed it and the SI number written.

One item per `(file, path)`: setting a key again replaces its item in place, so the list reads as
the case's current edits. The staged case is `stokes.compile.of14.external.write(copy, items)`.

## Why typed tokens

An override is the only way user text reaches a staged dictionary, so it must not be a way around
the `external` lint profile: a word cannot hold `#`, `$`, quotes, braces or a `;`, so it cannot
add a directive, a variable, code or a second entry.

Import rules: standard library, `stokes.errors`, `stokes.cases.keys` and
`stokes.compile.of14.external`; `stokes.units` inside the function that converts a quantity.
Headless.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from stokes.cases.keys import dimension_of
from stokes.compile.of14.external import (
    MAX_LIST_DEPTH,
    MAX_LIST_ITEMS,
    MAX_PATH,
    Override,
    OverrideError,
    Scalar,
    Tokens,
    Value,
    check_key,
    check_word,
)
from stokes.errors import ValidationError

MAX_OVERRIDES = 2000
MAX_FILE = 512


def check_file(file: object) -> str:
    """``file`` when it is a case-relative path an override may name."""
    if not isinstance(file, str) or not file or len(file) > MAX_FILE:
        raise OverrideError("an override needs a file path", code="case.override_file")
    parts = file.split("/")
    if (
        "\\" in file
        or ":" in file
        or "\x00" in file
        or any(part in ("", ".", "..") for part in parts)
    ):
        raise OverrideError(
            f"{file[:80]!r} is not a path inside the case",
            code="case.override_file",
            details={"file": file[:80]},
        )
    return file


def check_path(path: object) -> tuple[str, ...]:
    """``path`` as a tuple of keys, each a word or quoted name."""
    if (
        not isinstance(path, list | tuple)
        or not path
        or len(path) > MAX_PATH
        or not all(isinstance(key, str) for key in path)
    ):
        raise OverrideError(
            "an override needs a key path (a list of keys)", code="case.override_path"
        )
    return tuple(check_key(key) for key in path)


def decode(raw: Any, depth: int = 0) -> Value:  # Any: JSON from a node or a caller
    """The typed value of a stored or given JSON value. Raises `OverrideError`."""
    if depth > MAX_LIST_DEPTH:
        raise OverrideError("lists nest too deep", code="case.override_value")
    if isinstance(raw, bool | str):
        return check_word(raw) if isinstance(raw, str) else raw
    if isinstance(raw, int | float):
        if not math.isfinite(raw):
            raise OverrideError("a number must be finite", code="case.override_value")
        return raw
    if isinstance(raw, list):
        if len(raw) > MAX_LIST_ITEMS:
            raise OverrideError("the list is too long", code="case.override_value")
        return tuple(decode(item, depth + 1) for item in raw)
    if isinstance(raw, dict) and set(raw) == {"tokens"} and isinstance(raw["tokens"], list):
        items = tuple(decode(item, depth) for item in raw["tokens"])
        if not items or any(isinstance(item, Tokens) for item in items):
            raise OverrideError("tokens hold plain values", code="case.override_value")
        return Tokens(items)
    if isinstance(raw, dict) and set(raw) == {"quantity", "si"}:
        si = raw["si"]
        if (
            not isinstance(raw["quantity"], str)
            or isinstance(si, bool)
            or not isinstance(si, int | float)
        ):
            raise OverrideError(
                "a quantity is {quantity: text, si: number}", code="case.override_value"
            )
        if not math.isfinite(si):
            raise OverrideError("a number must be finite", code="case.override_value")
        return Scalar(float(si))
    raise OverrideError(
        "a value is a number, a boolean, a word, a list, {tokens} or {quantity}",
        code="case.override_value",
    )


def stored(file: str, path: Sequence[str], raw: Any) -> dict[str, Any]:  # Any: JSON
    """The item to record for a caller's value, converting a quantity on a known key to SI.

    A known physical key (`stokes.cases.keys`) takes quantity text (`"0.5 [s]"`) or a bare number,
    which is read in the dimension's SI unit; any other key takes a typed token, and quantity text
    there is refused with `case.override_unit` because Stokes cannot know the dimension.
    """
    file = check_file(file)
    keys = check_path(list(path))
    dimension = dimension_of(file, keys)
    text = raw.get("quantity") if isinstance(raw, dict) else None
    value: Any  # Any: JSON
    if dimension is None:
        if isinstance(raw, dict) and "quantity" in raw:
            raise OverrideError(
                f"{'/'.join(keys)} in {file} has no known dimension; give a plain number",
                code="case.override_unit",
            )
        decode(raw)
        value = raw
    elif isinstance(text, str):
        value = _quantity(text, dimension)
    elif isinstance(raw, str) or (isinstance(raw, int | float) and not isinstance(raw, bool)):
        value = _quantity(raw, dimension)
    else:
        raise OverrideError(
            f"{'/'.join(keys)} in {file} is a {dimension.replace('_', ' ')}: give a quantity "
            "such as '0.5 [s]' or a number in SI",
            code="case.override_unit",
        )
    return {"file": file, "path": list(keys), "value": value}


def _quantity(raw: str | float, dimension: str) -> dict[str, Any]:  # Any: JSON
    from stokes.units import DIMENSIONS, parse_quantity, to_si

    dim = DIMENSIONS[dimension]
    text = raw if isinstance(raw, str) else f"{raw!r} [{dim.si_unit}]" if dim.si_unit else repr(raw)
    try:
        si = to_si(parse_quantity(text, dim), dim)
    except ValidationError as exc:
        raise OverrideError(exc.message, code="case.override_unit", details=exc.details) from exc
    if not math.isfinite(si):
        raise OverrideError("a number must be finite", code="case.override_value")
    return {"quantity": text, "si": si}


def to_override(item: Any) -> Override:  # Any: one stored JSON item
    """The writer's `Override` for one stored item. Raises `OverrideError`."""
    if not isinstance(item, dict) or set(item) != {"file", "path", "value"}:
        raise OverrideError("an override is {file, path, value}", code="case.override")
    return Override(check_file(item["file"]), check_path(item["path"]), decode(item["value"]))


def parse_list(items: Any) -> list[Override]:  # Any: the stored JSON list
    """Every stored item as an `Override`, in order. Raises `OverrideError`."""
    if not isinstance(items, list):
        raise OverrideError("the overrides must be a list", code="case.override")
    if len(items) > MAX_OVERRIDES:
        raise OverrideError(f"more than {MAX_OVERRIDES} overrides", code="case.override")
    return [to_override(item) for item in items]


def upsert(
    items: Sequence[dict[str, Any]], new: dict[str, Any]
) -> list[dict[str, Any]]:  # Any: JSON
    """``items`` with ``new`` in place of the item for the same file and path, or appended."""
    out = [dict(item) for item in items]
    for index, item in enumerate(out):
        if item.get("file") == new["file"] and item.get("path") == new["path"]:
            out[index] = new
            return out
    out.append(new)
    if len(out) > MAX_OVERRIDES:
        raise OverrideError(f"more than {MAX_OVERRIDES} overrides", code="case.override")
    return out


def remove(
    items: Sequence[dict[str, Any]],  # Any: JSON
    file: str,
    path: Sequence[str] | None = None,
) -> list[dict[str, Any]]:  # Any: JSON
    """``items`` without the file's override at ``path``, or without all of the file's."""
    wanted = list(path) if path is not None else None
    return [
        dict(item)
        for item in items
        if not (item.get("file") == file and (wanted is None or item.get("path") == wanted))
    ]


def staged(project: Path, node_id: str, items: Any) -> dict[str, bytes]:  # Any: the stored list
    """The edited files of an imported case as they stage; every other file stages from the copy.

    For the executor: it sends the copy's `source` area with these written over it. Raises
    `OverrideError` or the store's `CaseStoreError`.
    """
    from stokes.cases.store import read_source
    from stokes.compile.foam_read import MAX_BYTES
    from stokes.compile.of14.external import write

    overrides = parse_list(items)
    files = {item.file: read_source(project, node_id, item.file, MAX_BYTES) for item in overrides}
    return write(files, overrides)
