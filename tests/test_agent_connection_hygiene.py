"""Opus L1: a connection must not leak, and a bad proof must not crash the handler.

**Both are reachable BEFORE authentication**, which is what makes them worth fixing on an agent
meant to run unattended behind an ssh tunnel:

* every connection left an entry in `_readers` for the life of the process, so anyone who can reach
  the port grows a dict without ever completing a handshake;
* a proof containing one non-ASCII character made `hmac.compare_digest` raise `TypeError`, so a peer
  could crash the handler with a malformed message.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from stokes_agent import handshake, protocol
from stokes_agent.handshake import check_proof
from stokes_agent.server import AgentServer

# ------------------------------------------------------------------ the proof, without a socket


def test_a_non_ascii_proof_is_REFUSED_rather_than_raising() -> None:
    """**The crash, in one call.** `hmac.compare_digest` documents that it takes `str` only when
    both arguments are ASCII; given a non-ASCII one it raises `TypeError`. Before the fix:

        check_proof("abc", "caf\\u00e9")  ->  TypeError

    A refusal is the only correct answer to a malformed proof - it is untrusted input, and an
    exception is a crash reachable by anyone who can reach the port.
    """
    assert check_proof("abc", "abc") is True
    for hostile in ("caf\u00e9", "\u00ff" * 8, "abc\u2028", "\U0001f600"):
        assert check_proof("abc", hostile) is False, hostile


def test_the_ascii_cases_still_work() -> None:
    """**The false-refusal half.** The fix must not break the comparison it protects."""
    assert check_proof("deadbeef", "deadbeef") is True
    assert check_proof("deadbeef", "deadbeee") is False
    assert check_proof("deadbeef", "") is False
    # A prefix must not match - the property `compare_digest` exists for.
    assert check_proof("deadbeef", "dead") is False
    assert check_proof("", "deadbeef") is False


# ------------------------------------------------------------------ the leak, over a real socket


def test_a_connection_that_never_authenticates_leaves_nothing_behind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**The leak Opus found.** `_readers` was written for every connection and removed for none,
    so it grew for the life of the process. Measured before the fix: five connections that closed
    without a handshake left `_readers` at **5** while `_writers` correctly fell to **0** - the
    comparison that showed it was a missing line rather than a missing mechanism.
    """
    monkeypatch.setenv("STOKES_AGENT_STATE_DIR", str(tmp_path / "agent"))
    asyncio.run(_open_and_drop(tmp_path))


async def _open_and_drop(tmp_path: Path) -> None:
    server = AgentServer(port=0, token="a-secret", mode="serve")
    host, port = await server.start()
    try:
        for _ in range(8):
            _reader, writer = await asyncio.open_connection(host, port)
            # **Closed WITHOUT a handshake** - which is the reachable-before-auth case.
            writer.close()
            await asyncio.sleep(0.02)
        await asyncio.sleep(0.3)
        assert len(server._readers) == 0, (
            f"{len(server._readers)} readers leaked from connections that never authenticated"
        )
        assert len(server._writers) == 0
    finally:
        await server.stop()


def test_a_non_ascii_proof_over_the_wire_is_a_refusal_not_a_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Through the real protocol, because the unit test above calls `check_proof` directly and
    says nothing about whether the SERVER survives the message."""
    monkeypatch.setenv("STOKES_AGENT_STATE_DIR", str(tmp_path / "agent"))
    asyncio.run(_send_non_ascii(tmp_path))


async def _send_non_ascii(tmp_path: Path) -> None:
    del tmp_path
    server = AgentServer(port=0, token="a-secret", mode="serve")
    host, port = await server.start()
    try:
        reader, writer = await asyncio.open_connection(host, port)
        hello = protocol.hello_nonce(handshake.new_nonce(), "probe")
        writer.write(protocol.encode(hello))
        await writer.drain()
        # Read the agent's own hello - the proof exchange has begun.
        head = await asyncio.wait_for(reader.readexactly(4), timeout=10)
        await asyncio.wait_for(reader.readexactly(int.from_bytes(head, "big")), timeout=10)
        # **A proof that is not ASCII.** Before the fix, this raised inside the handler.
        reply = protocol.hello_proof(handshake.new_nonce(), "caf\u00e9")
        writer.write(protocol.encode(reply))
        await writer.drain()
        try:
            head = await asyncio.wait_for(reader.readexactly(4), timeout=10)
            body = await asyncio.wait_for(
                reader.readexactly(int.from_bytes(head, "big")), timeout=10
            )
            message = protocol.decode(body)
            assert message.get("ok") is not True, "a bad proof was accepted"
        except (asyncio.IncompleteReadError, TimeoutError):
            # Closing the connection is also a refusal, and not a crash.
            pass
        writer.close()
        # **The server is still alive and still serving**, which is what "not a crash" means.
        _again, second = await asyncio.open_connection(host, port)
        second.close()
        await asyncio.sleep(0.1)
        assert server._readers == {}, "the server stopped cleaning up after a bad proof"
    finally:
        await server.stop()


def test_a_NON_ASCII_token_on_the_v2_path_is_REFUSED_not_a_crash() -> None:
    """**Opus residue 7.** `secrets.compare_digest` raises `TypeError` for a `str` holding a
    non-ASCII character - measured: "comparing strings with non-ASCII characters is not supported".

    The v2 handshake compared `str(token)` against the agent's token, so a client sending
    `caf\\u00e9` **made the handler die instead of being refused**: measured, the connection closed
    with no reply at all, and a traceback went to the log as though the agent had a bug.

    A token is bytes on the wire, so bytes are what is compared - and a malformed token must be a
    refused client, not an exception. **Reachable before authentication**, which is what makes it
    worth fixing on an agent meant to run unattended: the v2 path is accepted in local mode, so
    anyone who can reach that port can try it.
    """

    async def main() -> None:
        from stokes_agent.server import AgentServer

        # **`local` mode, because that is where a bare-token hello is accepted at all.**
        server = AgentServer(port=0, token="correct-token", mode="local")
        host, port = await server.start()
        try:
            for label, token in (
                ("the right token", "correct-token"),
                ("a wrong token", "wrong"),
                ("non-ASCII", "caf\u00e9"),
                ("an emoji", "\U0001f600"),
                ("a number", 12345),
                ("null", None),
                ("a list", ["x"]),
            ):
                reader, writer = await asyncio.open_connection(host, port)
                # **The shape `protocol.hello` builds**, since that is what a real v2 client sends.
                hello = {"protocol": 2, "token": token, "kind": "hello", "client": "probe"}
                writer.write(protocol.encode(hello))
                await writer.drain()
                reply = await asyncio.wait_for(protocol.read_message(reader), timeout=10)
                writer.close()
                # **A reply, always** - which is what distinguishes a refusal from a crash. The
                # non-ASCII case used to close the connection with nothing.
                assert reply is not None, f"{label}: the handler died instead of refusing"
                expected = label == "the right token"
                assert bool(reply.get("ok")) is expected, (
                    f"{label}: ok={reply.get('ok')!r}, expected {expected}"
                )
            # **And the server is still serving**, so one bad client did not take the listener down.
            _again, writer = await asyncio.open_connection(host, port)
            writer.close()
            # **Given a moment to reap**, because a connection closed a microsecond ago is still in
            # `_readers` until its handler reaches the `finally`. Asserting immediately made this
            # fail on correct code, which is a race in the test rather than a leak in the server -
            # the same distinction the leaked-reader test above exists to make.
            for _ in range(50):
                if server._readers == {}:
                    break
                await asyncio.sleep(0.02)
            assert server._readers == {}, "the server stopped cleaning up after a bad token"
        finally:
            await server.stop()

    asyncio.run(main())
