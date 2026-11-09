"""Wire protocol: 4-byte big-endian length prefix + msgpack map.

Message kinds::

    {"kind": "hello", "token": str, "protocol": 1, "client": str}
    {"kind": "hello", "ok": true, "protocol": 1, "agent": str}
    {"kind": "request", "id": str, "method": str, "params": {...}}
    {"kind": "response", "id": str, "ok": true, "result": ...}
    {"kind": "response", "id": str, "ok": false, "error": {"code", "message", "details"}}
    {"kind": "event", "event": str, "data": {...}}

Every frame is length-checked before it is read (docs/engineering/security.md).
"""

from __future__ import annotations

import asyncio
import struct
from typing import Any

from stokes.errors import ProtocolError, StokesError

PROTOCOL_VERSION = 1
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
    return {"kind": "hello", "token": token, "protocol": PROTOCOL_VERSION, "client": client}


def hello_ok(agent: str) -> dict[str, Any]:
    return {"kind": "hello", "ok": True, "protocol": PROTOCOL_VERSION, "agent": agent}
