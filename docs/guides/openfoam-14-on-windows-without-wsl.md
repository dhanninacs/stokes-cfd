---
title: Run OpenFOAM 14 on Windows without WSL
description: How to run OpenFOAM Foundation 14 natively on Windows 10 and 11 with FoamWin, and drive it from the Stokes GUI, without WSL, a virtual machine or Docker.
---

[Stokes](https://github.com/dhanninacs/stokes-cfd) can mesh and solve OpenFOAM
cases natively on Windows. It uses **FoamWin**, a native Windows (MinGW) build of
OpenFOAM Foundation 14, instead of running Linux through WSL. This page covers
the engine part; the full install is in the
[Windows install guide](https://github.com/dhanninacs/stokes-cfd/blob/main/docs/INSTALL-WINDOWS.md).

## What you need

- Windows 10 or 11, 64-bit.
- FoamWin **v14.0.0-win.5 or newer** from
  <https://github.com/dhanninacs/foamWin/releases/latest>. That release includes
  cfMesh and the Scotch and METIS partitioners. Older builds fail on heat-transfer
  cases because they cannot load the `limitTemperature` constraint that Stokes
  writes.
- Microsoft MPI, only if you want to run on more than one core.

## 1. Install FoamWin

Download `OF14-win-setup.exe` (installer) or `OF14-win.zip` (portable) and
`SHA256SUMS.txt` from the release, and check the download against the checksum.
Install with the setup program, or from the extracted ZIP:

```powershell
powershell -ExecutionPolicy Bypass -File .\setup-openfoam14.ps1
```

The default location is `%LOCALAPPDATA%\OpenFOAM-14-win`. Check it with the
bundled smoke test, which meshes and solves a small cavity case:

```powershell
powershell -ExecutionPolicy Bypass -File .\of14-smoke-test.ps1
```

## 2. Point Stokes at FoamWin

Stokes finds FoamWin through one environment variable, the path of
`of14-env.ps1` in the FoamWin folder:

```powershell
[Environment]::SetEnvironmentVariable("STOKES_FOAMWIN_ENV", "$env:LOCALAPPDATA\OpenFOAM-14-win\of14-env.ps1", "User")
```

Open a new PowerShell window afterwards so the variable is set.

## 3. Parallel runs

Install Microsoft MPI (`msmpisetup.exe` from Microsoft's MS-MPI download page).
Then set **Cores** on the Mesh or Run card. Stokes decomposes the case, runs the
requested number of ranks and reconstructs the results. blockMesh always runs on
one core. Without MS-MPI, asking for more than one core fails with a clear
message instead of silently running on one.

## 4. Run a case

Start Stokes (`python -m stokes.app` from the checkout), create a project, import
a STEP file, set the boundaries and press **Generate** on the Mesh card, then
**Run**. The [usage tour](https://github.com/dhanninacs/stokes-cfd/blob/main/docs/USAGE.md)
walks a complete mixing-elbow case.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Mesh or Run says the engine is not found | `STOKES_FOAMWIN_ENV` is unset or wrong: check `Test-Path $env:STOKES_FOAMWIN_ENV` and reopen the window. |
| `Unknown constraint type limitTemperature` in the solver log | FoamWin is older than v14.0.0-win.5. Install the latest release. |
| "Parallel mode requires … Microsoft MPI" | Install MS-MPI, or set Cores to 1. |
| Path-length warning when opening a project | Move the project to a short local path such as `C:\cfd`. Deep case files can hit Windows' 260-character limit. |

---

OPENFOAM® is a registered trade mark of OpenCFD Limited. Stokes and FoamWin are
not approved or endorsed by OpenCFD Limited or the OpenFOAM Foundation.
