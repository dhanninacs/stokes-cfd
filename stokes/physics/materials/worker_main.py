"""What runs inside the materials worker process (ADR-0018 decision 6; the parent's side is
:mod:`stokes.physics.materials.worker`).

The worker answers provider questions for the API process, which never imports CoolProp or
Cantera: CoolProp holds the GIL in every call and for seconds while it loads its fluid library,
so in the API process it would stall every thread. It serves one request at a time, so REFPROP
(not re-entrant) is serialised by construction as well as by its lock.

Every message is one ``send_bytes`` frame holding a UTF-8 JSON object; nothing is pickled after
the hand-over of :func:`child_main` at spawn. Requests (``type``)::

    warm       {providers}                      import the providers now
    providers  {refprop_path}                   versions, fluid counts, REFPROP detection
    index      {provider, refprop_path}         every fluid of a provider as library records
    info       {provider, fluid, composition, basis, phase, T_ref, p_ref, refprop_path}
    evaluate   {provider, fluid, composition, basis, phase, T: [...], p, refprop_path}

Replies are ``{"type": "ok", ...}`` or ``{"type": "error", "code", "message", "details"}``;
non-finite numbers travel as null. The process exits when its pipe closes.

Import rule: the standard library and this package; the providers load lazily per request.
"""

from __future__ import annotations

import json
import math
import os
import traceback
from multiprocessing.connection import Connection
from typing import Any

MAX_REQUEST_BYTES = 4 * 1024 * 1024
MAX_REPLY_BYTES = 64 * 1024 * 1024
REQUESTS: tuple[str, ...] = ("warm", "providers", "index", "info", "evaluate")
_refprop: dict[str, Any] = {}  # Any: JSON; the worker's one REFPROP detection, kept for life


def _clean(value: Any) -> Any:  # Any: JSON in, JSON out
    """Non-finite floats as null, recursively, so the frame is strict JSON."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_clean(v) for v in value]
    return value


def _refprop_detection(path: str | None) -> dict[str, Any]:  # Any: JSON
    """Detect and load REFPROP once for the life of this process."""
    if _refprop:
        return _refprop
    from stokes.physics.materials.providers import coolprop, refprop

    try:
        detection = refprop.load(path, os.environ, coolprop.module()).to_json()
    except ImportError as exc:
        detection = refprop.Detection(False, reason=f"CoolProp is not installed: {exc}").to_json()
    detection["settings_path"] = path
    _refprop.update(detection)
    return _refprop


def _require_refprop(message: dict[str, Any]) -> dict[str, Any]:  # Any: JSON
    from stokes.physics.materials.errors import RefpropMissing

    detection = _refprop_detection(message.get("refprop_path"))
    if not detection.get("available"):
        raise RefpropMissing(
            str(detection.get("reason") or "REFPROP is not available"),
            details={"path": detection.get("path")},
        )
    return detection


def _providers(message: dict[str, Any]) -> dict[str, Any]:  # Any: JSON
    found: dict[str, Any] = {}
    try:
        from stokes.physics.materials.providers import coolprop

        version = coolprop.version()
        pure, solutions = coolprop.incomp_lists()
        names = coolprop.module().get_global_param_string("FluidsList").split(",")
        found["coolprop"] = {"available": True, "version": version, "fluids": len(names)}
        found["coolprop_incomp"] = {
            "available": True,
            "version": version,
            "fluids": len(pure) + len(solutions),
        }
        found["refprop"] = _refprop_detection(message.get("refprop_path"))
    except ImportError as exc:
        reason = f"CoolProp is not installed ({exc}); pip install 'stokes-cfd[materials]'"
        for name in ("coolprop", "coolprop_incomp", "refprop"):
            found[name] = {"available": False, "version": "", "reason": reason}
    try:
        from stokes.physics.materials.providers import cantera

        found["cantera"] = {
            "available": True,
            "version": cantera.version(),
            "fluids": len(cantera.BUNDLED),
        }
    except ImportError as exc:
        found["cantera"] = {
            "available": False,
            "version": "",
            "reason": f"Cantera is not installed ({exc}); pip install 'stokes-cfd[materials]'",
        }
    return {"providers": found}


def _index(message: dict[str, Any]) -> dict[str, Any]:  # Any: JSON
    provider = str(message.get("provider"))
    if provider == "cantera":
        from stokes.physics.materials.providers import cantera

        return {"records": list(cantera.index())}
    from stokes.physics.materials.providers import coolprop

    folder = None
    if provider == "refprop":
        folder = _require_refprop(message).get("path")
    return {"records": list(coolprop.index(provider, folder))}


def _spec(message: dict[str, Any]) -> tuple[str, Any]:  # Any: a provider's spec
    provider = str(message.get("provider"))
    fluid = str(message.get("fluid", ""))
    composition = str(message.get("composition") or "")
    basis = str(message.get("basis") or "")
    if provider == "cantera":
        from stokes.physics.materials.providers import cantera

        return provider, cantera.parse(fluid, composition, basis)
    from stokes.physics.materials.providers import coolprop

    if provider == "refprop":
        _require_refprop(message)
    return provider, coolprop.parse(provider, fluid, composition, basis)


def _info(message: dict[str, Any]) -> dict[str, Any]:  # Any: JSON
    provider, spec = _spec(message)
    t_ref = float(message.get("T_ref", 300.0))
    p_ref = float(message.get("p_ref", 101325.0))
    if provider == "cantera":
        from stokes.physics.materials.providers import cantera

        return {"info": cantera.info(spec, t_ref, p_ref)}
    from stokes.physics.materials.providers import coolprop

    phase = str(message.get("phase") or "auto")
    return {"info": coolprop.info(spec, phase, t_ref, p_ref)}


def _evaluate(message: dict[str, Any]) -> dict[str, Any]:  # Any: JSON
    provider, spec = _spec(message)
    raw = message.get("T")
    if not isinstance(raw, list) or not raw or len(raw) > 100_000:
        raise ValueError("evaluate needs a list of temperatures")
    temperatures = [float(t) for t in raw]
    p = float(message.get("p", 101325.0))
    if provider == "cantera":
        from stokes.physics.materials.providers import cantera

        return cantera.evaluate(spec, temperatures, p)
    from stokes.physics.materials.providers import coolprop

    return coolprop.evaluate(spec, str(message.get("phase") or "auto"), temperatures, p)


def _warm(message: dict[str, Any]) -> dict[str, Any]:  # Any: JSON
    loaded: dict[str, str] = {}
    for name in message.get("providers") or ():
        try:
            if name == "cantera":
                from stokes.physics.materials.providers import cantera

                loaded[name] = cantera.version()
            else:
                from stokes.physics.materials.providers import coolprop

                loaded[name] = coolprop.version()
        except ImportError:
            loaded[name] = ""
    return {"loaded": loaded}


def handle(message: Any) -> dict[str, Any]:  # Any: the parsed request, checked here
    """One reply for one request; typed errors keep their code."""
    from stokes.errors import StokesError

    if not isinstance(message, dict) or message.get("type") not in REQUESTS:
        return {"type": "error", "code": "validation", "message": "unknown request"}
    handlers = {
        "warm": _warm,
        "providers": _providers,
        "index": _index,
        "info": _info,
        "evaluate": _evaluate,
    }
    try:
        reply = handlers[str(message["type"])](message)
    except StokesError as exc:
        return {"type": "error", **exc.to_dict()}
    except Exception as exc:  # reported to the parent, which raises a typed error
        last = traceback.extract_tb(exc.__traceback__)[-1:] or None
        where = f" ({last[0].name}:{last[0].lineno})" if last else ""
        return {
            "type": "error",
            "code": "materials.evaluation",
            "message": f"{type(exc).__name__}: {exc}{where}",
        }
    return {"type": "ok", **reply}


def child_main(conn: Connection) -> None:
    """Serve requests until the parent closes the pipe."""
    conn.send_bytes(json.dumps({"type": "ready", "pid": os.getpid()}).encode("utf-8"))
    while True:
        try:
            frame = conn.recv_bytes(MAX_REQUEST_BYTES)
        except (EOFError, OSError):
            return
        try:
            message = json.loads(frame.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            message = None
        reply = _clean(handle(message))
        payload = json.dumps(reply, allow_nan=False).encode("utf-8")
        if len(payload) > MAX_REPLY_BYTES:
            payload = json.dumps(
                {"type": "error", "code": "materials.evaluation", "message": "reply too large"}
            ).encode("utf-8")
        try:
            conn.send_bytes(payload)
        except (EOFError, OSError):
            return
