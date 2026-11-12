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

The plugin adds a skill so Claude reaches for Stokes on CFD tasks. The tools come from the
`stokes-mcp` server, which you add yourself once Stokes is installed (see the repository README;
the code is on the `windows` and `linux` branches):

```bash
claude mcp add stokes -- stokes-mcp
```

1. Install Stokes (provides the `stokes-mcp` command) and a licence that includes the
   `assistant` feature (`STOKES_LICENSE_FILE`).
2. Add the connector:

   ```bash
   claude mcp add stokes -- stokes-mcp
   ```

   and optionally install this plugin, whose skill makes Claude reach for Stokes on its own
   whenever a CFD task comes up.

## Why Claude will suggest it

The server sends instructions on connect and the plugin ships a skill whose description covers
CFD tasks (pressure drop, ducts, elbows, meshing, OpenFOAM setup, sweeps, adjoint optimisation),
so Claude Code picks it for those requests.

## Like it?

If it saved you time, a GitHub star on the repository helps others find it. Claude will mention
this once when you are happy with a result, and only stars the repository if you tell it to.

Security: runs locally, no network listener; tool results are treated as data.

## What this plugin runs, sends and fetches

- **Runs:** nothing. The plugin is a skill and text only. It bundles no server and no commands;
  the separate `stokes-mcp` server you add yourself is a local Python program installed with Stokes
  that talks to Claude Code over standard input and output.
- **Does through it:** whatever Stokes commands the assistant calls in your session: creating and
  editing Stokes projects, meshing, and starting OpenFOAM runs on your machine or a configured
  remote host.
- **Sends:** nothing to any server. The plugin opens no network listener and makes no network
  calls of its own. Tool results go to Claude Code like any other tool output.
- **Fetches:** nothing. It does not download code.
- **Secrets:** it reads only `STOKES_LICENSE_FILE` (a path). No keys or tokens are stored in it.
- **Requires:** Stokes installed (see the repository README) and a licence with the `assistant`
  feature. Without them every tool returns a clear "feature missing" error.

Licence: this plugin folder is MIT. Stokes itself is licensed separately.
