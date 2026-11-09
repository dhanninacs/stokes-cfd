from __future__ import annotations

import asyncio
from typing import Any

import pytest

from stokes.errors import ProtocolError
from stokes_agent import protocol
from stokes_agent.client import AgentClient, AgentError
from stokes_agent.server import AgentServer


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def test_ping_probe_and_errors() -> None:
    async def scenario() -> None:
        server = AgentServer()
        host, port = await server.start()
        client = await AgentClient.connect(host, port, server.token)
        assert client.agent.startswith("stokes-agent/")
        assert await client.call("ping") == {"pong": True}
        probe = await client.call("probe")
        assert "cpu_count" in probe and "tools" in probe
        with pytest.raises(AgentError):
            await client.call("nope")
        with pytest.raises(AgentError):
            await client.call("ping", extra=1)
        await client.close()
        await server.stop()

    run(scenario())


def test_wrong_token_is_rejected() -> None:
    async def scenario() -> None:
        server = AgentServer()
        host, port = await server.start()
        with pytest.raises(ProtocolError):
            await AgentClient.connect(host, port, "wrong")
        await server.stop()

    run(scenario())


def test_concurrent_requests_and_events() -> None:
    async def scenario() -> None:
        server = AgentServer()

        async def slow() -> dict[str, bool]:
            await asyncio.sleep(0.3)
            return {"slow": True}

        server.register("slow", slow)
        host, port = await server.start()
        client = await AgentClient.connect(host, port, server.token)
        order: list[str] = []

        async def call(name: str) -> None:
            await client.call(name)
            order.append(name)

        await asyncio.gather(call("slow"), call("ping"))
        assert order == ["ping", "slow"]
        await server.broadcast("job.state", {"state": "solving"})
        event = await asyncio.wait_for(client.events.get(), timeout=5)
        assert event["event"] == "job.state" and event["data"]["state"] == "solving"
        await client.close()
        await server.stop()

    run(scenario())


def test_agent_shutdown_stops_serving_after_the_reply() -> None:
    """The owner's close over the wire, used by a WSL child agent (ADR-0024 §3.4).

    **The reply must arrive before the server stops.** A shutdown that closed the socket first
    would look like a dropped connection to the runner, which would then terminate the process and
    lose the knowledge that the jobs were stopped. `serve_forever` finishing is the observable
    proof that the stop event fired.
    """

    async def scenario() -> None:
        server = AgentServer()
        host, port = await server.start()
        client = await AgentClient.connect(host, port, server.token)
        serving = asyncio.create_task(server.serve_forever())
        try:
            assert await client.call("agent.shutdown", timeout=10) == {"stopping": True}
            # **The client must go before the server can finish stopping.** `stop` waits for the
            # listener to close and for its writers; a client that stays connected would make the
            # graceful path wait until the runner's terminate fallback. The runner closes it, and
            # this test does the same thing in the same order.
            await client.close()
            await asyncio.wait_for(serving, timeout=10)
        finally:
            await client.close()

    run(scenario())


def test_frame_limits_and_malformed_frames() -> None:
    with pytest.raises(ProtocolError):
        protocol.encode(
            {"kind": "event", "event": "x", "data": {"blob": b"\0" * (protocol.MAX_FRAME + 1)}}
        )
    with pytest.raises(ProtocolError):
        protocol.decode(b"\xc1")
    with pytest.raises(ProtocolError):
        protocol.decode(protocol.encode({"kind": "bogus"})[4:])


def test_loopback_only() -> None:
    with pytest.raises(ProtocolError):
        AgentServer(host="0.0.0.0")


def test_the_protocol_version_is_three_and_the_legacy_hello_still_says_two() -> None:
    """**PROTOCOL_VERSION 3 (T-159a): the mutual handshake.**

    **The history, because a version is contract and this assertion exists to make each move
    deliberate rather than incidental:**

    * **2** (2026-10-15, FIXES-C item 7): `install` became an engine id, not a path. A version-1
      client sent a filesystem path that this agent's store would refuse; the version is what makes
      the mismatch *say so* instead of looking like a bad parameter.
    * **3** (2026-10-15, T-159a): a remote agent proves it holds the shared secret and the client
      proves it holds the same one, so neither side trusts the encrypted channel alone.

    **The two numbers now differ on purpose.** `hello` is the legacy bare-token form and must keep
    announcing **2**, because that is what it is - an agent that reads it as a v3 hello would look
    for a nonce that is not there.
    """
    assert protocol.PROTOCOL_VERSION == 3, (
        "the protocol version changed; the module docstring's version history must say why and "
        "this assertion must be updated deliberately, not to make a test pass"
    )
    assert protocol.LEGACY_PROTOCOL_VERSION == 2
    # The legacy hello announces the version it really is, and the v3 ones announce 3.
    assert protocol.hello("a-token", "a-client")["protocol"] == 2
    assert protocol.hello_nonce("ab" * 16, "a-client")["protocol"] == 3
    assert protocol.hello_ok("an-agent")["protocol"] == 3
    assert protocol.hello_challenge("cd" * 16, "proof")["protocol"] == 3


def test_the_documented_host_probe_record_has_the_keys_the_docstring_names() -> None:
    """**The docstring is the agent↔desktop contract, so it is checked against the real record.**

    `default_install_id` and `installs` are what a client needs to name an engine, and the docstring
    records that **`default_install_id` is inside `record`, not beside `available`** — reading it
    from the wrapper returns `None` and every run comes out `skipped`, a mistake made once here.

    This asserts the two facts a client depends on, using the shapes the agent produces rather than
    a live probe (which needs OpenFOAM and would make this a platform test).
    """
    from stokes_agent import probe

    record = probe.probe_host()
    documented = {
        "hostname",
        "system",
        "release",
        "machine",
        "python",
        "cpu_count",
        "memory_bytes",
        "home",
        "tools",
        "openfoam_candidates",
        "wsl_distros",
    }
    missing = documented - set(record)
    assert not missing, (
        f"host.probe no longer returns what the protocol docstring documents: {missing}"
    )
    # And the docstring names the engine fields, which the AGENT's probe does not itself build: the
    # registry wraps this record and adds them. Recorded here so the two halves stay findable.
    text = protocol.__doc__ or ""
    for name in ("default_install_id", "installs", "record"):
        assert name in text, f"the protocol docstring no longer documents {name!r}"
