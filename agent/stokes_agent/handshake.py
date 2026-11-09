"""The mutual handshake, and the mode rule that makes it unbypassable (T-159a).

**Two things are proved, and neither side learns anything when they are not.**

    client -> agent   nonce_c                    (16 random bytes, no token)
    agent  -> client  nonce_a, HMAC(secret, "agent"  || nonce_c || nonce_a)
    client -> agent   HMAC(secret, "client" || nonce_a || nonce_c)

The client checks the agent's proof before sending its own, so a host that does not hold the
secret never receives anything derived from it. Both sides compare with `hmac.compare_digest`,
and **either failure closes the connection with one message** - not "bad nonce", not "bad hmac"
- because a distinguishable refusal tells an attacker which half of the exchange to work on.

## Why the mode is fixed at agent start

An agent that accepted a bare-token v2 hello whenever one arrived would be **downgradable**:
anyone who can reach the port (a local user on the host, anything that can open the tunnel)
could skip the mutual handshake by asking for the older version. So the mode is a **property of
how the agent was started**, never a choice the client makes:

* `--serve --state-dir` (the detached remote agent) accepts **v3 only**. A v2 `hello` is
  refused with `protocol 3 expected`.
* an in-process local agent accepts **v2 as today**, and may also accept v3.

That also keeps the local runner exactly as it was, which the spec requires and the existing tests
pin.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from enum import Enum
from typing import Any

from stokes_agent.protocol import LEGACY_PROTOCOL_VERSION, NONCE_BYTES, PROTOCOL_VERSION

#: One message for every refusal. **The same string for all of them, deliberately**: a caller that
#: learns WHICH check failed learns what to fix next.
REFUSED = "handshake rejected"
#: What a serve-mode agent says to a bare-token hello. The same for a bad token as for a good
#: one, because a serve-mode agent has no token to be right about.
EXPECTED_V3 = "protocol 3 expected"


class Mode(Enum):
    """How an agent was started, which decides what it will accept. **Never chosen by a client.**"""

    #: The in-process agent of the local runner: the v2 token hello, and today that is all it gets.
    LOCAL = "local"
    #: The detached agent behind an SSH tunnel: v3 mutual handshake only.
    SERVE = "serve"


def new_nonce() -> str:
    """A fresh nonce, hex. Hex so it survives being written into a test or a log by hand."""
    return secrets.token_hex(NONCE_BYTES)


def agent_proof(secret: str, nonce_c: str, nonce_a: str) -> str:
    """`HMAC(secret, "agent" || nonce_c || nonce_a)`.

    **The label is the first field**, so a proof for one role can never be replayed as the
    other's.
    """
    return _proof(secret, "agent", nonce_c, nonce_a)


def client_proof(secret: str, nonce_c: str, nonce_a: str) -> str:
    """`HMAC(secret, "client" || nonce_a || nonce_c)`. **The nonces are in the other order** as well
    as the label differing, so the two proofs are not each other's input under any reflection."""
    return _proof(secret, "client", nonce_a, nonce_c)


def _proof(secret: str, label: str, first: str, second: str) -> str:
    material = f"{label}{first}{second}".encode()
    return hmac.new(secret.encode(), material, hashlib.sha256).hexdigest()


def check_proof(expected: str, received: Any) -> bool:
    """Constant-time comparison.

    **`compare_digest`, never `==`**: a byte-by-byte comparison leaks how much of a guess was
    right, which is enough to recover an HMAC one character at a time.
    """
    if not isinstance(received, str) or not expected:
        return False
    # **COMPARED AS BYTES, because `compare_digest` raises on non-ASCII** (Opus L1). Measured:
    # `hmac.compare_digest('abc', 'caf\u00e9')` raises `TypeError: comparing strings with non-ASCII
    # characters is not supported`. A peer sends one non-ASCII byte in its proof and the handler
    # raises **before authentication** - reachable by anyone who can reach the port.
    #
    # **Encode as ASCII and refuse what will not encode**, rather than `errors="replace"`: a proof
    # that is not ASCII is not a proof we issued, and `expected` is our own hex string. So a refusal
    # is both correct and unambiguous, and no replacement character can be made to collide with it.
    try:
        return hmac.compare_digest(expected.encode("ascii"), received.encode("ascii"))
    except (UnicodeEncodeError, UnicodeDecodeError):
        return False


def accepts(mode: Mode, hello: dict[str, Any]) -> bool:
    """Whether this agent mode will even begin a handshake with this hello.

    **The downgrade rule, in one place.** A serve-mode agent refuses anything that is not a v3
    nonce hello - including a v2 hello carrying the right token, which it does not have one of.
    """
    if mode is Mode.SERVE:
        return (
            hello.get("protocol") == PROTOCOL_VERSION
            and isinstance(hello.get("nonce"), str)
            and "token" not in hello
        )
    # LOCAL: the v2 token hello as today. A v3 hello is also allowed, so the same client code can
    # talk to either - the mode decides what is REQUIRED, not what is possible.
    if hello.get("protocol") == LEGACY_PROTOCOL_VERSION and isinstance(hello.get("token"), str):
        return True
    return hello.get("protocol") == PROTOCOL_VERSION and isinstance(hello.get("nonce"), str)


def refusal_for(mode: Mode, hello: dict[str, Any]) -> str:
    """The message a refused hello gets. **A serve-mode agent says what it expects**, because the
    caller there is a version of ourselves rather than an attacker we are trying to slow down."""
    if mode is Mode.SERVE and hello.get("protocol") == LEGACY_PROTOCOL_VERSION:
        return EXPECTED_V3
    return REFUSED
