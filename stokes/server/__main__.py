"""``python -m stokes.server``: the headless server (free tier).

Prints the URL with its token, serves the built front end from ``web/dist``
when present, and runs until interrupted.
"""

from __future__ import annotations

import argparse
import signal
import sys
import time
import webbrowser

import stokes.app.demo  # noqa: F401  (registers the Flanged pipe sample's builder)
from stokes.api import Session
from stokes.log import get_logger
from stokes.server.app import create_app, new_token
from stokes.server.runner import ServerThread

log = get_logger(__name__)


def install_signal_handlers() -> None:
    """Turn `SIGTERM` and `SIGHUP` into `KeyboardInterrupt`, so the shutdown path actually runs.

    **`atexit` does not run on `SIGTERM`.** Python's default action for it ends the process at once, so
    the `except KeyboardInterrupt: server.stop()` below never executes — and because OpenFOAM utilities
    are started with `start_new_session=True` they are not in this process's group, so a solver outlives
    the server as an orphan holding its case directory. That is the shape of a `pkill`, a
    `systemctl stop`, or a closed `nohup` session's kill: exactly how this server is run.

    Raising `KeyboardInterrupt` in the main thread reuses the path that already works for Ctrl-C, which is
    why this is two lines rather than a signal-driven shutdown of its own.

    A no-op off the main thread, because `signal.signal` refuses there — and a caller that cannot install
    handlers has not lost anything it had.
    """
    for name in ("SIGTERM", "SIGHUP"):
        number = getattr(signal, name, None)
        if number is None:
            continue
        try:
            signal.signal(number, _raise_keyboard_interrupt)
        except ValueError:
            # Not the main thread, or an unsupported platform: nothing to do.
            return


def _raise_keyboard_interrupt(signum: int, frame: object) -> None:
    """The handler itself. Kept separate so a test can call it directly."""
    del frame
    raise KeyboardInterrupt(f"signal {signum}")


def _warm_local_host(session: Session) -> None:
    """Probe the local host on a thread as soon as the server listens, so the first client does
    not pay for it.

    **Measured 2026-11-01 (laptop, native FoamWin):** the first client of a `--project` server sat
    on the Start screen for 30-40 s while the loop thread was idle in every py-spy dump taken
    between requests; the first `host.list`/`host.probe` runs the FoamWin handshake (`of14-env.ps1`
    through PowerShell) and that runs INSIDE the dispatcher, on the loop, so the tree read and
    `project.info` waited behind it. The probe is cached per session, so paying it here, before
    anyone connects, is the whole fix; a probe that fails is the same failure the first request
    would have reported, logged and nothing more.
    """
    import threading

    def work() -> None:
        try:
            from stokes.run.hosts import HostRegistry

            HostRegistry.of(session).probe("local")
        except Exception:
            log.debug("the local host could not be probed at start-up", exc_info=True)

    threading.Thread(target=work, name="stokes-host-warmup", daemon=True).start()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="stokes-server")
    parser.add_argument(
        "--project", help="open this .stokes project (default: no project, the start screen)"
    )
    parser.add_argument("--port", type=int, default=0, help="port (default: chosen by the OS)")
    parser.add_argument("--open", action="store_true", help="open the system browser")
    parser.add_argument(
        "--compress-loopback",
        action="store_true",
        help=(
            "gzip replies to loopback clients too; by default only remote clients get them. "
            "Use it for a browser behind an SSH tunnel, which arrives from 127.0.0.1"
        ),
    )
    args = parser.parse_args(argv)

    # Without --project no project is opened; the client shows the start screen.
    session = Session()
    if args.project:
        session.execute("project.open", path=args.project)
    token = new_token()
    app = create_app(session, token=token, compress_loopback=args.compress_loopback)
    server = ServerThread(app, port=args.port)
    url = server.start()
    full = f"{url}/?token={token}"
    sys.stdout.write(f"{full}\n")
    sys.stdout.flush()
    _warm_local_host(session)
    # **Before the loop, so a signal arriving during start-up still runs the shutdown.**
    install_signal_handlers()
    if args.open:
        webbrowser.open(full)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        server.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
