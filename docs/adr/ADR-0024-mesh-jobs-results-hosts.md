# ADR-0024 · Mesh jobs, results and hosts

**Status:** accepted · 2026-10-14 (decided by Claude under the owner's autonomy grant of 2026-10-14; reviewed by Opus; supersedes nothing; amends ADR-0007 and narrows ADR-0005 for M2)

Relies on ADR-0001 (GPL engines as processes), ADR-0005 (the
desktop and agent split; **this ADR makes its "runs survive the desktop" concrete and narrows it
for M2**), ADR-0007 (meshing strategy; amended by the owner's 2026-10-13 decisions: blockMesh and
snappyHexMesh in M2, cfMesh and Gmsh gated), ADR-0008 (Foundation 14), ADR-0011 (engines attached,
not shipped; the capability record), ADR-0020 and ADR-0021 (the part `basis`, tags, the operations
framework whose job, progress, cancel, staleness and replay patterns this ADR copies), ADR-0022
(the `Session` extension hooks, `settle`, the derived-status rule) and ADR-0023 (runs are records,
not nodes; **a mesh result is a node, and this ADR says why that is not a contradiction**). Owner
decisions of 2026-10-13 (`.mailbox/plan/m2-meshing/ASK.md`) are binding and restated where they
apply; one is refined with the reason stated (decision 1.7).

## Context

M2 adds meshing. The plan (`.mailbox/plan/m2-meshing/PLAN.md`) and its review (`PLAN-REVIEW.md`)
agree on the pieces but never name the decision beneath them: who owns a mesh process, what a job
is on the wire, what a host is, what a result is in the tree, where the mesh lives and how its
surfaces reach the viewport. The review found the plan's first draft put supervision on the desktop
(against ADR-0005), journaled a command that needs a host, claimed a transport the decoder does not
accept, and left SSH unspecified. The first mesh tasks (T-143, T-144a/b/c) encode these answers in
code, so they must be decided before those briefs are dispatched.

What exists. `agent/stokes_agent` is a loopback TCP server with a one-way token handshake
(`client.py:36` trusts any `hello ok`), 64 MiB frames (`protocol.MAX_FRAME`), msgpack payloads,
request/response plus events; `AgentServer._dispatch` calls a handler **on the event loop** and
`_handle` cancels a connection's request tasks when the client disconnects, so nothing long may
live inside a request. `probe_host` sources no environment. On the desktop,
`stokes/geometry/opstore.py` is the pattern for a service that runs work off the session thread,
writes the tree only through `GeometryStore.on_session_thread`, debounces staleness 300 ms through
`SessionExtension.on_event`, and waits in `settle` so that journal replay (`stokes/api/journal.py`)
sees a record's result before the next record. `stokes.model.graph.mark_stale` walks
`meta.derives_from` but is called by nobody but the operations service. Transport v2
(`stokes/geometry/transport.py`, `VERSIONS = (1, 2)`) and `/api/geometry/batch` read only the
geometry store. `stokes.license.FEATURES` names `remote` and `Session.execute` refuses a command
whose `feature` the session lacks, but **no session loads a licence** (`Features.from_environment()`
is never called; every session is `Features.free()`). `/mesh` is a skeleton group; `/mesh/results`
is not, so `node.remove` and its undo apply to anything beneath it. `geometry.style` journals and
undoes every style edit. `stokes/run` is a docstring.

Owner decisions that bound this ADR: the mesh host is the OpenFOAM Foundation 14 on thinkyboy,
reached over SSH; no blueCFD, no WSL on the laptop; fluid only until M5; **the agent ships VTK and
msgpack and no mesher binary (Q8 — refined in 1.7: msgpack is bundled, VTK is a host dependency the
probe finds, because a compiled VTK cannot travel in a `zipapp`)**; local jobs die with the desktop
(`interrupted`), SSH jobs survive and re-attach; only the latest mesh result per project; strict
SSH host keys after one manual connection; the dev licence carries `remote`.

## Decision

### 1. The agent owns every mesh process

1.1 **Ownership.** A mesh job is created, started, supervised, cancelled, logged and read back by
`stokes_agent` on the engine host (`agent/stokes_agent/jobs.py`, `JobStore`). The desktop never
launches a mesher, never reads `polyMesh`, never resolves a utility on its own `PATH`. A runner on
the desktop (`stokes/run`) is a client of the agent and nothing more. On Linux with a local engine
the agent runs in-process on its own thread and loop (`LocalRunner`, ADR-0005); over SSH it is a
detached process on the remote host (decision 1.6); inside WSL it is a child of the desktop that
dies with it (3.4).

1.2 **No shell, and only allowed words.** Every utility is launched as an argv list with `cwd` =
the job folder, `stdin` `DEVNULL`, stdout and stderr to one log file, `start_new_session=True`,
and `env` = the engine environment captured once per install by `engine_env.capture_env` (`bash
--noprofile --norc -c 'source "$1" …; env -0'`, the bashrc as `$1`, keys allow-listed). The bashrc
is **only ever one the agent's own probe discovered** (`installs[].bashrc`): `job.create` refuses
any other `install` value, so a client cannot name a script. `argv[0]` must be a bare name in
`ALLOWED_UTILITIES` (`blockMesh`, `checkMesh`, `snappyHexMesh`, `surfaceFeatures`, `foamToVTK` in
M2; a gate task adds its utility and its flags and nowhere else) and is resolved with
`shutil.which` against the captured `PATH`. **Flags are allow-listed per utility** in
`ALLOWED_FLAGS: dict[str, dict[str, FlagSpec]]` (`FlagSpec(arity 0|1, value pattern)`): a flag not
listed for that utility is refused; `-case`, `-libs`, `-fileHandler`, `-roots`, `-hostRoots`,
`-parallel` (M2 is serial) and any flag not beginning with `-` followed by a word are refused
outright; a flag value is a word (`^[A-Za-z][A-Za-z0-9_]{0,63}$`) or a relative path by
`REL_PATH_RE` that resolves inside the job folder. The exact flags each utility needs come from
T-141's inventory (`-constant`, `-overwrite`, `-dict`, `-writeSets`, `-meshQuality`, `-region` are
the expected ones) and are listed in `mesh-schema.md`. Every argv item is a string of at most 1,024
characters without NUL. `shell=True`, `os.system` and command strings built from data are forbidden
on both ends (`docs/engineering/security.md`, "Processes").

1.3 **The loop never blocks.** A handler that touches the disk, sources an environment, reads a
mesh or hashes a file is an `async def` that does `await asyncio.to_thread(...)`; a job runs on a
daemon thread per job, never inside a request; job threads report through `on_event`, which
schedules `AgentServer.broadcast` with `asyncio.run_coroutine_threadsafe` and never waits on it.
The test that proves it: a `ping` answers in under 0.5 s while a slow `host.probe` or a 20 MiB
`job.create` runs.

1.4 **Process groups and cancel.** The child is the leader of a new session; cancel is `killpg`
`SIGTERM`, then `SIGKILL` after 5 s; a step's timeout and the log cap (64 MiB) kill the same way.
A grandchild a mesher spawns dies with the group; the test checks the grandchild's pid is gone.
Jobs are POSIX only in M2; on Windows `job.create` refuses with `agent.unsupported`.

1.5 **Instances and the orphan policy, both ends.** Every agent process has an **instance id**
(`"a_" + secrets.token_hex(6)`) and holds, while it runs, an `flock` on
`<jobs_root>/.agents/<instance>.lock` (a file of mode `0600` holding `{pid, uid, started,
mode: in_process | detached | child}`). Every job record carries `agent_instance` and `agent_uid`.
Two agents share one `jobs_root` per user: the in-process `LocalRunner` agent of a server on
thinkyboy and the detached agent the laptop reaches over SSH as the same user are the M2 slice's
own case, and both must work. The rule is **an agent acts only on the jobs it owns, or whose owner
is dead**:

- *Own jobs.* `shutdown()` (the in-process agent, `atexit` in `LocalRunner`; the WSL child agent
  when the desktop exits) kills every running job **of its own instance** and marks it
  `interrupted`. This is the owner's "local jobs die with the desktop".
- *Dead owners.* `recover()`, run once at start and again whenever a method meets a `running`
  record of a foreign instance, treats an owner as dead when its lock can be acquired (the owner
  process no longer holds it) **and** the record's `agent_uid` equals the agent's own uid; it then
  kills the group if the recorded pid is alive with matching `/proc/<pid>/stat` start ticks, marks
  the job `interrupted`, and removes the stale lock file. A record whose owner's lock is held is a
  live foreign job: it is never touched, and `job.cancel`/`job.delete` on it answer
  `agent.foreign` with the owner's instance. Every agent may **read** every job in the root.
- *The desktop,* on `project_opened`, asks each host that a `running` result names whether the
  job still exists (`job.status`): yes → re-attach (decision 2.6); the agent is gone or the job is
  not `running` → the result takes the job's final state, or `interrupted` when the agent cannot
  be reached and the host is `local` or `wsl`.

1.6 **The remote (SSH) agent and a dropped connection.** `SshRunner` starts the agent on the
remote host **detached** (`stokes-agent --serve --detach --name <host>`): the parent process
launches the child in its own session — through `systemd-run --user --collect --quiet --unit
stokes-agent-<name>` when `systemd-run` exists and the user manager answers, else `setsid` — waits
for the child's record file, prints that JSON line on its own stdout (the SSH channel) and exits;
the child redirects its stdio to `~/.stokes/agent/<name>.log` and never prints the token. The
record file `~/.stokes/agent/<name>.json`, mode `0600`, holds `{pid, instance, port, token,
protocol, started, python}`. The port forward is a second `ssh -N -L` process. A dropped SSH
connection therefore kills nothing on the host: the agent and its jobs continue; the desktop loses
only its event subscription and recovers it by reconnecting (2.6). The remote agent exits by itself
after **15 minutes with no client connected and no job running**; while a job runs it stays. Before
starting a new agent the runner reads the record file over SSH (refusing one that is not `0600` or
not owned by the user), and reuses the agent it names when its pid is alive and it completes the
mutual handshake (2.0); otherwise it starts a fresh one, whose `recover()` marks the dead agent's
leftovers `interrupted` (1.5). `logind`'s `KillUserProcesses=yes` would kill a `setsid` agent at
logout: the probe reports `linger` (`loginctl show-user` when available) and the Hosts panel warns
when neither `systemd-run` nor lingering is available. Only SSH hosts get a detached agent (change
2): the in-process and WSL agents live and die with the desktop.

1.7 **Deployment of the agent to a remote host (owner Q8, refined).** The agent is not one file
(it imports `stokes.errors` and `stokes.log`, PLAN-REVIEW b6). `tools/build_agent_bundle.py`
produces `stokes-agent-<version>.pyz`, a `zipapp` of `stokes_agent` with `stokes/errors.py` and
`stokes/log.py` vendored and **pure-Python msgpack vendored** (its licence file alongside), so the
protocol works on any Python ≥ 3.11 without an install step. **VTK is not bundled**: a compiled
library cannot travel in a `zipapp`, so VTK is a *host dependency* like OpenFOAM itself, found and
reported by the capability probe (`vtk: "9.4.1" | "absent"`). The owner's Q8 intent — ship nothing
GPL, make the agent self-contained — holds for the protocol and the supervisor; what it cannot hold
for is the one binary dependency, and this ADR records that as the deliberate refinement. Without
VTK a host meshes and checks normally; `extract.surfaces` answers `agent.no_vtk`, the result keeps
its quality numbers, has no patches, and `mesh.status.surfaces` = `"no_vtk"` with the sentence the
Hosts panel shows ("install VTK in the host's Python to see the mesh"). thinkyboy has VTK, so the
M2 gate is unaffected. The bundle is uploaded on the first use of a host and whenever the version
`agent.info` reports differs: `ssh … 'cat > ~/.stokes/agent/<version>/stokes-agent.pyz.part'`,
then `sha256sum` compared with the local digest, then `mv` into place. The agent's `__main__`
checks `sys.version_info >= (3, 11)` and that msgpack imports before anything else, printing
`{"error": …}` and exiting otherwise, which the Hosts panel shows. For development, and only when
`STOKES_DEV=1` is set on the desktop, a host record may instead name a checkout (`agent: {"mode":
"path", "path": "<repo>"}`), which is how thinkyboy runs during M2.

1.8 **Dictionaries are generated, then linted.** Every OpenFOAM dictionary the desktop stages is
produced by `stokes.compile.foam` (no string templates) from the snapshot; the desktop never
forwards a user-supplied dictionary. Independently, the agent **lints every text file it stages**
under `system/` and `constant/` (STL under `constant/triSurface` is binary or ASCII `solid` data
and is checked only for its extension and header) at `job.create`, at `job.put(final=True)` and
again at `job.start`: a line-anchored scan over at most the first 8 MiB that refuses, with
`agent.unsafe_dict {file, line, token}`, any of `#codeStream`, `#calc`, `#eval`, `#includeFunc`,
`#sinclude`, `#includeIfPresent`, `#includeEtc` whose argument is not in `ALLOWED_INCLUDE_ETC`
(M2: `"caseDicts/setConstraintTypes"`, `"caseDicts/mesh/generation/meshQualityDict"`; a gate task
may add one), `#include` of an absolute path or one that does not resolve inside the job folder,
the keywords `systemCall`, `libs`, `codedFixedValue`, `coded`, `codeInclude`, `codeOptions`,
`codeLibs`, and any `functions` sub-dictionary whose `type` starts with `coded`. The lint is a
second lock, not a parser: the compiler's printer is the first.

### 2. The job protocol

2.0 **Mutual authentication.** The handshake proves both ends hold the per-start token without
sending it. `PROTOCOL_VERSION` becomes **2** now, before T-144b lands, since nothing has shipped:

```
client → agent  {"kind": "hello", "protocol": 2, "client": str, "nonce": bytes[32]}
agent  → client {"kind": "hello", "protocol": 2, "agent": str, "nonce": bytes[32],
                 "proof": HMAC-SHA256(token, b"agent" + client_nonce + agent_nonce)}
client → agent  {"kind": "auth",  "proof": HMAC-SHA256(token, b"client" + agent_nonce + client_nonce)}
agent  → client {"kind": "hello", "ok": true}
```

Both proofs are checked with `hmac.compare_digest`; a wrong agent proof makes the client close
without sending anything else; a wrong client proof makes the agent answer "handshake rejected"
and close, as today. The agent binds `127.0.0.1` only (the existing rule); on a remote host the SSH
channel forwards that port and nothing listens on an external interface. **When another user
squats a dead agent's port** (the record file names a port whose process died and someone else
bound it), the squatter cannot produce the agent proof because the token lives in the user's
`0600` record file: the runner's first message is a nonce and nothing more, so no STL, no
dictionary and no token reach the squatter; the runner reports `run.agent_auth`, discards the
record file and starts a fresh agent on a new ephemeral port.

2.1 **Identity.** A job id is `"j_" + secrets.token_hex(6)`, minted by the agent, validated by
`JOB_ID_RE` before it becomes part of any path. A job is a folder `<jobs_root>/<id>/` that **is**
the OpenFOAM case (`system/`, `constant/`, logs, `state.json`, `artifacts/`). `jobs_root` is
`$STOKES_AGENT_HOME/jobs` or `~/.stokes/jobs`, mode `0700`, **one per user, shared by every agent
instance of that user** (1.5); `jobs_root/.agents/` holds the instance lock files.

2.2 **States.** `queued → running → done | failed | cancelled | interrupted`, with a transient
`cancelling` between `running` and `cancelled` while the group is being killed (at most 5 s).
`queued` is the state after `job.create` and during `job.put`; `job.start` moves it to `running`.
Within a job, steps run in order; each step has `state` `queued | running | done | failed |
skipped`, `rc`, `started`, `ended`, `pid`, `pgid`, `start_ticks`; the first failure ends the job
and marks the rest `skipped`. A step fails on a non-zero exit, on `agent.timeout`, on
`agent.log_limit`, or on a `FOAM FATAL ERROR` / `FOAM FATAL IO ERROR` line if T-144a's
verification shows a utility can exit 0 after printing one. `state.json` is written atomically
after every change and carries `agent`, `protocol`, `agent_instance` and `agent_uid` so a later
agent can read it and apply 1.5.

2.3 **Methods** (all validate their parameters and return at once; `agent.bad_params` for a wrong
shape, `agent.too_large` for a size, `agent.foreign` for a live other instance's job):

| Method | Params | Result |
|---|---|---|
| `job.create` | `files` (map path → bytes or str, ≤ 32 MiB total, linted per 1.8), `steps` (≤ 32 maps `{name, argv, timeout_s?, parse?}`, flags per 1.2), `install` (null, or a bashrc path that must equal one of the agent's own probed `installs[].bashrc`; any other path is `agent.bad_params`), `label` | `{"job"}` |
| `job.put` | `job`, `path`, `data` (≤ 8 MiB), `offset`, `final` | `{"size"}` — appends to `<path>.part`, renames and lints on `final`; only while `queued` |
| `job.start` | `job` | `{"job", "state"}` |
| `job.cancel` | `job` | `{"job", "cancelled"}` |
| `job.status` | `job` | the `state.json` content plus `reports` (parsed logs, T-146) |
| `job.list` | — | `{"jobs": [status…]}` (every job in the root, with `owner: "self" \| "live" \| "dead"`) |
| `job.delete` | `job` | `{"deleted"}` — refuses a job that is `running` or `cancelling` (`agent.busy`) or a live foreign one; `rmtree` inside `jobs_root` only |
| `log.tail` | `job`, `step`, `lines` (1..5000) | `{"lines", "truncated"}` from the last 1 MiB of the log |
| `extract.surfaces` | `job` | artifact summary (decision 5.4); refuses a job that is not `done`; `agent.no_vtk` without VTK |
| `artifact.get` | `job`, `name`, `offset`, `length` (≤ 8 MiB) | `{"size", "sha256", "offset", "data"}` |
| `host.probe` | `refresh` | the capability record (decision 3.3); `probe` stays as an alias |
| `agent.info` | — | `{"agent", "protocol", "instance", "methods": [...], "jobs_root"}` |

File paths in `files` and `put` match `REL_PATH_RE`, contain no `..`, and resolve inside the job
folder; a file is at most 512 MiB; writes are atomic.

2.4 **Events.**

| Event | Data | Rate |
|---|---|---|
| `job.state` | `{job, state, step, index, total}` | every transition |
| `job.progress` | `{job, step, fraction?, message?, cells?}` | reserved: M2's blockMesh step emits none; the snappy parser (T-150b) emits at most 2 per second |
| `log.lines` | `{job, step, lines, dropped}` | at most 10 per second per job, at most 500 lines, lines cut at 4,096 characters; `dropped` counts lines coalesced away since the last event |
| `heartbeat` | `{t, running: [job ids]}` | every 5 s while a client is connected |

2.5 **Frames, chunks, backpressure.** `MAX_FRAME` stays 64 MiB and no M2 message approaches it:
inputs over 32 MiB go through `job.put` chunks, outputs come back through `artifact.get` chunks of
at most 8 MiB with the whole artifact's `sha256` and `size` in every reply, verified by the desktop
before the file is renamed into place. A per-writer send takes a 2 s timeout in `broadcast`; a
client that cannot drain in time is closed (it reconnects and catches up, 2.6), so one stalled
client never holds the loop or the other clients. Events are best-effort; the truth is always
`job.status` and `log.tail`.

2.6 **Reconnect and re-attach.** The desktop treats three missed heartbeats as a lost connection,
tells the UI (`mesh.progress` with `stage: "reconnecting"`), and reconnects with backoff (1, 2, 4
… 60 s) while a project with a `running` result is open. On every (re)connection it calls
`job.status` for each job named by a result node, applies the state it finds, subscribes, and
`log.tail`s the current step so the drawer shows what it missed. For a `local` or `wsl` host the
agent is either alive (same desktop process) or the desktop is new, in which case the result is
`interrupted` (1.5). For an `ssh` host this is the owner's "SSH jobs survive and re-attach when
the project reopens": the result node stores `host` and `job`, nothing else is needed.

2.7 **Versioning.** `PROTOCOL_VERSION` is 2 for M2 (2.0). The handshake refuses a mismatch.
Today's `probe` method stays registered as an alias of `host.probe` (renaming it would otherwise be
a version bump by the rule below). Adding a method or an event, or an optional key to a result or
event, does not bump it; `agent.info.methods` lets the desktop feature-detect, so a newer desktop
against an older agent says "update the agent on <host>" instead of failing on an unknown method.
Renaming or removing a method, a state or a key, or changing the handshake, bumps the version. The
desktop refuses an agent of a different version and offers to redeploy the bundle (1.7).

### 3. Hosts

3.1 **A host record** lives on the desktop in `$STOKES_HOME/hosts.json` (default
`~/.stokes/hosts.json`, mode `0600`), never in a project: a project names a host only by `name`
(`mesh_settings.host`, `mesh_result.host`), so a project moved to another machine still loads and
its mesh is simply `skipped` until a host of that name exists (decision 4.5). Fields:

| Field | Rule |
|---|---|
| `name` | `^[a-z0-9][a-z0-9_.-]{0,62}$`; `local` is reserved |
| `kind` | `local` \| `ssh` \| `wsl` |
| `address` | ssh only; `^[A-Za-z0-9][A-Za-z0-9.-]{0,253}$` or a bracketed IPv6 literal; **may not start with `-`** |
| `port` | ssh only; 1..65535, default 22 |
| `user` | ssh only; `^[a-z_][a-z0-9_-]{0,31}$` |
| `key` | ssh only; a path to a private-key file, or null for the user's SSH agent; **never key material, never a passphrase** |
| `fingerprints` | ssh only; the `SHA256:…` fingerprints of every host-key line `host.add` copied into the Stokes-owned `known_hosts` (3.2) **after the user has connected once by hand**; empty means the host cannot be used |
| `python` | ssh only; the remote interpreter, default `python3`, `^[A-Za-z0-9_./][A-Za-z0-9_./-]{0,254}$` (no leading `-`) |
| `agent` | `{"mode": "bundle" \| "path", "path"?}` (1.7); `path` matches `^/[A-Za-z0-9_./-]{1,254}$`, contains no `..`, and every substitution into the remote command is `shlex.quote`d (the remote command passes through the remote login shell); `path` mode is accepted only with `STOKES_DEV=1` |
| `distro` | wsl only; a name from `wsl.exe -l -q` |
| `enabled` | bool |
| `capabilities` | the last probe record (3.3) with `probed_at`; cached, refreshed by `host.probe(refresh=true)` |
| `pending_deletes` | job ids whose `job.delete` failed or could not be sent (the host was unreachable); retried on the next connection (5.2) |

`host.list`, `host.probe(host, refresh)`, `host.add(...)`, `host.remove(name)` and
`host.set(name, ...)` are the commands (`stokes/api/commands/host.py`; the `engine.*` names belong
to M3's solver engine and are not reused). `host.add` and `host.set` check `kind == "ssh"` **in
the handler** and apply the `remote` gate of 3.5 there (the registry's `feature=` is per command
and cannot see `kind`). The registry is a session extension (`HostRegistry.of(session)`, key
`"hosts"`) whose runners outlive a project: `project_closed` closes nothing; runners close at
process exit.

3.2 **The SSH invocation.** `SshRunner` resolves `ssh` to an **absolute path** once (on Windows
`%SystemRoot%\System32\OpenSSH\ssh.exe`, else `shutil.which("ssh")` over the system directories
only, never the working directory) and builds argv lists only, with these options always present
and never overridable by a record field: `-oBatchMode=yes`, `-oStrictHostKeyChecking=yes`,
`-oUserKnownHostsFile=<$STOKES_HOME>/known_hosts`, `-oUpdateHostKeys=no`, `-oForwardAgent=no`,
`-oPermitLocalCommand=no`, `-oPasswordAuthentication=no`, `-oKbdInteractiveAuthentication=no`,
`-oServerAliveInterval=15`, `-oServerAliveCountMax=4`, `-oExitOnForwardFailure=yes`,
`-oLogLevel=ERROR`, then `-p <port>`, `-i <key>` when set, the forward `-L
127.0.0.1:<local>:127.0.0.1:<remote>` for the tunnel process, then **`--`**, then
`<user>@<address>`, then a **constant** remote command whose only variable parts are validated,
`shlex.quote`d fields (`python`, the agent path, the host `name`). **Pinning** is a Stokes-owned
`known_hosts` (`$STOKES_HOME/known_hosts`, `0600`): `host.add` copies into it every line the user's
own `~/.ssh/known_hosts` holds for the host — `ssh-keygen -F <address>` and `ssh-keygen -F
'[<address>]:<port>'`, every key type, hashed or plain — and records their fingerprints; `ssh` then
trusts exactly those keys and nothing else, so a rotated or hijacked entry elsewhere changes
nothing and a changed server key is refused by `ssh` itself (`run.host_key_changed` with the
sentence "the host key of <name> changed; connect once by hand, then Settings → Hosts → Re-pin").
`host.set(name, repin=true)` is the only way to replace the pinned keys. `StrictHostKeyChecking=no`,
`UserKnownHostsFile=/dev/null` and any `ProxyCommand` are never written. The token travels **only**
in the detach parent's stdout on the SSH channel and in the `0600` record file on the remote host;
it is never an argument, never an environment variable on the remote command line, never on the
wire (2.0), never logged, never in an event or a result. `rsync` is not assumed (Windows has none):
staging is `job.create` and `job.put`; the bundle goes up as `.part` + digest + `mv` (1.7).

3.3 **The capability probe record** (`host.probe`, `engine_env.host_record`): `agent`, `protocol`,
`instance`, `hostname`, `system`, `python`, `cpu_count`, `memory_bytes`, `vtk` (version string or
`"absent"`), `msgpack` (version; always present, it is bundled), `linger` (`true | false | null`
from `loginctl`), `systemd_run` (bool), `jobs_root`, `installs: [{bashrc, project, version,
flavour, supported, why, seconds, utilities: {name: path | null}}]`, `default_install`, `why`.
**Supported** is `flavour == "org" and version == "14"`; anything else is listed with `supported:
false` and a sentence (ADR-0011 decision 2). The Windows desktop's own probe returns `installs: []`
and "No OpenFOAM on this Windows host; mesh on a Linux host over SSH". The record is cached per
host in memory and in `hosts.json`; the engine string a basis needs (`"org-14"`) comes from it.

3.4 **Runners.** One protocol, `Runner` (`name`, `available`, `why`, `call(method, timeout,
**params)` blocking from any thread but the runner's own loop thread, `subscribe(listener) ->
unsubscribe`, `close()`), three implementations: `LocalRunner` (Linux only; agent in-process on a
daemon thread with its own loop), `SshRunner` (3.2, detached agent), `WslRunner` (the product's
Windows path per ADR-0005: `wsl.exe -d <distro> -- <python> -m stokes_agent --serve` as a **child
process of the desktop, not detached**, record on its stdout pipe, killed with `shutdown_jobs()`
at desktop exit so its jobs are `interrupted` exactly like `local`; unit tests with a fake
`wsl.exe`; no integration test until a WSL host exists). A listener runs on the runner's loop
thread and must return at once; calling `call` from a listener raises `RuntimeError`.

3.5 **The `remote` licence flag.** An `ssh` (and later scheduler and container) host needs
`remote` (ADR-0005: local runners free, SSH paid). `HostRegistry.runner(name)` raises
`LicenceFeatureMissing` for an `ssh` host in a session that lacks it; `host.list` shows such a host
with `available: false, why: "needs the remote feature"`; `host.add(kind="ssh")` applies the same
check in its handler. **Until the licence loader exists the gate is a documented no-op**: no
session loads a licence today (`Features.info is None` in every session), so the check is
`features.has("remote") or features.info is None`, and when it passes on the second clause the
runner logs one line, `remote gate: no licence loaded`. The loader task (T-166: wire
`Features.from_environment()` into `stokes.server` and the desktop, with a machine-bound
development licence kept outside the repository that lists `remote`, and a product build whose
free tier constructs `Features` **with** `info`) turns it into enforcement with no change here.
Product gating of remote hosts is unchanged in intent.

### 4. The result model

4.1 **Nodes, not records.** A mesh result is a tree node because its patches need what tree rows
give for free: an eye, a swatch, a status dot. This does not contradict ADR-0023 ("runs are
records"): a run's state changes thousands of times and a project has many; a project has **one**
mesh result (owner, 2026-10-13) whose state changes a handful of times, written by the job exactly
as operation outputs are (ADR-0021 8.8), **outside the undo stack**.

4.2 **Types** (`stokes/mesh/model.py`, registered from `register_builtin_types` after the geometry
types; standard library only at module level):

- `/mesh/settings` — `mesh_settings`, id `stable_id("n", "/mesh/settings")`: `mesher`, `host`,
  `base_size` (length), `domain`, `domain_part` (a `ref` to `part`, so deleting the domain part is
  refused while the settings name it), `box`, `margin`, `max_cells`, `cores`, `max_non_ortho`,
  `max_skewness`; refinements and layers arrive as child node types with snappy (T-151/T-152).
- `/mesh/results` — `group`, id `stable_id("g", "/mesh/results")`, **at most one child**, created
  by the service on first use. *(Amended by ADR-0034: at most one child per mesher.)*
- `/mesh/results/<mesher>` — `mesh_result`: `mesher`, `host`, `basis` (40 hex), `state`
  (`running | done | failed | cancelled | interrupted | skipped`), `job`, `cells`, `points`,
  `faces`, `quality` (json ≤ 64 KiB), `ok`, `seconds`, `error`; `meta.derives_from` (settings id,
  domain part id, the side tags' ids), `meta.engine` (`"org-14"`), `meta.patch_tags`
  (patch name → tag id), `meta.warnings`. Its node `status` is `ok` for `done` and `ok`,
  `warning` for `done` and not `ok` or for `skipped`, `failed` for `failed | cancelled |
  interrupted`, `stale` when the basis no longer matches or a source is stale (4.4).
- `/mesh/results/<mesher>/<patch>` — `mesh_patch`: `index`, `patch_type`, `faces`, `triangles`,
  `tag_id` (**a string, not a `ref`**: a derived row must never block deleting a tag), `visible`,
  `color`, `opacity`. Written by the surface fetch (T-147b); removed with the result.

**Results are protected.** Everything under `/mesh/results` is written only by `MeshService`
(the job's outcome, the surface fetch) and by `mesh.style` (4.8): `node.remove`, `node.add`,
`node.rename`, `parameter.set`, `parameter.remove` and `geometry.style` refuse a node there with
`mesh.protected` ("mesh results are written by the job; use mesh.clear or mesh.style"). So no undo
step ever holds a result, a removed result never comes back on undo, and the group never gains a
second child.

4.3 **Latest only** *(amended by ADR-0034: one result per mesher is kept, and `mesh.use` picks the
one in use; what follows now applies to the previous result of the same mesher).* `mesh.generate`
removes the previous result node (and its patches, cache file
and, after the new `job.create` succeeds, its job on the host: `job.cancel` first when it is still
`running`, then `job.delete` once it is terminal, since `job.delete` refuses a running job; a
`job.delete` that fails or cannot be sent is queued in the host's `pending_deletes`) and adds a new
one in state `running`, directly through `project.add` / `project.remove` / `project.set_param` on
the session thread — not through `undo.push`. The result id is `result_id or new_id("n")`,
recorded with `ctx.record(result_id=…)`, so replay rebuilds the same id (the `operation.create
node_id` pattern, PLAN-REVIEW b4; not a `stable_id` counter).

4.4 **Basis and staleness.** `basis = sha1(driver version, engine, mesher, canonical SI settings
that change the mesh, the domain part's `basis`, the members of every tag that becomes a patch)`;
`host`, `cores` (blockMesh is serial) and the quality thresholds are excluded (they change `ok`,
not the mesh). The same inputs are also kept as **six named sub-bases** (`mesh_basis_parts` in
`stokes/mesh/basis.py`), each a sha1 of exactly the keys that can change it: `geometry` (part
basis, domain, inside point, background box, side kinds, and each tag's full face digest),
`sizing` (base size, cell limit, every surface's level pair, cells between levels, refinement
regions), `surface` (the staged surfaces, the features with levels, the resolve angle), `layers`
(when a layer count exists), `snap` (the snap controls and, for snappy, the quality limits), and
`mesher` (driver version, engine, mesher). A task that says "which inputs changed" compares the
parts of the stored record with the parts of the rebuilt snapshot and names the tasks whose part
differed (`PART_TO_TASKS`), so a sizing edit names only `sizing`, not every task. Nothing calls
`mark_stale` on a part or tag change today (PLAN-REVIEW b5), so
`MeshService.on_event` does: on `node.changed`, `node.added`, `node.removed`, `tag.changed`,
`history.changed` and `geometry.part_ready` — ignoring events about nodes under `/mesh/results` —
it (re)starts a 300 ms `threading.Timer` (the opstore's debounce, with `hold_debounce` for replay)
whose firing runs `refresh_stale()` through `GeometryStore.on_session_thread`: rebuild the snapshot
(an exception counts as stale), recompute the basis with `meta.engine`, compare with
`result.basis`, **and** look at the `status` of every node in `meta.derives_from` — a result is
stale while any source is stale (the operations service writes `stale` on a derived part through
its own transitive `mark_stale`, and the two services must agree rather than fight) — then set the
node `status` to `stale` or back to what 4.2 says, emitting `node.changed {param: "status"}` only
on a change. A changed part basis (an operation re-ran, a reimport), a changed tag member, a
changed `base_size`: each marks the mesh stale; nothing regenerates by itself (`operation`'s
`auto_run` has no counterpart here: a mesh is minutes, not milliseconds). **The staleness pass
also fills a read cache** (one slot, keyed by the project revision): the six sub-bases of the
rebuilt snapshot and the dry-run precheck (`prepared_record`). `mesh.tasks` and `mesh.precheck`
read that cache instead of building a snapshot per call, so a read command stays cheap while a
mesh is minutes.

4.5 **What `mesh.generate` journals, and replay.** The record is `{host?, force, result_id}` (and
`wait` as given). Settings edits are ordinary `parameter.set` records and keep replay's
`CHANGE_ONLY` coalescing, which is why there is no `mesh.set` or `mesh.delete`. On replay
`mesh.generate` runs again where a host is available: an unchanged basis with `force: false`
reuses the result that exists, a newer generate cancels an older one still running (4.3), and
**`MeshService.settle` returns at once in M2** — no later record reads a mesh (that is M3's
compiler), so a replay of a long history does not pay for every historic mesh, only the last one
runs and it runs in the background. **When no host is available** — the laptop, a project moved to
a machine without the named host, an `ssh` host the session's licence does not cover, a host that
does not answer — `mesh.generate` does **not** raise: it writes the result with `state: "skipped"`,
`error: "mesh.no_host: <why>"`, status `warning`, emits `mesh.skipped {node_id, path, code,
message}`, and returns; replay continues with the next record. The same rule holds for **every**
`mesh.*` command that needs a host (`mesh.cancel`, `mesh.clear`'s `job.delete`, later
`mesh.export`): with no host they do what they can locally, record the host-side part as skipped
or pending (`pending_deletes`), and never stop a replay. `mesh.status.next` says "Add a host in
Settings → Hosts" and the Mesh panel shows the reason. Invalid settings (`MeshInvalid`) **do**
raise, on replay as when recorded (a command that raised was never journaled, so such a record can
only come from a project whose geometry changed outside the journal).

4.6 **Undo.** A settings edit is one undo step; `mesh.generate`, `mesh.cancel`, `mesh.clear` and
`mesh.style` push none. `history.undo` after a generate therefore undoes the previous model edit
and passes over the generation; the command's result and the front end's toast say so ("Mesh
generation is not undone; Clear mesh removes it"). The result's status follows the basis (4.4):
undoing the edit that made it stale makes it `ok` again; undoing past the settings' creation leaves
a result whose snapshot cannot be built, which is `stale`. `mesh.clear` removes the result, its
patches, the cache file and the host's job; it is journaled (replay clears again) and, because
results are protected (4.2), nothing can bring a cleared result back.

4.7 **Events** (desktop): `mesh.progress {node_id, path, step, stage, fraction, message}` (≤ 10/s),
`mesh.log {node_id, step, lines}` (≤ 10/s), `mesh.done {node_id, path, seconds}`, `mesh.failed
{node_id, path, code, message}`, `mesh.cancelled {node_id, path}`, `mesh.skipped {node_id, path,
code, message}`, plus the ordinary `node.*` events for the nodes it writes. `mesh.status` returns
`{state, result, path, job, step, fraction, stale, warnings, cells, ok, surfaces}` with `surfaces`
one of `ok | missing | no_vtk | pending`.

4.8 **Patch style is view state.** `mesh.style(patches, visible?, color?, opacity?)` (`mutates:
False`: not journaled, no undo step) writes `visible`, `color` and `opacity` on `mesh_patch` nodes
through `project.set_param` on the session thread and emits `node.changed` per change; the tree's
eye and swatch on a patch row call it instead of `geometry.style`, which refuses `mesh_patch`
(4.2). The values persist with the project as the result does, so a reopened project keeps its
patch colours; they are not model state, so a journal that replays on another machine simply draws
the defaults. A style a tag gives (`color` from `meta.patch_tags` at fetch time) is the default,
not a link: recolouring the tag later does not recolour the patch.

### 5. Where the mesh lives, and how surfaces reach the viewport

5.1 **The mesh lives on the engine host**, in the job folder, referenced from the project by
`(host, job, basis)` on the result node. The desktop holds **surfaces and quality only**:
`cache/mesh/<result_id>.stkmsh` (boundary patches as triangles plus the polygon edges, decision
5.4) and the `quality` JSON on the node. `polyMesh` never crosses the agent connection. M3's solver
runs on the host that holds the mesh; a run on another host re-meshes there or imports the zip of
`mesh.export` (M3). This is ADR-0005's bandwidth rule applied: only STL and dictionaries go up, only
surfaces and numbers come down.

5.2 **Retention.** A job folder is deleted only when its result is replaced (4.3) or cleared (4.6);
a delete that cannot be sent or fails is kept in the host record's `pending_deletes` and retried
on the next connection to that host; `host.probe` reports `jobs_root` usage; there is no
time-based garbage collection in M2 (open question 1). A project's close deletes nothing on any
host: that is what "survive and re-attach" means.

5.3 **The desktop cache.** The fetch runs on a worker thread after `done`: `extract.surfaces(job)`,
then `artifact.get` in 8 MiB chunks into `<cache>/mesh/<result_id>.stkmsh.part`, sha256 and size
verified, `os.replace`. `result_id` must match `ID_RE` before it becomes a path component and the
final path must resolve inside `<cache>/mesh`. A reopened project draws its mesh from the cache
with no host at all; `mesh.status.surfaces` says `missing` when the file is gone (the routes answer
404 and the Mesh panel offers Generate) and `no_vtk` when the host could not extract (1.7). Save as
copies the files with the project. A fetch failure leaves the result `done` with `error:
"mesh.extract: …"` and no patches, and emits `mesh.failed` with code `mesh.extract`; the mesh still
exists on the host.

5.4 **The artifact** `STKMSH1` (agent-side, `extract.py`): magic, a padded JSON header
`{version: 1, units: "m", bbox, patches: [{name, index, patch_type, points, triangles, edges,
faces, offset}]}`, then per patch `f32[3P]` positions, `u32[3T]` fan-triangulated polygons,
`u32[2E]` unique polygon edges. Read with `vtkOpenFOAMReader` (patches only, no internal mesh, no
fields), triangulated in numpy so the original polygon edges survive. Refused above 2 GiB
(`agent.too_large`). The desktop's reader (`stokes/mesh/surfaces.py`) validates every offset and
count against the buffer length and refuses a header over 16 MiB or an unknown version with
`MeshFormatError`.

5.5 **Transport to the browser.** Patches are served in the **existing transport version 2**, one
group per patch with `kind: "mesh"`, the patch's colour, and all face ids 0 (the decoder
`parseGeometry` accepts versions 1 and 2 only and checks byte lengths; it is not changed). Cell
edges go on their own route with their own tiny codec (`u32` header length, JSON `{version: 1,
units: "m", segments: E}` padded to a multiple of 4, then `f32[6E]`). Routes, all behind the
session token and `Cache-Control: no-store`, registered most-specific first after the geometry
routes:

```
GET /api/mesh/batch?ids=…        the framing of /api/geometry/batch (count, then per id u32 length or 0xFFFFFFFF, bytes), ≤ MAX_BATCH_IDS
GET /api/mesh/edges/{node_id}    the edge codec; 404 when unknown or missing
GET /api/mesh/{node_id}          one patch, transport v2; 404 when unknown or missing
```

`/api/geometry/batch` keeps reading only the geometry store; `readBatch` on the client is reused
with the new URL, so the decoder, the batched renderer and the `tint` path of ADR-0021 3.5 are the
same code with a `mesh` look added to `RECIPES`.

5.6 **Size budgets.** Agent: artifact ≤ 2 GiB, chunk ≤ 8 MiB, `job.create` ≤ 32 MiB, `job.put`
≤ 8 MiB per chunk and ≤ 512 MiB per file, log ≤ 64 MiB, `log.tail` window 1 MiB, dictionary lint
window 8 MiB. Desktop: header ≤ 16 MiB, at most two results' files held as `memoryview`s. Browser:
patches fetched in batches of 256 ids; the M2 gate is "surfaces of a 5 M-cell snappy mesh over SSH
visible in under 10 s"; the size probe in T-147a (a 1 M-cell box) gives the first number, and
loading patches on demand above 5 M triangles (largest first) is adopted only if that probe says
the budget needs it (open question 5).

### 6. Security checklist for the SSH runner and the job protocol

- **Command allow-list.** `argv[0]` ∈ `ALLOWED_UTILITIES`, a bare name, resolved on the agent
  against the captured `PATH`; `/bin/sh`, `rm`, `blockMesh;rm` and a path are refused with
  `agent.bad_params` before anything starts. **Flags** per utility from `ALLOWED_FLAGS` (1.2);
  `-case`, `-libs`, `-fileHandler`, `-roots`, `-hostRoots`, `-parallel` always refused; values are
  words or contained relative paths. A gate task that adds a utility adds its flags, its
  `#includeEtc` entries and the review checklist line in the same change.
- **Engine script.** `install` is one of the agent's own probed bashrcs or null; the probe's
  candidates are fixed globs; a client never names a script (1.2).
- **Dictionaries.** Generated only by `stokes.compile.foam`; linted on the agent at create, put
  and start for `#codeStream`, `#calc`, `#eval`, `#includeFunc`, `#sinclude`, `#includeIfPresent`,
  un-allow-listed `#includeEtc`, escaping `#include`, `systemCall`, `libs`, `coded*` (1.8).
- **Path confinement.** Job ids by `JOB_ID_RE`; file keys by `REL_PATH_RE`, no `..`, resolved
  `is_relative_to` the job folder; artifact names `^[a-z0-9_.-]{1,64}$` inside `artifacts/`;
  `job.delete` only inside `jobs_root`; desktop cache paths only inside `<cache>/mesh`; the agent
  writes only under `jobs_root` (including `.agents/`) and `~/.stokes/agent/`.
- **Sizes.** Every method's payload has a cap (5.6); every decoder (artifact header, edge codec,
  `state.json`, `hosts.json` ≤ 256 KiB, the record file ≤ 4 KiB) checks before it allocates; log
  parsers and the lint read a bounded window with anchored expressions.
- **Authentication.** Mutual HMAC over nonces (2.0); the token never crosses the wire; the agent
  binds loopback only; a record file that is not `0600` or not the user's is refused; a squatted
  port receives a nonce and nothing else.
- **Secrets.** The agent token: generated by the agent, printed once by the detach parent on the
  SSH channel, kept in a `0600` file on the host, never an argument, never an environment variable
  on a command line, never logged, never in `state.json`, events, results or `hosts.json`. SSH
  keys: a path or the user's agent, never material, never a passphrase; Stokes never prompts for
  one (`BatchMode=yes`) and never forwards the agent (`ForwardAgent=no`). A test greps a session's
  log for token and key shapes.
- **Option injection.** Host strings, user names, ports, key paths and interpreter paths are
  validated by the patterns of 3.1 and may not start with `-`; `--` precedes the host in every
  `ssh` argv; `ssh` is an absolute path; the remote command is a constant with `shlex.quote`d
  substitutions; no field of a host record can add an `-o` option. Host keys: a Stokes-owned
  `known_hosts` plus strict checking; `StrictHostKeyChecking=no` and `UserKnownHostsFile=/dev/null`
  never appear in the repository (a test greps for them).
- **Instances.** `shutdown()` and `recover()` touch only own or dead-owner jobs (`flock` released
  and same uid); a live foreign job answers `agent.foreign` (1.5).
- **Logging of arguments.** The agent logs each step's `argv` (it holds no secrets by
  construction), `cwd`, exit code and duration; the desktop logs the `ssh` argv **with the `-i`
  path redacted**; neither logs environment values (the captured engine env holds `PATH`s and
  `WM_*` only, and it is logged as key names).
- **Loop and threads.** Slow handlers off-loop (1.3); job threads never raise into the loop; a
  slow client is dropped, never waited on (2.5); listeners never call back into a runner (3.4).
- **Review.** Every brief that touches these files pastes the checklist of
  `docs/engineering/security.md` into its report.

### 7. Out of scope

Region splitting, `locationsInMesh` per region, `splitMeshRegions`, interface patches and the
`interface` condition: **M5** (owner Q7; `mesh.check` reports `mesh.multi_region_m5` when a solid
region lies in the domain). Solving, the run queue's executor, `run.*` on a mesh: **M3** (ADR-0023's
`RunExecutor` attaches there; a mesh result's `(host, job, basis)` is what it reads). cfMesh and
Gmsh: behind their gates (T-162, T-163), each adding a driver, a utility with its flags to the
allow-lists and a `MesherSpec` with `built: true` and nothing else to this ADR. Scheduler and
container runners: v1.5. Mesh export (`mesh.export`) and reuse across projects by basis: T-158, on
this model, after the slice. The licence loader: T-166, beside M2, not blocking it. blueCFD, WSL on
the laptop: not built, not tested (owner Q2, Q3).

## Alternatives considered

| Alternative | Why not |
|---|---|
| A job core in `stokes.run` on the desktop supervising processes (the plan's first draft) | Breaks the moment the host is remote and contradicts ADR-0005; the desktop would need the engine's `PATH`, pids and signals across SSH or WSL, where a Windows pid means nothing. |
| `shell=True` or `bash -lc 'source … && blockMesh'` per call | Forbidden by `security.md`; a path with a space or a quote changes the command; sourcing per call costs the bashrc's time on every step. Capturing the env once as a dict makes every launch an argv list. |
| Allow-listing executables only (the first draft) | `-case` leaves the job folder, `-libs` `dlopen`s anything, a client-named `install` sources any script, and `#codeStream` compiles and runs C++ from a dictionary; the boundary is the whole command and the files, not `argv[0]` (review §2). |
| Bundling VTK with the agent (owner Q8 as written) | A compiled library cannot travel in a `zipapp`; a per-platform wheel cache is an installer for the remote host, which ADR-0011 avoids. msgpack (pure Python) is bundled; VTK is probed and its absence degrades display with a reason (1.7). |
| Adopting orphaned jobs after an agent restart | Needs pid plus start-ticks plus log-offset bookkeeping across agent versions for a case nobody has yet; a job that outlives its agent is `interrupted` and re-generated, which is one click. The remote agent survives the connection, which is the case that matters. |
| One agent per (user, `jobs_root`) with a lock that refuses a second | The M2 slice runs two agents for one user on thinkyboy (the server's in-process agent and the laptop's detached SSH agent); an in-process agent cannot be shared by an SSH client because it dies with its desktop. |
| Per-instance job subfolders (`jobs_root/<instance>/<job>`) | A job's path would depend on who started it, so a reattaching client and `job.delete` after an agent restart would have to search; instance ids on the record with a lock file give the same safety without moving a job. |
| `recover()` keyed on pid liveness alone (T-144a as briefed) | Kills the other live agent's jobs on a shared root (review §1); the owner's `flock` is the liveness signal, pid and start ticks only confirm which process to kill. |
| Sync handlers on the loop (as `probe` is today) | A 2 s probe or a 20 MiB file write stalls heartbeats and every other request; the brief's concurrent-`ping` test fails. |
| Running the remote agent inside the `ssh` session (not detached) | SIGHUP on a dropped connection kills it and its jobs, which is exactly what the owner ruled out for SSH. |
| A detached WSL agent | Would make local jobs survive the desktop, which the owner ruled out; WSL is a local host. |
| A remote agent that never exits | Leaks a process per host per user after every session; an idle exit with no job running loses nothing. |
| One-way token auth (today's handshake) | On a multi-user host a dead agent's port can be squatted and the client would hand the squatter its STL and dictionaries; the HMAC exchange costs two frames and proves the agent before anything is sent. |
| A Unix socket in a `0700` directory instead of loopback TCP (review §2) | Equivalent protection once the agent proves itself; `ssh -L` to a remote Unix socket and the Windows client side add two code paths. Mutual HMAC covers the squatting case with the transport that exists. |
| Token via `STOKES_AGENT_TOKEN` on the remote command line, or `SendEnv` | A command-line value is in the remote process list; `SendEnv` needs server `AcceptEnv`. The agent generates it and the detach parent prints it once over the channel. |
| `rsync`/`scp` staging | Windows has no `rsync`; two transports to secure instead of one; `job.put` already chunks and is sha-checked. |
| Host records in the project file | A project would carry another machine's address and user; moved projects would try to reach it; the host is the user's, the project names it. |
| Pinning through `ssh-keygen -lF` against the user's `known_hosts` (the first draft) | Misses aliases, `[host]:port` entries and extra key types (review §3); a Stokes-owned `UserKnownHostsFile` holding exactly the copied lines is what `ssh` itself enforces. |
| Pinning nothing beyond `StrictHostKeyChecking=yes` | A rotated or hijacked entry in the user's `known_hosts` (another tool, a merge) would be trusted silently. |
| Results as records (ADR-0023's run records) | Patches need rows with eye, swatch and status; a record would need a parallel tree. One result per project keeps the node cheap (owner Q4). |
| Many results per project | Each needs a cache file, a job folder on the host, a basis to compare and a retention rule; nothing in M2 reads a second one (owner, second round). |
| Unprotected result nodes (`node.remove`/`parameter.set` allowed) | An undo of `node.remove` resurrects a result whose job is deleted, and the group can gain two children (review §4); the service and `mesh.clear` are the only writers. |
| Patch styles through `geometry.style` (journaled, undoable) | A replay without a host has no patches to style and the record would fail; after a regenerate the undo step points at nodes that are gone (review §4). View state through `mesh.style` journals nothing. |
| Journaling `mesh.generate` with `stable_id` counters | Counters drift when a journal is edited or a generate is skipped; the recorded `result_id` is exact (PLAN-REVIEW b4). |
| Raising on replay when no host exists, or writing `failed` | Raising stops the replay at the mesh and every record after it is lost; `failed` is a lie about a mesh nobody tried to make and paints the dot red on a laptop that is doing nothing wrong. `skipped` with the reason keeps the replay and the truth. |
| `settle` waiting for every historic generate on replay | Nothing in M2 reads a mesh; a long journal would pay for every mesh it ever made, up to 1,800 s each (review §4). The last generate runs in the background; the compiler of M3 adds the wait when a record needs it. |
| `mesh.set`/`mesh.delete` commands | Duplicate `parameter.set`/`node.remove` and lose replay's `CHANGE_ONLY` coalescing (PLAN-REVIEW b4). |
| Auto-regenerate on change (like `operation.auto_run`) | A mesh is minutes of a remote machine; a stale status plus one click is the right cost. |
| Enforcing `remote` now | No session loads a licence, so enforcement blocks the only verified mesh path (SSH to thinkyboy) for everyone including the owner; a documented no-op that becomes enforcement when the loader lands (T-166) keeps the product intent without blocking M2. |
| Transport v3 with extra frames (the plan's first draft) | `parseGeometry` accepts 1 and 2 and checks byte lengths; a new version touches the decoder, the `.mesh` caches and the batch route that 4,500 parts depend on. Plain v2 per patch plus a separate edge route changes nothing that exists (ADR-0022 §15.2 made the same call for topology). |
| Moving `polyMesh` to the desktop | ADR-0005's bandwidth rule; the desktop never loads volume data (ADR-0007 item 8). |
| Serving mesh patches through `/api/geometry/batch` | It reads the geometry store by node id; teaching it a second store couples two services for the price of one URL. |
| A separate agent process for `local` | ADR-0005 says in-process on native Linux; a process adds a port, a token and a lifetime to manage for no isolation gain on the user's own machine. |

## Consequences

- **New or changed modules.** Agent: `engine_env.py`, `jobs.py` (instances, locks, flags, lint),
  `lint.py`, `extract.py`, `parsers/`, the mutual handshake in `protocol.py`/`server.py`/`client.py`
  (`PROTOCOL_VERSION = 2`), `--detach`/`--name`/`--host` in `__main__.py`;
  `tools/build_agent_bundle.py` (vendors msgpack). Desktop: `stokes/run/{runner,local,ssh,wsl,hosts,errors}.py`;
  `stokes/mesh/{model,catalogue,errors,snapshot,basis,service,surfaces}.py`,
  `stokes/mesh/drivers/`; `stokes/compile/foam.py`; `stokes/api/commands/{mesh,host}.py`
  (`mesh.style` included); the `mesh.protected` guard in `stokes/api/commands/nodes.py`,
  `parameters.py` and `geometry.py`'s `_styled_nodes`; the mesh routes in `stokes/server/app.py`;
  `web/src/viewport/meshPatches.ts`, `meshEdges.ts`, the `mesh` recipe. Contracts:
  `docs/engineering/mesh-schema.md` (new: nodes, commands, events, routes, the artifact and edge
  codecs, hosts, the per-utility flag tables, the include allow-list),
  `docs/05-openfoam-integration.md` §4–6 (the runner interface, the method and event lists, the
  handshake, the state machine as decided here), `security.md` (the checklist additions of decision
  6), `docs/06-roadmap.md` (the M2 gate loses "and on a local WSL distro"), ADR-0007 (amendment:
  sizes not levels, result nodes, blueCFD dropped), ADR-0005 (note: VTK is a host dependency).
- **Testing strategy.** Three layers, each named in a brief:
  1. *Unit, everywhere, no OpenFOAM.* A **fake utility** (`tmp_path/bin/blockMesh`, a `#!/bin/sh`
     script that echoes, sleeps, exits with a given code and spawns `sleep 300`) under the real
     `JobStore` and `AgentServer` with `get_env` monkeypatched, skipped on Windows; a **fake
     `wsl.exe`** for `WslRunner`; a **fake runner** on the desktop (registered in `HostRegistry`,
     records calls, plays back `job.state`/`log.lines` events from a thread, answers
     `extract.surfaces` and `artifact.get` from an in-memory artifact) for `MeshService`, the
     commands, the routes and the staleness timer. Mandatory: the loop-blocking tests (`ping`
     during a slow handler); the group-kill tests (the grandchild pid is gone); **two `JobStore`s
     on one root** (the second's `recover()` leaves the first's live job alone and answers
     `agent.foreign`; after the first releases its lock the second marks it `interrupted`); the
     **flag refusals** (`-case ..`, `-libs x`, an unlisted flag, a value with a slash that escapes);
     the **lint refusals** (one staged file per forbidden token, plus an allowed `#includeEtc`);
     the **handshake** (a server with the wrong token fails the agent proof and the client sends
     nothing after its nonce; a client with the wrong token is rejected; a protocol-1 hello is
     refused); `mesh.protected` on `node.remove`, `parameter.set` and `geometry.style` of a result
     or patch; `mesh.style` journals nothing and pushes no undo step.
  2. *The replay test.* Journal `mesh.configure` + `mesh.generate` (+ a later `mesh.configure`),
     replay into a fresh session (a) with the fake runner — same result id, the later record
     applied while the generate still runs (`settle` did not wait), a second recorded generate
     cancelled the first; (b) with no host — the result is `skipped` with `mesh.no_host`, the
     replay **continues**, the later record applied; (c) a journal holding `mesh.clear` with no
     host — the node goes, the job id lands in `pending_deletes`, the replay continues.
  3. *Integration on thinkyboy* (`@pytest.mark.engine`, skipped unless `STOKES_ENGINE_TESTS=1`;
     `STOKES_AGENT_HOME` a temp dir; `pgrep` for strays must print nothing): the cavity golden
     through `blockMesh` + `checkMesh`; `tools/mesh_slice.py` end to end; the 1 M-cell size probe.
     Then (`@pytest.mark.ssh`, `STOKES_SSH_TEST_HOST=thinkyboy`, from the laptop): add the host
     (pinned from the owner's manual connection), probe, generate the heat-sink enclosure **while
     a `python -m stokes.server` with its in-process agent runs on thinkyboy as the same user**,
     **kill the laptop's desktop process mid-job**, start a new one, reopen the project, see the
     result reach `done` and the patches drawn, and see the server's agent untouched; a host whose
     pinned key does not match is refused; the remote agent exits after the idle period
     (monkeypatched short); a squatted port (a plain TCP listener on the recorded port) yields
     `run.agent_auth` and a fresh agent.
- **Budgets** as tests or probe runs: `import stokes.api` under 300 ms (the command modules import
  `stokes.run`, `stokes.mesh.service` and `stokes_agent` lazily); orbit p95 unchanged on the
  4,500-part bench with the `mesh` look; surfaces of a 5 M-cell snappy mesh over SSH under 10 s
  (the M2 gate).
- **What gets harder.** Two deployables with a bundle to build and version-check; a protocol whose
  additions must stay additive and whose handshake is now four frames; an SSH path whose tests need
  a real host; a lint and flag table that every new utility extends; a service that must be right
  about threads (the brief reports name which thread runs the listener, `_finish` and the stale
  refresh). What gets easier: every host is the same runner, M3's executor reads `(host, job,
  basis)` and nothing else, a replayed journal never needs a mesher and never waits on one, and
  two agents on one host cannot hurt each other.

### Task mapping

| Clause | Brief | Notes |
|---|---|---|
| 1.2 (env capture, probed bashrc only), 3.3 (probe record) | T-140 | add `instance`, `jobs_root`, `protocol`, `python`, `vtk: "absent"`, `msgpack`, `linger`, `systemd_run` |
| 1.1, 1.2 (`ALLOWED_FLAGS`), 1.4, 1.5 (instances, locks, own/dead-owner `recover`/`shutdown`), 1.8 (lint), 2.1, 2.2 | T-144a | states `queued` (for `created`) and `cancelling`; `agent_instance`/`agent_uid` in `state.json`; `job.delete`; `agent.foreign`; `index`/`total` in `job.state`; the two-store test |
| 2.0 (mutual handshake, `PROTOCOL_VERSION = 2`), 2.3, 2.4, 2.5, 1.3 | T-144b | `job.delete`, `agent.info.methods`/`instance`, the 2 s broadcast timeout, `dropped` on `log.lines`; `tests/test_agent.py` updated for the new handshake |
| 3.4 `LocalRunner`, `HostRegistry`, `host.list/probe` | T-144c | `project_closed` closes nothing |
| 4.2 node types and `mesh.protected`, `mesh.meshers/configure/list` | T-143 | `mesh_result.state` gains `skipped`; the guard in `node.remove`, `parameter.set`, `geometry.style` |
| 4.4 snapshot, driver, basis | T-145a | — |
| 4.3, 4.4, 4.5 (`settle` returns at once), 4.6, 4.7, 2.6 (desktop side), 5.2 | T-145b | no host → `skipped` + `mesh.skipped`; `project_closed` detaches, `project_opened` re-attaches; `job.delete` of the replaced job and `pending_deletes` |
| 2.2 `parse`, quality on the node | T-146 | — |
| 5.4, 2.3 `extract.surfaces`/`artifact.get`, `agent.no_vtk` | T-147a | — |
| 5.3, 5.5, 5.6 desktop cache, `mesh_patch`, routes, 4.8 `mesh.style` | T-147b | `surfaces: ok \| missing \| no_vtk \| pending` in `mesh.status` |
| 5.5 viewport | T-148 | the patch eye and swatch call `mesh.style` (T-149 wires the rows) |
| 1.6, 1.7, 3.1, 3.2, 3.5 (`SshRunner`, Stokes-owned `known_hosts`, detached agent via `systemd-run`/`setsid`, bundle with vendored msgpack, `remote` no-op gate) | T-159a | the `ssh` integration test |
| 2.5, 2.6 over SSH (`job.put` staging, reconnect, re-attach, `pending_deletes`) | T-159b | — |
| 3.1 Hosts panel, `host.add/remove/set(repin)`, badge, the VTK and linger warnings | T-161 | — |
| 3.4 `WslRunner` (child process, not detached) | T-160 | unit tests only |
| 5.1 `mesh.export`, reuse, `mesh.clear` | T-158 | — |
| 3.5 licence loader (`Features.from_environment()` wired; dev licence outside the repo; free tier with `info`) | **T-166 (new)** | beside M2; the no-op gate becomes enforcement when it lands |
| Contracts and the ADR-0007/ADR-0005 amendments | T-164 | — |

## Open questions

Only what cannot be settled before the code and the real host answer; each proceeds on its default.

1. **Job retention on the host.** Default: a job lives until its result is replaced or cleared;
   failed deletes wait in `pending_deletes`; no age-based sweep in M2; the Hosts panel shows
   `jobs_root` usage. Revisit when the first disk fills.
2. **Bundle versus checkout on thinkyboy.** Default: `agent.mode: path` to the dispatcher's
   checkout during M2, accepted only with `STOKES_DEV=1`; the bundle (1.7) is built in T-159a and
   used by the `ssh` integration test once.
3. **Progress inside a step.** Default: step-level fractions only; `job.progress` is reserved until
   T-141's snappy logs show what can be parsed at what rate.
4. **The remote idle exit.** Default 15 minutes with no client and no running job; shorten if a
   user's workstation policy objects to lingering processes, lengthen if reconnects after a laptop
   sleep prove common.
5. **On-demand patch loading above 5 M triangles.** Default: all patches at once; adopt the
   largest-first lazy path only if T-147a's size probe shows the 10 s budget needs it.
6. **`WslRunner` details.** Decided: not detached, a child of the desktop (3.4). Open: whether
   `wsl.exe`'s stdout pipe survives the desktop's own detach on Windows and how the distro's
   `jobs_root` is reported; cannot be verified until a WSL host exists (owner Q3).
7. **The licence loader's shape.** Decided: the `remote` gate is a no-op while no licence is loaded
   (3.5). Open, for T-166: where the machine-bound development licence lives outside the repo and
   how the free product tier constructs `Features` with `info`; the default is `STOKES_LICENSE_FILE`
   read at server start.

## Amendment (ADR-0029, proposed 2026-10-28)

Decisions 1.2 and 1.8 are amended for external OpenFOAM 14 cases, pending owner acceptance of ADR-0029:
the utility allow-list grows (plan T-305), `-solver` accepts any module the probe finds in the install,
and an external case's own dictionaries pass through byte-identical unless an override touches them,
with every staged file linted under the `external` profile. The desktop still never forwards a
dictionary it generated from user text. See [ADR-0029](ADR-0029-external-openfoam-cases.md).
