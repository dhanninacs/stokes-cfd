"""``stokes-agent`` entry point.

``stokes-agent --serve`` binds to loopback on an ephemeral port and prints one
JSON line with host, port and token to stdout so a launcher can read it from
the pipe (never from the process list). ``STOKES_AGENT_TOKEN`` sets the token
instead of generating one. ``stokes-agent --probe`` prints the host probe.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

from stokes_agent.probe import probe_host


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="stokes-agent")
    parser.add_argument("--serve", action="store_true", help="run the agent server")
    parser.add_argument("--probe", action="store_true", help="print the host probe and exit")
    parser.add_argument("--port", type=int, default=0, help="port (default: ephemeral)")
    args = parser.parse_args(argv)
    if args.probe or not args.serve:
        sys.stdout.write(json.dumps(probe_host(), default=str) + "\n")
        return
    asyncio.run(_serve(args.port))


async def _serve(port: int) -> None:
    from stokes_agent.server import AgentServer

    server = AgentServer(port=port, token=os.environ.get("STOKES_AGENT_TOKEN") or None)
    host, bound = await server.start()
    sys.stdout.write(json.dumps({"host": host, "port": bound, "token": server.token}) + "\n")
    sys.stdout.flush()
    await server.serve_forever()


if __name__ == "__main__":
    main()
