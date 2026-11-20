"""A minimal Model Context Protocol server over standard input and output (ADR-0010).

Only what a tool server needs: ``initialize``, ``ping``, ``tools/list`` and ``tools/call``, as
newline-delimited JSON-RPC 2.0. Written here rather than taken from a package so the connector
adds no dependency and installs anywhere Stokes does. Every frame is length-limited; a bad frame
gets an error reply, never a crash.

Import rule: headless, standard library only.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TextIO

PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
MAX_FRAME = 4 * 1024 * 1024

JsonObject = dict[str, Any]  # Any: JSON


@dataclass(frozen=True)
class Tool:
    """One tool: its schema for the client and the function that runs it."""

    name: str
    description: str
    input_schema: JsonObject
    run: Callable[[JsonObject], JsonObject]
    read_only: bool = False


class McpServer:
    def __init__(self, name: str, version: str, instructions: str, tools: list[Tool]) -> None:
        self.name = name
        self.version = version
        self.instructions = instructions
        self.tools = {t.name: t for t in tools}

    def handle(self, message: JsonObject) -> JsonObject | None:
        """The reply to one message, or ``None`` for a notification."""
        method = message.get("method")
        ident = message.get("id")
        if not isinstance(method, str) or ident is None:
            return None  # a reply to something we never sent, or a notification
        try:
            result = self._dispatch(method, message.get("params") or {})
        except KeyError as exc:
            return _error(ident, -32601, f"unknown method or tool: {exc.args[0]}")
        except ValueError as exc:
            return _error(ident, -32602, str(exc))
        return {"jsonrpc": "2.0", "id": ident, "result": result}

    def _dispatch(self, method: str, params: JsonObject) -> JsonObject:
        if method == "initialize":
            asked = params.get("protocolVersion")
            version = asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]
            return {
                "protocolVersion": version,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": self.name, "version": self.version},
                "instructions": self.instructions,
            }
        if method == "ping":
            return {}
        if method == "tools/list":
            return {"tools": [_describe(t) for t in self.tools.values()]}
        if method == "tools/call":
            name = params.get("name")
            if not isinstance(name, str) or name not in self.tools:
                raise KeyError(name)
            arguments = params.get("arguments") or {}
            if not isinstance(arguments, dict):
                raise ValueError("arguments must be an object")
            return self.tools[name].run(arguments)
        raise KeyError(method)

    def serve(self, source: TextIO | None = None, sink: TextIO | None = None) -> None:
        """Read frames until end of input."""
        source = source or sys.stdin
        sink = sink or sys.stdout
        for line in source:
            if not line.strip():
                continue
            reply: JsonObject | None
            if len(line) > MAX_FRAME:
                reply = _error(None, -32600, "frame too large")
            else:
                try:
                    message = json.loads(line)
                except ValueError:
                    reply = _error(None, -32700, "parse error")
                else:
                    reply = self.handle(message) if isinstance(message, dict) else None
            if reply is not None:
                sink.write(json.dumps(reply, separators=(",", ":")) + "\n")
                sink.flush()


def _describe(tool: Tool) -> JsonObject:
    return {
        "name": tool.name,
        "description": tool.description,
        "inputSchema": tool.input_schema,
        "annotations": {"readOnlyHint": tool.read_only},
    }


def _error(ident: Any, code: int, message: str) -> JsonObject:  # Any: JSON-RPC id
    return {"jsonrpc": "2.0", "id": ident, "error": {"code": code, "message": message}}


def text_result(payload: object, *, is_error: bool = False) -> JsonObject:
    """A tool result carrying ``payload`` as JSON text."""
    text = payload if isinstance(payload, str) else json.dumps(payload, indent=1, default=str)
    return {"content": [{"type": "text", "text": text}], "isError": is_error}
