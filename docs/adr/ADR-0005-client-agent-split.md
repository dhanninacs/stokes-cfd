# ADR-0005 · Desktop and agent split

**Status:** accepted · 2026-10-02 (decisions B1, B3, I1, I2; local runners are free, SSH and later scheduler and container runners are paid)

## Context

On Windows the solver stack runs in WSL2, on Linux it runs natively, and the
development laptop has no WSL at all while a workstation does. fingen measured
the Windows–WSL file bridge at about 21 MB/s and found that project shares
cannot be mounted inside WSL. Big meshes and time directories must not cross
that bridge.

## Decision

A `stokes-agent` process runs wherever the engine runs and owns everything
that touches the engine's filesystem: staging, process supervision with
heartbeats, log parsing, VTK extraction and an artifact cache. The desktop
talks to it through one protocol (docs/05 §4) over localhost TCP, a Unix
socket or an SSH tunnel. On native Linux with a local engine the agent runs
in-process through the same API, so there is no socket to manage.

## Alternatives considered

| Alternative | Why not |
|---|---|
| Desktop reads results directly over `\\wsl.localhost` or SSHFS | Bandwidth and latency make large cases unusable; fingen measured it. |
| Run the whole desktop inside WSL with an X server | Poor UX, no native feel, breaks the Windows installer story. |
| A web service (fingen's FastAPI model) | Adds HTTP, a second UI technology and no benefit for a single-user desktop tool. |

## Consequences

- Remote workstations and later cluster schedulers are the same runner with
  a different transport.
- Runs survive the desktop closing; reconnect is a first-class feature.
- The agent must be installed on the engine host: the Windows installer
  places it in the chosen WSL distro, the Linux installer alongside the
  desktop, and the SSH runner deploys it to a remote host as a single file.
- Two deployable artifacts to version and test; the protocol is versioned
  from day one.
