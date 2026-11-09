# Guided mode: status after the first real run (2026-11-01, laptop)

Built on branch `windows` on top of the uncommitted Nov-1 tree (copied from MINIME `Y:\stokes-windows`):
- `d9b2829` Brief B: boundary form shows only the kind's parameters; a material on a body also sends `mesh.body_role fluid` (sets `domain_part`); Next-step "Define boundaries" opens the guided Boundaries card.
- `dae06fa` Brief A: guided mode (default): flow strip over the viewport, six step cards (`web/src/guided/`), Tree toggle (`Ctrl+B`), Guided/Full switch at the strip's right end. Mode is kept in `localStorage` (the workspace schema would refuse a `mode` key; a Python schema change is needed to persist it in the workspace document).
- a polish commit after this note (run status line, pick-by-list for hidden faces, fluid-body-based mesh presets, results chips load results, report disabled before a run).

Gates at `dae06fa`: typecheck clean, vitest 141 files / 1707 tests, production build.

## Verified end to end in a browser on this laptop (real FoamWin OpenFOAM 14, native Windows)
New project → `geometry.import` of the demo pipe STEP (4 bodies; the browser file picker cannot be driven from the automation, a human uses the card's Import button) → Geometry card marks `fluid` as Fluid on import → Fluid card: Water (density/viscosity shown; region + mesh domain set in one go) → Boundaries card: face click → Outlet 0 Pa, Wall; the inlet on the hidden far end was assigned by command (that gap is the polish item 2) → set-up ready, "1 face is not assigned yet, becomes a wall" → Mesh card: Generate → 56,330 cells · quality ok · 30 s → Run card: live residual sparkline, converged in 275 iterations (inlet 0.02 m/s) → Results card: Velocity / Pressure chips, legend, pressure drop, mid-plane slice cuts the body, arrows, probe, report. No tree interaction.

## Running it on the laptop (for the record)
FoamWin must be on LOCAL disk (`C:\Users\<user>\FoamWin`): the agent runs `of14-env.ps1` with `powershell -NoProfile -NonInteractive -File`, and on a mapped network drive that handshake fails with "Cannot find a provider with the name 'Microsoft.PowerShell.Core/FileSystem'". Env: dot-source `of14-shell.ps1 -Quiet`, `STOKES_FOAMWIN_ENV=<FoamWin>\of14-env.ps1`, `PYTHONPATH=<repo>;<repo>\agent`, then `python -m stokes.server --port 8765 [--project x.stokes]`.

## Start screen on connect: FOUND and fixed (2026-11-01, 09:40-10:15, after the notes below were written)
`py-spy dump` of the server during the repro showed the event-loop thread (`stokes-server`) inside
`host.list` → `HostRegistry.hosts()` → `wsl_host_names()` → `stokes_agent.probe.wsl_distros()` →
`subprocess.run(["wsl.exe", "-l", "-q"], capture_output=True, timeout=15)` → `communicate` → joining
the pipe reader threads. The mesh panel asks for `host.list` as soon as the client connects
(`web/src/panels/meshRun.ts loadMeshContext`), the RPC dispatcher runs commands synchronously on the
loop, so `node.tree` and `project.info` (and every new WebSocket) waited behind it; the client's
reads came back fine afterwards (the `[stokes] tree read` breadcrumbs all said `project: pipe4`),
which is why neither the replay nor the retry helped - the reads were never refused, only late.

Measured: started from a process whose stdin is a console (the terminal, or `python -m
stokes.server` from a shell), `wsl.exe -l -q` does not exit until the timeout kills it; with
`stdin=DEVNULL` it answers in 0.2 s. And on Windows `subprocess.run` answers a timeout by killing
the child and calling `communicate()` AGAIN, which joins the readers until every holder of the pipe
is gone - about 50 s in all here, which is the stall seen on every first connect.

Fix (one commit after this note): `wsl_distros()` uses `Popen` with `stdin=DEVNULL`,
`CREATE_NO_WINDOW`, a 5 s timeout, kills and forgets a child that does not answer; `HostRegistry`
asks for the WSL names once per registry (`_wsl_names`, cleared by `refresh_hosts`). Three tests in
`tests/test_wsl_runner.py` pin the call shape, the timeout path and the cache. Re-driven on
`pipe4.stokes` with the server started with `--project`: the project is on screen 0.4 s after the
page loads. The `console.debug` breadcrumbs in `treeSync.ts` stay (silent at default levels).
`b05b268` (the `project.opened` replay) and `aa9fafd` (the one-shot retry) are harmless and stay.

Not done, worth a look: the dispatcher still runs every command on the loop, so any other command
that starts a process (`host.probe` for a host with a slow handshake, say) would stall the window
the same way. The fix for that is in `rpc.py` (dispatch such commands through `asyncio.to_thread`),
not here.

## Known gaps after today (written before the diagnosis above; the first two bullets are history now)
- Commit `b05b268` sends `project.opened` (with `replay: true`) to every newly connected client when a project is open (server `RpcHub.serve`, payload shared with `project.open/new`; the web client ignores a replay after a reconnect). The agent that made it could NOT reproduce the start-screen symptom on two projects, and I re-drove the repro at 06:50 with this build on `pipe4.stokes` (meshed, solved, saved): **still the start screen** after 10 s, server log shows only "client connected", no client console errors. So `b05b268` does not cure it. Diagnosis (06:50, code read, not yet proven): `App.tsx:220` shows the start screen while `store.project === null`; the only thing that sets it is `treeSync.ts fetchTree()` (`node.tree` + `project.info`, then `take()`), and `fetchTree` swallows the `"not connected"` rejection silently. If the connect-time fetch fires before the socket is open (likely on a fresh page load), nothing re-fetches until an event calls `scheduleRefresh()`, which matches both repros (the tree appeared only after a later `mesh pack written` event). The `project.opened` replay from `b05b268` should trigger `scheduleRefresh()` through the `/^(node|project|history)\./` test in `receive()`, unless it arrives before the store's receive hook is attached or is filtered in `api/client.ts` (the `replay && this.dropped` guard). UPDATE 06:55: the store already calls `refresh()` on the connect transition (`store.ts` ~1026), and a one-shot retry after a refused read (`aa9fafd`, treeSync `retried`) ALSO did not cure the pipe4 repro. So the reads most likely succeed and `take()` still leaves `project === null`, or the fetch never starts: instrument `fetchTree`/`take` (console.debug the `project.info` reply and the `mine/taken` tickets) on the next session; also open the app's own Console drawer during the repro (it was never checked). Earlier idea, now ruled out: in the store's connect transition (where `client` becomes connected), call `fetchTree()` explicitly, and log the `"not connected"` case in dev. Then: check the web client's start-screen condition (`App.tsx` / `StartScreen` shows while the store's project is null) against what the `project.opened` replay and the connect-time tree fetch set; log both in the browser; compare `pipe4` (fails) with the minimal project the agent used (works). Pre-existing on this tree, not from these commits: `ruff check stokes tests` reports 40 errors / 6 files to reformat in files already modified in the working tree, and `tests/test_compute_commands.py`, `test_run_store.py`, `test_stage_settings.py` have 4 failures (`test_the_shipped_default_is_AUTO`, `test_a_SYMLINK_that_is_not_a_file_is_refused`, `test_an_install_the_user_chose_is_marked_USER_ADDED`, `test_the_fields_cache_maps_the_file_and_maps_it_once`).
- **Client connect with a project already open** (original note): `python -m stokes.server --project x.stokes` then opening the URL shows the start screen ("No project") until some later event arrives (seen twice today; with pipe2 the tree appeared only after a "mesh pack written" event). The client must ask for the current project on connect (or the server must send `project.opened` to a new client). Not fixed.
- The guided Run card's "Waiting to start", the hidden-face pick list, the fluid-box mesh presets and the results chips (commit 2e16603) passed gates but were not re-driven in the browser (time).
- Guided mode flag not persisted in the workspace document (schema).
- Orbit/camera helpers are not in the cards; hidden faces rely on the pick-by-list row (polish) or the user orbiting.
- Mesh presets Medium/Fine on a long pipe give very large meshes; the presets should cap by an estimated cell count.
- The physics defaults (laminar, 500 iterations) do not pick a turbulence model from the Reynolds number; an inlet of 0.5 m/s water in a 60 mm pipe does not converge laminar. A "Flow model: automatic" choice in the Fluid card is the next product step.
- `imports/` does not travel on Save as (known); the demo STEP sits outside the project folder.
- The old Next-step chip and the stage chips still show beside the strip; decide whether the strip replaces them in guided mode. (Decided and done in the loop pass below: the strip replaces them whenever a project is open.)

## Loop pass, 2026-11-01 late morning (owner: "keep going through the app, fixing and improving the GUI on a loop")

Driven in the browser on a fresh project (`gui1`: demo pipe imported, nothing else), end to end,
with a real FoamWin mesh (63,504 cells, 1 min 27 s) and solve (converged in 323 iterations at
0.02 m/s). Commits on `windows`, in order:

- `93b7ca9` fix(hosts): the start-screen-on-connect bug (see the section above).
- `0e666e0` fix(web): **the Boundaries card blanked the whole window** (React #185) on any project
  with no face picked — a selector making a fresh `[]`; one shared empty array. Card notes are a
  few words with the sentence in a tooltip (`Note tip=`), per the owner's rule "no long sentences
  in the GUI, leave them for tooltips"; strip hints reworded ("fluid is the fluid", "Pick the fluid
  in fluid").
- `81cbee3` fix(server): **events raised on the loop between commands were held until the next
  request** — the Mesh card said "Generating… checkMesh" for two minutes after the mesh was done.
  `RpcHub._collect` now holds an event back only while a command executes.
- `0613047` fix(agent): the default slice plane's normal is the mesh's shortest axis (a pipe is cut
  lengthwise, not into a disc).
- `bb51be7` fix(web): the strip replaces the stage chips and the Next-step button whenever a project
  is open (both modes); switching to Full unfolds the left dock (it came back folded, two icons and
  no tree).

**First connect, solved (19:20, e3ffb26):** the 10-40 s Start screen on a `--project` server was
the first `host.probe` (FoamWin's PowerShell handshake) running inside the dispatcher on the loop;
the server now probes the local host on a thread as soon as it listens. Measured on pipe3: the
project is on screen 4.2 s after navigation (was 30-40 s, with the socket dropping as stalled).

**Windows path length (260) bites twice:** `case.import` under a deep folder fails (sweep
finding 1), and a slice cut at a chosen plane on `pipe3.stokes` under the session scratchpad
(~230-character project path) is cut on the host and then NOT cached — `a cut could not be
cached … ENOENT …\cache\geometry\results\slices\<run>\<hash>.stkslice.part` — so the route
answers 404 and the Looks card's Slice shows nothing on an axis change. Users with deep OneDrive
or Documents paths will hit this. Fix to make: either open every project path in the `\\?\`
extended form on Windows, or warn at `project.new`/`open` when the folder is deeper than ~150
characters. Not done; the verification projects were moved to `C:\Users\<user>\FoamWin\proj\`.

**Suspect data loss to chase:** `pipe4.stokes/project.json` was rewritten at 06:10:35 as a
1.8 kB project with an EMPTY geometry group (its `runs/` and `cache/` folders still hold the
mesh and the converged run; `pipe2`/`pipe3`, saved 05:25/05:28, still have their 4 parts). That
is during the previous session's start-screen repro drives, so a save made while the server had
not (yet) loaded the project's geometry — or a `project.new` into the same folder — wrote an empty
tree over a good one. Find which, and make a save refuse to write a project with fewer parts than
the file it replaces unless the user asked.

Seen and not yet done: the first click on a Recent row after a page load only focuses it (second
click opens); the Δp readout should say Pa when the density is known (plan: RESULTS-OVERHAUL §3.3);
a tutorial case project in Guided mode shows "Drop a CAD file here" (TUTORIAL-SWEEP finding 3);
`run.status` says `done_unconverged` for a case that reached its endTime (finding 2); the server
reads `web/dist/index.html` once, so a web rebuild needs a server restart.
