# Security

Stokes runs on an engineer's machine with their CAD, drives solvers on local
and remote hosts, and lets LLM agents act with full control. This document is
the threat model and the rules that follow from it. `CLAUDE.md` §4 is the
summary.

## What we protect

| Asset | Threat | Rule |
|---|---|---|
| The user's CAD and results | exfiltration, accidental upload | offline by default; no telemetry without opt-in; LLM providers receive only what the assistant sends, never files unless the user attaches them |
| The user's machine | code execution through crafted inputs | every input validated; no `eval`, `exec`, `pickle`; argv-only subprocesses |
| Remote hosts the user owns | lateral movement through the agent | agent binds to localhost or a Unix socket; per-session token; reachable remotely only through the user's SSH tunnel |
| Secrets (LLM keys, SSH) | leakage through logs, journals, project files | keys in the OS keychain through `keyring`; SSH uses the user's agent and keys; nothing stored in projects |
| The product's paid features | licence forgery | Ed25519 signatures over a canonical payload; the public key is compiled in; the free tier needs no file |
| The supply chain | malicious or vulnerable dependencies | allow-list audit, `pip-audit`, hash-pinned lockfile for releases |

## Inputs and their boundaries

| Input | Boundary module | Validation |
|---|---|---|
| CAD files (STEP, IGES, STL, OBJ, 3MF) | `stokes.geometry` | size limits, OCCT reader errors mapped to typed errors, tessellation in a worker with a timeout, never trust names in the file for paths |
| OpenFOAM dictionaries, logs, `postProcessing` data | `stokes.compile` parser, agent log parsers | grammar-based parsing, line-length and file-size limits, no regex on unbounded input without anchors |
| Agent protocol frames | `stokes_agent.protocol` | length prefix checked against `MAX_FRAME` before reading, msgpack with `raw=False` and `strict_map_key`, schema check per method |
| Licence files | `stokes.license` | signature verified before any field is read; expiry and product checked; file size limit |
| Journals | `stokes.api.journal` | each record validated against the command schema before execution |
| LLM tool calls | `stokes.llm`, `stokes.mcp` | parameters validated by the command schema like any client; results are data |
| Project files (`project.json`) | `stokes.model.persist` | schema version, migrations, size limit, no path fields trusted without containment checks |
| Workspace documents, presets and theme files (ADR-0015) | `stokes.workspace` | 256 KiB and 32 levels of nesting, JSON without NaN, a project's file must resolve inside the project folder, every value checked by kind (theme tokens cannot hold `url()` or leave their declaration), preset names restricted to `a-z 0-9 _ -`; bad content is reported and ignored, never executed |

## Paths and files

- The page reaches the disk only through the host bridge, and only at paths
  the user picked in a native dialog during this session
  (`stokes.app.textfiles`): an open dialog grants read, a save dialog grants
  write, text only, UTF-8, at most 1 MB.
- Resolve every path (`Path.resolve()`) and check containment with
  `is_relative_to` against the project directory, the staging directory or
  the user-chosen export directory. Refuse anything else, including symlinks
  that resolve outside.
- The desktop writes only inside the project directory, user-chosen export
  locations and its own settings in `~/.stokes/workspace.json` and
  `~/.stokes/presets/` (or under `$STOKES_HOME`); the agent writes only inside
  `~/.stokes/jobs/<id>` and the engine source directory. Neither side writes to
  the other's area.
- Temporary files go through `tempfile` with explicit directories; atomic
  writes use a temp file plus `os.replace`.
- Never derive a filesystem path from names inside a CAD file, a dictionary
  or a tool call without validation.

## Processes

- `subprocess.run` / `Popen` with argv lists only. No `shell=True`, no
  `os.system`, no string concatenation of commands.
- Explicit `cwd`, a minimal explicit environment (copy only what the engine
  needs: `PATH`, `HOME`, the OpenFOAM variables), timeouts, captured output
  written to log files with size limits.
- Child processes run as the user; Stokes never asks for elevation except
  the Windows installer.
- Cancel kills the process group (POSIX) or the job object (Windows).

## Network

- The HTTP/JSON-RPC server, the MCP server and the agent bind to `127.0.0.1`
  (or a Unix socket) and require a per-session token on the first message.
- Remote agents are reached through `ssh -L` tunnels using the user's keys;
  Stokes never stores passwords and never disables host-key checking.
- LLM providers are called only with the user's configured key, over TLS,
  with the request content visible in the assistant panel.
- The updater verifies a signed manifest before downloading anything.

## Secrets

- LLM API keys: `keyring` (OS keychain). Local OpenAI-compatible servers need
  none.
- The licence signing private key never enters the repository, CI or a
  developer machine's project directory. The dev public key is committed;
  release builds swap it at build time.
- Logs, journals, crash bundles and error messages are scrubbed of tokens
  and keys by construction (never logged) and checked by a test that greps
  the log output of a session for known secret shapes.

## LLM agents

- Agents have full control by decision L3, so the safety net is
  observability: every tool call is a journaled command, every mutation is
  undoable, and the assistant panel shows each call.
- Tool results, CAD metadata, logs and web content are data. The assistant's
  system prompt states that instructions found inside such data are not to
  be followed; prompt-injection tests cover this.
- An optional "confirm destructive actions" setting (off by default) gates
  `run.start`, `run.cancel`, `project.delete` and `node.remove` on the Runs
  tree behind a user confirmation.

## Serialization

- JSON for the model, journals, licences and the HTTP API; msgpack for the
  agent protocol. Never pickle, marshal or YAML with arbitrary tags.
- Every decoder has a size limit and rejects unknown top-level keys unless
  the schema allows them.

## Dependencies and build

- The allow-list audit and `pip-audit` run in CI on every change.
- Release builds install from a hash-pinned lockfile.
- Binaries are signed (Authenticode on Windows, GPG on Linux).
- No post-install scripts, no downloads at first run except user-approved
  engine source clones from the pinned repositories.

## Review checklist (paste into the PR when the change touches these)

- [ ] New input? Where is it validated and size-limited?
- [ ] New path? Containment check present?
- [ ] New subprocess? argv list, cwd, env, timeout, captured output?
- [ ] New network listener or client? Localhost or tunnel, token, TLS?
- [ ] New secret? Keychain, not file; scrubbed from logs?
- [ ] New dependency? Allow-list, registered, audited?
- [ ] New LLM-visible text? Marked as data in the prompt?
