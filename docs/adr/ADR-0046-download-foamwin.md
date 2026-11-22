# ADR-0046 · Download and link FoamWin for a Windows user with no engine

**Status:** accepted · 2026-11-21 · (ADR-0024 hosts and installs, ADR-0025 native Windows)

## Context

A Windows user who downloads Stokes from GitHub and has no FoamWin, no WSL with OpenFOAM 14 and no
remote host can open projects and import CAD, but cannot mesh or solve. Getting an engine took five
manual steps from `docs/INSTALL-WINDOWS.md`: find the FoamWin release, download it, check its
SHA-256, run its setup and, outside the default folder, set `STOKES_FOAMWIN_ENV`. Stokes already
finds FoamWin by itself in its default folder, `%LOCALAPPDATA%\OpenFOAM-14-win`
(`stokes_agent.native_env.candidates`), and the FoamWin release ships a portable `OF14-win.zip`
whose `setup-openfoam14.ps1` installs there without administrator rights. GitHub publishes a
SHA-256 digest for every release asset.

## Decision

1. **Stokes can download and install FoamWin itself** (`stokes.engines.foamwin_install`): read the
   latest release from a fixed URL (`api.github.com/repos/dhanninacs/foamWin/releases/latest`),
   stream `OF14-win.zip`, refuse it unless it hashes to the release's published SHA-256, unpack it
   into a temporary folder, and run the package's own `setup-openfoam14.ps1 -InstallDir
   %LOCALAPPDATA%\OpenFOAM-14-win -NoShortcut`. **Linking is the default folder**: discovery
   finds it with no setting, so nothing else is written.
2. **It is the one network call, and it only follows a user's yes.** The URL is fixed in the code
   and every request and redirect must be HTTPS to a GitHub host; nothing takes a URL from a
   caller, a page or a file. The download never starts from a probe, a scan or a project.
3. **Unpacking is checked**: no absolute paths, drive letters, `..` or links in the archive, every
   member inside the staging folder, and the unpacked size capped (4 GB). The setup script runs as
   `powershell.exe -File` with an argument list.
4. **The API**: `host.foamwin` (supported, installed, and which release a download would fetch,
   without downloading), `host.foamwin_install` (starts on a thread; `host.foamwin_progress`,
   `host.foamwin_installed`, `host.foamwin_failed` events, progress at most 10 Hz) and
   `host.foamwin_cancel`. One install at a time; an existing install is refused unless `replace`.
5. **Settings → Compute shows the offer only where it helps**: the host is this computer, the
   computer is Windows, and the probe found no supported install. The button names the size; a
   confirmation names the release, the archive, the URL and the install folder before anything is
   downloaded; progress, Cancel (until the setup step), and a rescan when it finishes.
6. **`scripts/setup-windows.ps1`** sets up a GitHub download: checks Python and Node, installs
   Stokes and builds the front end, looks for FoamWin (its folder or `STOKES_FOAMWIN_ENV`) and
   OpenFOAM 14 in WSL, offers the same FoamWin download when there is none (`-InstallFoamWin`
   answers yes), reports Microsoft MPI, and writes `run-stokes.ps1` (and a desktop shortcut with
   `-Shortcut`). It calls the same module, `python -m stokes.engines.foamwin_install`.

## Alternatives considered

| Alternative | Why not |
|---|---|
| Run FoamWin's GUI installer (`OF14-win-setup.exe`) | It is not code-signed, so SmartScreen stops it, and a silent install needs switches the NSIS script does not promise; the ZIP is the build the release was validated from. |
| Write `STOKES_FOAMWIN_ENV` after installing | Not needed: the default folder is discovered, and a user variable would outlive an uninstall. |
| Install Microsoft MPI as well | It needs administrator rights and is Microsoft's installer; Stokes says when it is missing instead. |
| Verify against `SHA256SUMS.txt` only | GitHub's per-asset digest comes in the same API answer, so there is one request less; the file is the same check. |

## Consequences

- A Windows user with nothing installed gets an engine from Settings, or from the setup script, in
  about a minute (v14.0.0-win.5: 137.5 MB; 41 s end to end on the development machine, handshake
  OpenFOAM 14 org).
- Stokes now makes one outbound request (GitHub), only on the user's yes.
- A FoamWin release must keep publishing `OF14-win.zip` with `setup-openfoam14.ps1` at its top
  level; the installer refuses a package without it.
