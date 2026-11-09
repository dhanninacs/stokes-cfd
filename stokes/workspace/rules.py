"""A small kit for validating JSON documents leniently: rules, findings, a checker.

A rule (:class:`Spec`) checks one value and returns it cleaned, or :data:`DROP`
when it is invalid; containers keep unknown keys and drop invalid entries.
Every decision is recorded on a :class:`Checker` as a :class:`Finding`, so a
caller can be lenient (files: report and go on) or strict (API writes: refuse
when a finding lands where it wrote). The workspace's own rules and its document
are in :mod:`stokes.workspace.schema`.

Standard library only.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Callable, Hashable, Mapping
from dataclasses import dataclass
from typing import Final, Literal

from stokes.workspace.paths import JSON, Loc, Segment, as_index, format_path

_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
UNKNOWN_KEY = "not part of the workspace schema; kept as it is"

FindingKind = Literal["invalid", "unknown", "notice"]


@dataclass(frozen=True)
class Finding:
    """One thing validation noticed.

    ``invalid``: the value was dropped (or replaced) and the layer below shows through.
    ``unknown``: a key this schema does not know, kept unchanged. ``notice``: information.
    """

    code: str
    kind: FindingKind
    path: Loc
    message: str
    scope: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "code": self.code,
            "kind": self.kind,
            "scope": self.scope,
            "path": format_path(self.path),
            "message": self.message,
        }


class _Drop:
    """Marks a value that validation removed."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "DROP"


DROP: Final = _Drop()


class Checker:
    """Collects findings while a document is checked. ``partial`` documents may omit keys."""

    def __init__(self, *, partial: bool) -> None:
        self.partial = partial
        self.findings: list[Finding] = []

    def invalid(self, loc: Loc, message: str, code: str = "workspace.invalid_value") -> _Drop:
        self.findings.append(Finding(code, "invalid", loc, message))
        return DROP

    def unknown(self, loc: Loc, message: str) -> None:
        self.findings.append(Finding("workspace.unknown_key", "unknown", loc, message))

    def notice(self, code: str, loc: Loc, message: str) -> None:
        self.findings.append(Finding(code, "notice", loc, message))


def show(value: JSON) -> str:
    """A short rendering of a value for messages; never the whole of a large value."""
    try:
        text = json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        text = type(value).__name__
    return text if len(text) <= 60 else text[:57] + "..."


def json_copy(value: JSON) -> JSON:
    """A deep copy of a JSON value. Raises ValueError for anything that is not JSON."""
    if isinstance(value, dict):
        copied: dict[str, JSON] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("object keys must be strings")
            copied[key] = json_copy(item)
        return copied
    if isinstance(value, list):
        return [json_copy(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("NaN and infinity are not JSON")
    if value is None or isinstance(value, str | int | float):
        return value
    raise ValueError(f"{type(value).__name__} is not a JSON value")


# ---------------------------------------------------------------------------------------- rules
class Spec:
    """A rule for one value: ``check`` returns the cleaned value or DROP; ``child`` walks paths."""

    def check(self, value: JSON, loc: Loc, ck: Checker) -> JSON:
        raise NotImplementedError

    def child(self, key: Segment) -> Spec | None:
        return None


@dataclass(frozen=True, eq=False)
class Choice(Spec):
    values: tuple[str, ...]

    def check(self, value: JSON, loc: Loc, ck: Checker) -> JSON:
        if isinstance(value, str) and value in self.values:
            return value
        return ck.invalid(loc, f"must be one of {', '.join(self.values)}; got {show(value)}")


class Flag(Spec):
    def check(self, value: JSON, loc: Loc, ck: Checker) -> JSON:
        if isinstance(value, bool):
            return value
        return ck.invalid(loc, f"must be true or false; got {show(value)}")


@dataclass(frozen=True, eq=False)
class Number(Spec):
    low: float
    high: float
    integer: bool = False

    def check(self, value: JSON, loc: Loc, ck: Checker) -> JSON:
        kind = "an integer" if self.integer else "a number"
        numeric = isinstance(value, int) if self.integer else isinstance(value, int | float)
        if isinstance(value, bool) or not numeric or not math.isfinite(value):
            return ck.invalid(
                loc, f"must be {kind} from {self.low:g} to {self.high:g}; got {show(value)}"
            )
        if not self.low <= value <= self.high:
            return ck.invalid(loc, f"must be from {self.low:g} to {self.high:g}; got {value:g}")
        return value


@dataclass(frozen=True, eq=False)
class Text(Spec):
    pattern: re.Pattern[str]
    what: str
    max_chars: int = 64

    def check(self, value: JSON, loc: Loc, ck: Checker) -> JSON:
        if isinstance(value, str) and len(value) <= self.max_chars and self.pattern.match(value):
            return value
        return ck.invalid(loc, f"must be {self.what}; got {show(value)}")


@dataclass(frozen=True, eq=False)
class Prose(Spec):
    """Free text for people (titles, descriptions): one line, length-limited."""

    max_chars: int

    def check(self, value: JSON, loc: Loc, ck: Checker) -> JSON:
        if (
            isinstance(value, str)
            and len(value) <= self.max_chars
            and not _CONTROL_RE.search(value)
        ):
            return value
        return ck.invalid(loc, f"must be one line of at most {self.max_chars} characters")


@dataclass(frozen=True, eq=False)
class Nullable(Spec):
    inner: Spec

    def check(self, value: JSON, loc: Loc, ck: Checker) -> JSON:
        return None if value is None else self.inner.check(value, loc, ck)

    def child(self, key: Segment) -> Spec | None:
        return self.inner.child(key)


@dataclass(frozen=True, eq=False)
class ListOf(Spec):
    """A list. Lists replace as a whole when layers merge, so every entry is a complete unit."""

    item: Spec
    max_items: int
    key: Callable[[JSON], Hashable] | None = None
    """Entries with equal keys are duplicates: the first stays, the others are removed."""
    min_items: int = 0
    fill: Callable[[int, JSON], None] | None = None
    """Fills optional fields of a cleaned entry from its position."""

    def check(self, value: JSON, loc: Loc, ck: Checker) -> JSON:
        if not isinstance(value, list):
            return ck.invalid(loc, f"must be a list; got {show(value)}")
        if len(value) > self.max_items:
            return ck.invalid(loc, f"has {len(value)} entries; at most {self.max_items}")
        out: list[JSON] = []
        seen: set[Hashable] = set()
        for index, entry in enumerate(value):
            cleaned = self.item.check(entry, (*loc, index), ck)
            if cleaned is DROP:
                continue
            if self.key is not None:
                identity = self.key(cleaned)
                if identity in seen:
                    ck.notice(
                        "workspace.duplicate", (*loc, index), f"duplicate {identity!r} removed"
                    )
                    continue
                seen.add(identity)
            out.append(cleaned)
        if len(out) < self.min_items:
            return ck.invalid(loc, f"needs at least {self.min_items} valid entries")
        if self.fill is not None:
            for index, entry in enumerate(out):
                self.fill(index, entry)
        return out

    def child(self, key: Segment) -> Spec | None:
        return self.item if as_index(key) is not None else None


@dataclass(frozen=True, eq=False)
class Obj(Spec):
    """An object with known keys. Unknown keys are kept and reported."""

    fields: Mapping[str, Spec]
    required: tuple[str, ...] = ()
    complete: bool = False
    """A unit (a list entry): required keys are checked even inside a partial document."""

    def check(self, value: JSON, loc: Loc, ck: Checker) -> JSON:
        if not isinstance(value, dict):
            return ck.invalid(loc, f"must be an object; got {show(value)}")
        out: dict[str, JSON] = {}
        for key, entry in value.items():
            spec = self.fields.get(key)
            if spec is None:
                ck.unknown((*loc, key), UNKNOWN_KEY)
                out[key] = json_copy(entry)
                continue
            cleaned = spec.check(entry, (*loc, key), ck)
            if cleaned is not DROP:
                out[key] = cleaned
        if self.complete or not ck.partial:
            missing = [key for key in self.required if key not in out]
            if missing:
                return ck.invalid(loc, f"needs {', '.join(missing)}")
        return out

    def child(self, key: Segment) -> Spec | None:
        return self.fields.get(key) if isinstance(key, str) else None


KeyVerdict = tuple[FindingKind | None, str]
"""(None, "") accepts a key; ("unknown", why) keeps it with a finding; ("invalid", why) drops it."""


@dataclass(frozen=True, eq=False)
class MapOf(Spec):
    """An object whose keys are data (action ids, stage names, preset names)."""

    key_rule: Callable[[str], KeyVerdict]
    value: Spec
    max_items: int
    dotted: bool = False
    """Keys may contain dots; a path's remaining segments then name one key."""

    def check(self, value: JSON, loc: Loc, ck: Checker) -> JSON:
        if not isinstance(value, dict):
            return ck.invalid(loc, f"must be an object; got {show(value)}")
        if len(value) > self.max_items:
            return ck.invalid(loc, f"has {len(value)} entries; at most {self.max_items}")
        out: dict[str, JSON] = {}
        for key, entry in value.items():
            verdict, why = self.key_rule(key)
            if verdict == "invalid":
                ck.invalid((*loc, key), why)
                continue
            if verdict == "unknown":
                ck.unknown((*loc, key), f"{why}; kept as it is")
            cleaned = self.value.check(entry, (*loc, key), ck)
            if cleaned is not DROP:
                out[key] = cleaned
        return out

    def child(self, key: Segment) -> Spec | None:
        return self.value if isinstance(key, str) else None
