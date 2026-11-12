"""The MCP server: protocol framing, the four tools, the feature gate."""

from __future__ import annotations

import io
import json
from pathlib import Path

from stokes.api import Session
from stokes.license import Features
from stokes.mcp.server import build_server


def _call(server, name, arguments=None, ident=1):  # type: ignore[no-untyped-def]
    reply = server.handle(
        {
            "jsonrpc": "2.0",
            "id": ident,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {}},
        }
    )
    return reply["result"], json.loads(reply["result"]["content"][0]["text"])


def _licensed() -> Session:
    return Session(features=Features({"assistant"}))


def test_initialize_carries_instructions_and_lists_four_tools() -> None:
    server = build_server(_licensed())
    init = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-06-18"},
        }
    )
    assert init["result"]["protocolVersion"] == "2025-06-18"
    assert "CFD" in init["result"]["instructions"]
    tools = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["result"]["tools"]
    assert [t["name"] for t in tools] == [
        "stokes_search",
        "stokes_describe",
        "stokes_run",
        "stokes_events",
    ]
    assert server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_run_a_command_and_read_its_events(tmp_path: Path) -> None:
    server = build_server(_licensed())
    _, found = _call(server, "stokes_search", {"query": "project new"})
    assert any(m["command"] == "project.new" for m in found["matches"])
    _, schema = _call(server, "stokes_describe", {"command": "project.new"})
    assert schema["name"] == "project.new"
    result, _ = _call(
        server,
        "stokes_run",
        {"command": "project.new", "params": {"path": str(tmp_path / "p.stokes"), "name": "p"}},
    )
    assert result["isError"] is False
    _, events = _call(server, "stokes_events")
    assert events["events"]


def test_errors_are_results_not_crashes() -> None:
    server = build_server(_licensed())
    result, body = _call(server, "stokes_run", {"command": "parameter.set", "params": {}})
    assert result["isError"] is True and "code" in body["error"]
    assert server.handle({"jsonrpc": "2.0", "id": 3, "method": "nope"})["error"]["code"] == -32601


def test_free_tier_is_told_what_is_missing() -> None:
    server = build_server(Session(features=Features.free()))
    result, body = _call(server, "stokes_search", {"query": "mesh"})
    assert result["isError"] is True
    assert body["error"]["code"] == "licence_feature_missing"


def test_serve_survives_garbage_frames() -> None:
    server = build_server(_licensed())
    out = io.StringIO()
    server.serve(io.StringIO('not json\n[1]\n{"jsonrpc":"2.0","id":5,"method":"ping"}\n'), out)
    lines = [json.loads(x) for x in out.getvalue().splitlines()]
    assert lines[0]["error"]["code"] == -32700 and lines[1]["result"] == {}
