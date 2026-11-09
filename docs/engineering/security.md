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

**Agent log parsers** (`agent/stokes_agent/parsers/`) run on utility output the agent did not
write, so each is bounded the same three ways: it keeps at most the last 4 MiB of the log
(`MAX_BYTES`), skips any line over 4096 characters (`MAX_LINE`), and matches only anchored
patterns written against real captured logs in `tests/fixtures/of14/logs/`. `checkMesh` reads
the quality summary (T-208), `foamRun` the residuals (T-213), and `snappy` the final layer
coverage table (brief M-504, fixture `logs/snappy-layers.log`); the hostile-input cases — an
empty log, a ten-megabyte line, binary noise, a row short of its columns — are in the parser
tests, and a parser that meets an unreadable line skips it rather than raising, because a
parser that throws turns a successful mesh into a failed job.

**Staged dictionaries** are linted by the agent before any utility reads them
(`stokes_agent.lint`, ADR-0024 §1.8): the current profile admits only what Stokes' own writers
emit. **External cases** (ADR-0029 decision 5) use a second profile, `external`
(`stokes_agent.lint_external`), which shares every code-execution refusal (`#codeStream`,
`#calc`, `#eval`, `#codeBlock`, `coded*`, `systemCall`, the `$` macro allow-list, quoted pattern
keys) and admits, **only against the probed install**: `#includeEtc "caseDicts/…"` when that file
is in the probed `$FOAM_ETC`; `#includeFunc`/`-func` names the install ships, with arguments that
carry no path; `#includeConstraint` (OpenFOAM 14's form for `fvConstraints`) with a plain name and path-free arguments, checked like `#includeModel`; `libs` naming `lib<name>[.so|.dll]` the probe listed; `#include` and `-dict` paths
that resolve inside the job (`..` allowed while they stay in, `$FOAM_CASE/` as the job root,
polyMesh and `constant/geometry` reserved). Surface data under `constant/geometry/` with a surface
extension and a matching header passes unread, because no dictionary can name it (a `.gz` surface
is decompressed to the end and refused if it expands past the 8 MiB text cap, so a gzip bomb fails
at lint time); any other `.gz` is decompressed (bounded) and linted; other binary files are
refused. Refusals are `agent.unsafe_dict` with file, line and token. The external utility and flag
list (`stokes_agent.flags_external`) is closed like the current one: `-solver` takes only a probed
module, values are checked, and `-case`, `-libs`, `-roots`, `-hostRoots`, `-fileHandler` and raw
`-parallel` stay refused. The current entry points (`lint_text`, `lint_file`, `lint_job`,
`flags.ALLOWED_FLAGS`, `jobs.ALLOWED_UTILITIES`) take no profile argument and are unchanged but
for one stricter rule: reserved include components are judged folded, so `#include "geometry./x"`
(which Windows opens as `geometry/x`) is now refused.

**The cfMesh volume fills** (ADR-0032) add `cartesianMesh`, `tetMesh`, `pMesh` and
`polyDualMesh` to `jobs.ALLOWED_UTILITIES`, each with no flags. `polyDualMesh` is the one utility
with a positional argument (`flags.POSITIONAL`): one feature angle, 0 to 180 with at most three
decimals, matched in full and taken once. A cfMesh step may carry `threads` (1 to 256); the
agent sets `OMP_NUM_THREADS` in that process's environment only, and refuses `threads` on any
other utility and on every external step.

**How a job takes the external profile** (T-309). `job.create` takes `profile: "external"`, and
the agent records it in the job's own record. From then on every check of that job uses the
external rules: `create`, each `put`, `start`, and the re-check before each step launches. The
install lists those rules check against come from **the agent's own read** of the engine's
`FOAM_*` folders (`probe.install_inventory`, cached per engine), never from the request. An
external job may also stage time directories and `0.orig/` (`jobpaths.EXTERNAL_TOP_RE`), may not
`copy_from` another job, and may run MPI only for `foamRun`, `foamMultiRun`, `snappyHexMesh` and
`checkMesh`. A record whose profile is not one the agent knows is refused at `start`, not run
under the default rules.

**Residual risk, `#includeEtc` shadowing.** OpenFOAM resolves `#includeEtc` by searching the
user's etc folders (`~/.OpenFOAM/<version>`, `~/.OpenFOAM`) and the site folder
(`$WM_PROJECT_SITE` or `$WM_PROJECT_INST_DIR/site`) **before** `$FOAM_ETC`. The lint checks the
name against the probed `$FOAM_ETC` list only, so a file of the same name in a user or site etc
folder on the agent host would be read instead. That content is outside the job, and neither the
lint nor the job can control it; it is trusted as part of the host's installation, like the
OpenFOAM binaries themselves.

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
