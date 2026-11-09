"""Wire protocol: 4-byte big-endian length prefix + msgpack map.

Message kinds::

    {"kind": "hello", "token": str, "protocol": 2, "client": str}
    {"kind": "hello", "ok": true, "protocol": 2, "agent": str}
    {"kind": "request", "id": str, "method": str, "params": {...}}
    {"kind": "response", "id": str, "ok": true, "result": ...}
    {"kind": "response", "id": str, "ok": false, "error": {"code", "message", "details"}}
    {"kind": "event", "event": str, "data": {...}}

Every frame is length-checked before it is read (docs/engineering/security.md).

## Version 2 (2026-10-15): `install` is an engine ID, not a path

**The one incompatible change since version 1, and the reason the number moved.** The desktop
sends `install` to name which OpenFOAM a job runs on. It used to carry a **filesystem path**;
it now carries an **engine ID** from `host.probe` — `installs[].id`, or `default_install_id`
for the host's default. The agent's store refuses anything that is not an id it knows
(`jobs.py`, `install must be an engine id (a string) or null`), so a version-1 client sending
a path is not silently misread: it is refused with a message that says what an `install` is.

**The documented `host.probe` record** — the map under `record`, reached through the wrapper
`{available, host, record, why}`::

    {"hostname", "system", "release", "machine", "python", "cpu_count", "memory_bytes",
     "home", "tools": {name: path|null}, "openfoam_candidates": [...], "wsl_distros": [...],
     "vtk": {...}, "why": [...],
     "installs": [{"id": "e_...", "root", "version", "flavour", ...}, ...],
     "default_install_id": "e_..." | null,
     "solver_modules": [...], "utilities": [...], "libraries": [...],
     "etc_functions": [...], "etc_case_dicts": [...], "tutorials_root": str | null}

The last six keys (T-304, ADR-0029) list the default install's `FOAM_LIBBIN` `lib<name>Solver`
modules, `FOAM_APPBIN` applications, library base names, `#includeFunc` names and
`FOAM_ETC/caseDicts` files, and its tutorials folder. Each install carries the same map under
`installs[].inventory`. Nothing is executed to build them.

**`default_install_id` is inside `record`, not beside `available`.** Reading it from the
wrapper's top level returns `None` and every run comes out `skipped` — that mistake was made
once here and is why the shape is written down rather than left to the reader.

A client that only reads `installs` and `default_install_id` works against either version's
*facts*; it is the `install` parameter's MEANING that changed, which is what the number marks.
"""

from __future__ import annotations

import asyncio
import struct
from typing import Any

from stokes.errors import ProtocolError, StokesError

#: **3 since 2026-10-15**: the mutual handshake (T-159a). A remote agent proves it holds the
#: secret and the client proves it holds the same one, so neither side trusts the channel alone.
#:
#: Was **2 since 2026-10-15**, when `install` became an engine id rather than a path: a version-1
#: client's meaning for that parameter is not this one, and a handshake that cannot tell them apart
#: would let a path be sent to a store that expects an id.
PROTOCOL_VERSION = 3
#: The version whose `hello` carries a bare token. **A remote agent refuses it** - see
#: `stokes_agent.handshake` - because accepting it there would let anyone who can reach the
#: port skip mutual authentication entirely.
LEGACY_PROTOCOL_VERSION = 2
#: Bytes in each nonce. 16 is the spec's number and is ample for a single handshake: these are not
#: keys, they exist to stop a recorded exchange being replayed.
NONCE_BYTES = 16
MAX_FRAME = 64 * 1024 * 1024
_HEADER = struct.Struct(">I")
KINDS = ("hello", "request", "response", "event")


def encode(message: dict[str, Any]) -> bytes:
    import msgpack

    body: bytes = msgpack.packb(message, use_bin_type=True)
    if len(body) > MAX_FRAME:
        raise ProtocolError(f"frame of {len(body)} bytes exceeds {MAX_FRAME}")
    return _HEADER.pack(len(body)) + body


def decode(body: bytes) -> dict[str, Any]:
    import msgpack

    try:
        message = msgpack.unpackb(body, raw=False, strict_map_key=True)
    except Exception as exc:
        raise ProtocolError(f"malformed frame: {exc}") from exc
    if not isinstance(message, dict) or message.get("kind") not in KINDS:
        raise ProtocolError("frame is not a protocol message")
    return message


async def read_message(reader: asyncio.StreamReader) -> dict[str, Any] | None:
    """Read one message; ``None`` at a clean end of stream."""
    try:
        header = await reader.readexactly(_HEADER.size)
    except asyncio.IncompleteReadError:
        return None
    (length,) = _HEADER.unpack(header)
    if length > MAX_FRAME:
        raise ProtocolError(f"announced frame of {length} bytes exceeds {MAX_FRAME}")
    try:
        body = await reader.readexactly(length)
    except asyncio.IncompleteReadError as exc:
        raise ProtocolError("connection closed mid-frame") from exc
    return decode(body)


def request(request_id: str, method: str, params: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "request", "id": request_id, "method": method, "params": params}


def response(request_id: str, result: Any) -> dict[str, Any]:  # Any: msgpack-able
    return {"kind": "response", "id": request_id, "ok": True, "result": result}


def error_response(request_id: str, error: StokesError | str) -> dict[str, Any]:
    payload = (
        error.to_dict()
        if isinstance(error, StokesError)
        else {"code": "agent.error", "message": str(error), "details": {}}
    )
    return {"kind": "response", "id": request_id, "ok": False, "error": payload}


def event(name: str, data: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "event", "event": name, "data": data}


def hello(token: str, client: str) -> dict[str, Any]:
    """The **v2** hello: a bare token. Still what the in-process local runner sends."""
    return {"kind": "hello", "token": token, "protocol": LEGACY_PROTOCOL_VERSION, "client": client}


def hello_nonce(nonce: str, client: str) -> dict[str, Any]:
    """The **v3** hello: a nonce and no token, which is what starts the mutual handshake."""
    return {"kind": "hello", "nonce": nonce, "protocol": PROTOCOL_VERSION, "client": client}


def hello_challenge(nonce_a: str, proof: str) -> dict[str, Any]:
    """The agent's answer: its own nonce and its proof that it holds the secret."""
    return {"kind": "hello", "nonce": nonce_a, "proof": proof, "protocol": PROTOCOL_VERSION}


def hello_proof(proof: str, agent: str) -> dict[str, Any]:
    """The client's answer: its proof, and then the agent may accept."""
    return {"kind": "hello", "proof": proof, "protocol": PROTOCOL_VERSION, "agent": agent}


def hello_ok(agent: str) -> dict[str, Any]:
    return {"kind": "hello", "ok": True, "protocol": PROTOCOL_VERSION, "agent": agent}
