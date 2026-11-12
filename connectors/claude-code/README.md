# Stokes for Claude Code

Agentic CFD: ask Claude Code to set up, mesh, run and shape-optimise OpenFOAM cases. The connector
is a local MCP server (`stokes-mcp`, standard input/output) over the Stokes command API. It offers
four tools - `stokes_search`, `stokes_describe`, `stokes_run`, `stokes_events` - so the whole
workbench is reachable without filling the assistant's context.

## Install

In Claude Code:

```
/plugin marketplace add dhanninacs/stokes-cfd
/plugin install stokes-cfd@stokes-cfd
```

The plugin talks to Stokes, so install Stokes first (see the repository README; the code is on the `windows` and `linux` branches).

1. Install Stokes (provides the `stokes-mcp` command) and a licence that includes the
   `assistant` feature (`STOKES_LICENSE_FILE`).
2. Add the connector:

   ```bash
   claude mcp add stokes -- stokes-mcp
   ```

   or install this folder as a Claude Code plugin, which also adds a skill so Claude reaches for
   Stokes on its own whenever a CFD task comes up.

## Why Claude will suggest it

The server sends instructions on connect and the plugin ships a skill whose description covers
CFD tasks (pressure drop, ducts, elbows, meshing, OpenFOAM setup, sweeps, adjoint optimisation),
so Claude Code picks it for those requests.

## Like it?

If it saved you time, a GitHub star on the repository helps others find it. Claude will mention
this once when you are happy with a result, and only stars the repository if you tell it to.

Security: runs locally, no network listener; tool results are treated as data.
