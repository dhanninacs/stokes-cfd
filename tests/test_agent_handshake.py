"""The mutual handshake and the mode rule that makes it unbypassable (T-159a).

**The rule claude-win2 added to my design is the important one**: an agent started in serve
mode accepts **only** v3, and the mode is fixed at start rather than chosen by the client.
Without it, the compatibility path is a downgrade path - anyone who can reach the port could
ask for the older handshake and skip mutual authentication entirely.

The other half is that neither side learns *which* check failed. A peer that is told "bad nonce"
rather than "bad hmac" knows which half of the exchange to work on next.
"""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Any

import pytest

from stokes_agent import handshake, protocol


@pytest.mark.parametrize(
    "hello",
    [
        {"protocol": 2, "token": "the-token"},  # a v2 token hello
        {"protocol": 3, "token": "the-token"},  # a nonce hello with a token instead
        {"protocol": 3},  # no nonce at all
        {"protocol": 1, "nonce": "ab" * 16},
        {"protocol": 3, "nonce": 12345},  # a nonce that is not a string
        {"protocol": 3, "nonce": "ab" * 16, "token": "x"},  # BOTH, which is neither
    ],
)
def test_a_serve_agent_refuses_everything_that_is_not_v3(hello: dict[str, Any]) -> None:
    """**The anti-downgrade rule.** A serve-mode agent has no token to be right about, so a bare
    token hello is refused however good the token is."""
    assert handshake.accepts(handshake.Mode.SERVE, hello) is False


def test_a_local_agent_keeps_the_legacy_hello_and_may_also_speak_v3() -> None:
    """**The local runner is unchanged** - that is what the existing tests pin, and breaking it was
    the risk the spec's own contradiction created."""
    assert handshake.accepts(handshake.Mode.LOCAL, {"protocol": 2, "token": "t"}) is True
    assert handshake.accepts(handshake.Mode.LOCAL, {"protocol": 3, "nonce": "ab" * 16}) is True
    # And a local agent still refuses nonsense.
    assert handshake.accepts(handshake.Mode.LOCAL, {"protocol": 2}) is False
    assert handshake.accepts(handshake.Mode.LOCAL, {"protocol": 9, "token": "t"}) is False


def test_a_serve_agent_says_what_it_expects_but_only_about_the_version() -> None:
    """A v2 caller is a version of ourselves, so it is told what changed. Anything else gets
    the one indistinguishable refusal."""
    legacy = {"protocol": 2, "token": "t"}
    assert handshake.refusal_for(handshake.Mode.SERVE, legacy) == handshake.EXPECTED_V3
    # A wrong token in a v3-shaped hello gets nothing useful: it is not a version problem.
    assert handshake.refusal_for(handshake.Mode.SERVE, {"protocol": 3, "nonce": "ab" * 16}) == (
        handshake.REFUSED
    )
    assert handshake.refusal_for(handshake.Mode.LOCAL, {"protocol": 2}) == handshake.REFUSED


def test_the_two_proofs_are_not_interchangeable() -> None:
    """**The role label and the nonce order both differ**, so an attacker who can make a
    client prove itself cannot replay that proof back as the agent's - a reflection attack."""
    secret, nonce_c, nonce_a = "s3cret", "c" * 32, "a" * 32
    agent_side = handshake.agent_proof(secret, nonce_c, nonce_a)
    client_side = handshake.client_proof(secret, nonce_c, nonce_a)
    assert agent_side != client_side
    assert not handshake.check_proof(agent_side, client_side)
    # Swapping the nonces does not produce the other proof either.
    assert handshake.agent_proof(secret, nonce_a, nonce_c) != client_side
    assert handshake.client_proof(secret, nonce_a, nonce_c) != agent_side


def test_a_proof_needs_the_secret() -> None:
    assert (
        handshake.check_proof(
            handshake.agent_proof("right", "c" * 32, "a" * 32),
            handshake.agent_proof("wrong", "c" * 32, "a" * 32),
        )
        is False
    )
    # A missing or non-string proof is refused rather than raising: it arrives from the network.
    for bad in (None, "", 1234, b"bytes", []):
        assert handshake.check_proof("x", bad) is False
    # An empty expectation never matches, so an empty secret cannot authenticate anything.
    assert handshake.check_proof("", "") is False


def test_a_nonce_is_fresh_and_hex() -> None:
    """Replay is what the nonces are for, so two in a row must differ."""
    first, second = handshake.new_nonce(), handshake.new_nonce()
    assert first != second
    assert len(first) == protocol.NONCE_BYTES * 2
    assert all(character in "0123456789abcdef" for character in first)


def test_the_versions_mean_what_the_docstring_says() -> None:
    """3 is the newest this build speaks; 2 is the legacy hello's own version."""
    assert protocol.PROTOCOL_VERSION == 3
    assert protocol.LEGACY_PROTOCOL_VERSION == 2
    assert protocol.hello("t", "c")["protocol"] == protocol.LEGACY_PROTOCOL_VERSION
    assert protocol.hello_nonce("ab" * 16, "c")["protocol"] == protocol.PROTOCOL_VERSION


def test_a_serve_agent_is_constructed_with_its_mode(tmp_path: Path) -> None:
    """The mode is a property of how the agent was STARTED, so it is set at construction and a
    client cannot talk it out of it."""
    from stokes_agent.jobs import JobStore
    from stokes_agent.server import AgentServer

    serve = AgentServer(port=0, token="t", jobs=JobStore(tmp_path / "s"), mode="serve")
    assert serve.mode is handshake.Mode.SERVE
    local = AgentServer(port=0, token="t", jobs=JobStore(tmp_path / "l"))
    assert local.mode is handshake.Mode.LOCAL, "the default must stay local, as today"
    with pytest.raises(ValueError):
        AgentServer(port=0, token="t", jobs=JobStore(tmp_path / "x"), mode="nonsense")


def test_the_REAL_serve_entry_point_refuses_a_v2_hello(tmp_path: Path) -> None:
    """**Opus H1, and the reason this test exists rather than the one above.**

    The test above constructs `AgentServer(..., mode="serve")` itself, so it proves the *rule* works
    and says nothing about whether production uses it. **It did not**: `_serve_detached` built the
    agent with no `mode`, so the default `local` applied, and a detached remote agent - the one
    reached over a network - accepted a **v2 bare-token hello**, letting any process that
    port skip the mutual handshake (measured: `mode=<Mode.LOCAL>`, reply `ok: true`).

    So this starts the agent through **its own CLI entry point** and speaks to it over a socket:

    * **v2 bare token** must be refused with "protocol 3 expected";
    * **v3 nonce** must be accepted, or the fix would have broken the handshake it protects.

    A test that builds the object under test cannot see this class of defect at all - the same
    lesson as the mesh fakes that ignored the host name.
    """
    import json
    import os
    import subprocess
    import sys
    import time

    from stokes_agent import daemon

    state = tmp_path / "agent"
    env = {
        **os.environ,
        "PYTHONPATH": str(Path(__file__).resolve().parent.parent / "agent"),
        "STOKES_AGENT_HOME": str(state),
    }
    # **`Popen`, not `run`**: `--state-dir` makes the agent detach and keep serving, so a
    # blocking call would wait out its own timeout and report a start failure that never
    # happened.
    proc = subprocess.Popen(
        [sys.executable, "-m", "stokes_agent", "--serve", "--state-dir", str(state)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        record = None
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline and record is None:
            try:
                record = json.loads((state / daemon.SESSION_FILE).read_text())
            except (OSError, ValueError):
                time.sleep(0.1)
        assert record is not None, "the agent never wrote its session record"
        secret = daemon.read_secret(state)
        assert secret, "the agent wrote no secret"

        refused = greet(record["port"], protocol.hello(secret, "probe"))
        assert refused.get("ok") is False, "a SERVED agent accepted the v2 downgrade"
        assert "protocol 3" in str(refused.get("error", {}).get("message", "")).lower()

        accepted = greet(record["port"], protocol.hello_nonce(handshake.new_nonce(), "probe"))
        assert accepted.get("kind") == "hello", "the v3 handshake did not start"
        assert accepted.get("proof"), "the agent sent no proof"
    finally:
        with contextlib.suppress(OSError, ValueError):
            os.kill(int(json.loads((state / daemon.SESSION_FILE).read_text())["pid"]), 15)
        with contextlib.suppress(Exception):
            proc.wait(timeout=10)
        if proc.poll() is None:  # pragma: no cover - only if the agent ignored SIGTERM
            proc.kill()


def greet(port: int, message: dict[str, Any]) -> dict[str, Any]:
    """One wire round trip: send a hello, read the reply. **Blocking and short**, for a test."""
    import socket

    with socket.create_connection(("127.0.0.1", port), timeout=20) as sock:
        sock.sendall(protocol.encode(message))
        head = sock.recv(4)
        assert len(head) == 4, "the agent closed the connection instead of answering"
        body = b""
        wanted = int.from_bytes(head, "big")
        while len(body) < wanted:
            chunk = sock.recv(wanted - len(body))
            if not chunk:
                break
            body += chunk
    return protocol.decode(body)
