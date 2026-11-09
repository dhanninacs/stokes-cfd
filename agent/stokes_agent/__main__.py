"""``stokes-agent`` entry point.

``stokes-agent --serve`` binds to loopback on an ephemeral port and prints one
JSON line with host, port and token to stdout so a launcher can read it from
the pipe (never from the process list). ``STOKES_AGENT_TOKEN`` sets the token
instead of generating one. ``stokes-agent --probe`` prints the host probe.

**``--state-dir`` makes it a DETACHED agent** (T-159a): it generates its own secret into that
directory rather than printing one, `setsid`s away from the ssh session, records itself in
`session.json`, and exits after twelve idle hours. A client starts it over ssh and then reads the
files - which is why no secret is ever an argument here. See `stokes_agent.daemon`.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time

from stokes_agent.probe import probe_host
from stokes_agent.protocol import PROTOCOL_VERSION


def _idle_exit_default() -> int:
    """The agent's own twelve hours. **Read from `daemon` rather than repeated**, so a client and an
    agent cannot disagree about what "idle" means - and imported lazily so `--probe` stays cheap.
    """
    from stokes_agent.daemon import IDLE_EXIT_S

    return IDLE_EXIT_S


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="stokes-agent")
    parser.add_argument("--serve", action="store_true", help="run the agent server")
    parser.add_argument("--probe", action="store_true", help="print the host probe and exit")
    # **`--host` is the record the mesh slice needs** (T-140): the base probe plus every OpenFOAM
    # install
    # this machine can drive. `--probe` stays as it is, because `tests/test_agent.py` relies on its
    # shape.
    parser.add_argument(
        "--host",
        action="store_true",
        help="print the host record (the probe plus the OpenFOAM installs) and exit",
    )
    parser.add_argument("--port", type=int, default=0, help="port (default: ephemeral)")
    # **`--state-dir` is what makes this a remote agent**: it writes its own secret and a session
    # record, and detaches. The token it uses is generated from that secret rather than given.
    parser.add_argument(
        "--state-dir",
        default=None,
        help="detach and keep state here (a remote agent; no token is printed)",
    )
    # **The idle exit, on the command line so the DETACHED agent actually gets it** (Opus recheck
    # round 3, finding 3). `should_exit_idle` and the serve-loop wiring both existed, and
    # `_serve_detached` built its server without a value - so the wiring never ran in production.
    # Configurable because the alternative is a test that waits twelve hours.
    parser.add_argument(
        "--idle-exit",
        type=int,
        default=None,
        help="exit after this many idle seconds (default: twelve hours)",
    )
    args = parser.parse_args(argv)
    if args.host:
        from stokes_agent.engine_env import host_record

        sys.stdout.write(json.dumps(host_record(), default=str) + "\n")
        return
    if args.probe or not args.serve:
        sys.stdout.write(json.dumps(probe_host(), default=str) + "\n")
        return
    if args.state_dir is not None:
        asyncio.run(_serve_detached(args.port, args.state_dir, args.idle_exit))
        return
    asyncio.run(_serve(args.port))


async def _serve(port: int) -> None:
    from stokes_agent.server import AgentServer

    server = AgentServer(port=port, token=os.environ.get("STOKES_AGENT_TOKEN") or None)
    host, bound = await server.start()
    sys.stdout.write(json.dumps({"host": host, "port": bound, "token": server.token}) + "\n")
    sys.stdout.flush()
    await server.serve_forever()


async def _serve_detached(port: int, state_dir_argument: str, idle_exit: int | None = None) -> None:
    """A detached agent: own secret, session record, idle exit (T-159a).

    **No token is printed.** The local `_serve` prints one because its launcher reads the pipe; this
    one is started by `ssh`, whose stdout a client would have to hold open for as long as the agent
    lives. The secret goes to a 0600 file instead, and a client reads it over ssh when it attaches -
    which is also why it cannot appear in `ps`.
    """
    from stokes_agent import daemon
    from stokes_agent.server import AgentServer

    folder = daemon.state_dir(state_dir_argument)
    secret = daemon.read_secret(folder) or daemon.new_secret()
    daemon.write_secret(folder, secret)
    # **`mode="serve"`, and this is a SECURITY fix, not a tidiness one** (Opus H1, reproduced).
    #
    # The default is `local`, which accepts a **v2 bare-token hello** - so a process reaching
    # port could skip the mutual handshake entirely by asking for the older protocol, and the
    # downgrade rule would never run in production. Measured on this tree before the fix: the agent
    # built here reported `mode=<Mode.LOCAL>` and answered a v2 hello with `ok: true`.
    #
    # A detached remote agent is reached over a network, so it is a **serve** agent by
    # definition: the mode is how the agent was started, never something a client can ask for.
    # **`idle_exit_s` is passed, and that is the whole of finding 3.** Without it the serve loop
    # never created the idle task, so a detached agent ran for ever however the code above was
    # written. `None` keeps the agent's own default, which is the twelve hours in `daemon`.
    server = AgentServer(
        port=port,
        token=secret,
        mode="serve",
        idle_exit_s=idle_exit if idle_exit is not None else _idle_exit_default(),
    )
    host, bound = await server.start()
    daemon.detach()
    daemon.write_session(
        folder,
        daemon.Session(
            pid=os.getpid(),
            port=bound,
            started=time.time(),
            protocol=PROTOCOL_VERSION,
        ),
    )
    # **One line, and it carries no secret**: whoever is reading this pipe learns where to look, not
    # how to authenticate.
    sys.stdout.write(json.dumps({"host": host, "port": bound, "state_dir": str(folder)}) + "\n")
    sys.stdout.flush()
    await server.serve_forever()


if __name__ == "__main__":
    main()
