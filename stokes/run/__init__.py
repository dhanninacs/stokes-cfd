"""Runners, the host registry and the agent client (ADR-0005, T-144c).

Import rule: headless; talks to `stokes_agent` over the protocol. Imported **inside** command
handlers, never at module level, because `import stokes.api` has a 300 ms budget and most sessions
never mesh.

What is here now: `LocalRunner` (an in-process agent on its own loop) and `HostRegistry`,
the hosts a
session can use. The SSH runner (T-159a) joins the same `Runner` protocol; the WSL runner is not
planned (owner decision 2026-10-13).

**ADR-0023's run records are not here yet.** They arrive with T-214, which is where a run
acquires an
id, a state and a place in the project tree. This package answers only which machines can mesh, and
what they have.
"""
