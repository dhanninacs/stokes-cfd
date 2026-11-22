"""The volume mesh at a section plane, cut by the mesh's host (`mesh.cut`) and kept for the viewer.

The Mesh stage's section draws the cells the plane passes through, outlined and coloured by region,
instead of the hollow a clipped boundary surface shows. The host does the cut (it holds the mesh and
VTK); this side asks for it **off the session thread**, keeps the last few planes, and answers
`cutting` until the cut is in, so a slow first read of a large mesh never stalls a command.

Headless: the reply's arrays travel as base64 of the host's own little-endian bytes, which the
front end views as typed arrays without parsing a number.
"""

from __future__ import annotations

import base64
import threading
from collections import OrderedDict
from typing import Any

from stokes.log import fields, get_logger

log = get_logger(__name__)

#: Planes kept per session: a user moving the slider revisits the last few.
KEEP = 8

#: Seconds a host may take for one cut. The first cut of a large mesh reads it (measured 13 s for
#: 1.23 M cells); later cuts of the same mesh take under a second.
CUT_TIMEOUT_S = 300.0

AXES = {"x": (1.0, 0.0, 0.0), "y": (0.0, 1.0, 0.0), "z": (0.0, 0.0, 1.0)}


def plane_of(axis: str, offset: float) -> dict[str, list[float]]:
    """The plane normal to `axis` at `offset` metres along it."""
    normal = AXES[axis]
    return {"origin": [offset * c for c in normal], "normal": list(normal)}


class MeshSections:
    """One session's mesh cuts: the cache, and at most one cut in flight at a time."""

    _lock = threading.Lock()
    _by_session: dict[int, MeshSections] = {}

    def __init__(self) -> None:
        self._held: OrderedDict[tuple[str, str, float], dict[str, Any]] = OrderedDict()
        self._errors: dict[tuple[str, str, float], str] = {}
        self._busy: set[tuple[str, str, float]] = set()
        self._guard = threading.Lock()

    @classmethod
    def of(cls, session: Any) -> MeshSections:
        """The session's sections, made on first use."""
        with cls._lock:
            found = cls._by_session.get(id(session))
            if found is None:
                found = cls._by_session[id(session)] = cls()
            return found

    def section(self, runner: Any, job: str, axis: str, offset: float) -> dict[str, Any]:
        """The cut if held; otherwise start it and answer `{"state": "cutting"}`."""
        key = (job, axis, round(offset, 9))
        with self._guard:
            held = self._held.get(key)
            if held is not None:
                self._held.move_to_end(key)
                return held
            error = self._errors.pop(key, None)
            if error is not None:
                return {"state": "failed", "message": error}
            if key not in self._busy:
                self._busy.add(key)
                threading.Thread(
                    target=self._cut, args=(runner, key), name="mesh-section", daemon=True
                ).start()
        return {"state": "cutting", "axis": axis, "offset": offset}

    def _cut(self, runner: Any, key: tuple[str, str, float]) -> None:
        job, axis, offset = key
        try:
            reply = runner.call(
                "mesh.cut", job=job, plane=plane_of(axis, offset), timeout=CUT_TIMEOUT_S
            )
            answer = _answer(reply, axis, offset)
        except Exception as exc:  # logged, and answered as the cut's failure
            log.warning("a mesh section failed", extra=fields(job=job), exc_info=True)
            with self._guard:
                self._busy.discard(key)
                self._errors[key] = str(exc) or type(exc).__name__
            return
        with self._guard:
            self._busy.discard(key)
            self._held[key] = answer
            while len(self._held) > KEEP:
                self._held.popitem(last=False)


def _b64(value: Any) -> str:
    return base64.b64encode(bytes(value or b"")).decode("ascii")


def _answer(reply: Any, axis: str, offset: float) -> dict[str, Any]:
    """The host's reply as the command's answer, sizes checked."""
    if not isinstance(reply, dict):
        raise ValueError("the host answered a mesh cut with something that is not a result")
    sizes = {
        "positions": 12,
        "triangles": 12,
        "edges": 8,
        "regions": 1,
    }
    for name, size in sizes.items():
        if len(bytes(reply.get(name) or b"")) % size:
            raise ValueError(f"the host's mesh cut has a malformed {name} array")
    triangles = len(bytes(reply.get("triangles") or b"")) // 12
    if len(bytes(reply.get("regions") or b"")) != triangles:
        raise ValueError("the host's mesh cut has a region count unlike its triangle count")
    names = reply.get("names")
    return {
        "state": "ready",
        "axis": axis,
        "offset": offset,
        "cells": int(reply.get("cells") or 0),
        "names": [str(n) for n in names] if isinstance(names, list) else [],
        "positions": _b64(reply.get("positions")),
        "triangles": _b64(reply.get("triangles")),
        "edges": _b64(reply.get("edges")),
        "regions": _b64(reply.get("regions")),
    }
