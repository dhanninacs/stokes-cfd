# Installing Stokes on Windows

Stokes runs natively on Windows 10 and 11 (x64). It does not ship an engine: it
drives an OpenFOAM Foundation 14 install as separate processes. On Windows that
install is **FoamWin**, a native build of OpenFOAM 14 with MS-MPI, from
<https://dhanninacs.github.io/foamWin/>. WSL is not required.

There is no installer for Stokes yet. You run it from a checkout, which takes
about ten minutes.

> New to this? Do steps 1 to 6 in order, then open [USAGE.md](USAGE.md).

## Requirements

| What | Version | Why |
|---|---|---|
| Windows | 10 or 11, 64-bit | |
| Python | 3.13 or 3.14 (64-bit) | the application and the agent |
| Node.js | 22.12 or newer (24 recommended) | builds the front end once |
| Git | any recent | to get the source |
| FoamWin (OpenFOAM 14) | see [step 3](#3-install-the-engine-foamwin) | meshing and solving |
| Microsoft MPI | 10.1 or newer | only for runs on more than one core |
| Graphics | any GPU with WebGL 2 | the 3D view; current drivers recommended |
| Memory and disk | 8 GB RAM minimum, 16 GB or more for meshes over a million cells; 5 GB free | |

You do not need administrator rights for Stokes or FoamWin. Installing Python,
Node or MS-MPI may ask for them.

## 1. Get the source

Open PowerShell:

```powershell
git clone -b windows https://github.com/dhanninacs/stokes-cfd.git
cd stokes-cfd
```

Keep the checkout on a **local drive**, in a short path such as `C:\code\stokes`.
Network shares are many times slower and Git can refuse them; Stokes warns when a
project path is longer than 180 characters because OpenFOAM case files can hit
Windows' 260-character limit.

## 2. Python environment and the front end

```powershell
py -3.14 -m venv .venv          # or py -3.13
.\.venv\Scripts\python -m pip install --upgrade pip
.\.venv\Scripts\python -m pip install -e ".[agent,server,ui,cad,extract,explore,materials]"
cd web
npm install
npm run build
cd ..
```

The extras are: `agent` (the job agent), `server` (the local web server), `ui`
(PySide6, the window), `cad` (Open CASCADE, STEP import), `extract` (VTK, result
fields), `explore` (design studies) and `materials` (CoolProp and Cantera). The
first install downloads a few hundred megabytes, mostly Qt and VTK.

`npm run build` writes `web/dist`, which the app serves. Run it again after you
pull new commits.

Check it:

```powershell
.\.venv\Scripts\stokes --help
```

## 3. Install the engine (FoamWin)

Stokes needs OpenFOAM 14 to mesh and solve. You can open projects, import CAD and
view results without it.

1. Open the FoamWin releases, <https://github.com/dhanninacs/foamWin/releases/latest>
   (v14.0.0-win.5 or newer), and download `OF14-win-setup.exe` (a GUI installer,
   about 86 MB) or `OF14-win.zip` (portable, about 131 MB), plus `SHA256SUMS.txt`
   to check the download. That release includes cfMesh and the Scotch and METIS
   partitioners. If its README differs from this page, follow its README.
2. Run `OF14-win-setup.exe`, or from the extracted ZIP run
   `powershell -ExecutionPolicy Bypass -File .\setup-openfoam14.ps1`. The default
   location is `%LOCALAPPDATA%\OpenFOAM-14-win`; `-InstallDir` changes it.
3. Check it, from the install folder:

   ```powershell
   powershell -ExecutionPolicy Bypass -File .\of14-smoke-test.ps1
   ```

   It runs a small cavity case with `blockMesh`, `checkMesh` and `icoFoam`.
4. For parallel runs install **Microsoft MPI** (`msmpisetup.exe` from Microsoft's
   download page for MS-MPI). Without it Stokes runs on one core and says so;
   asking for more cores without MS-MPI fails with a clear message rather than
   falling back silently.

## 4. Point Stokes at the engine

Stokes finds FoamWin through one environment variable: the path of the
`of14-env.ps1` script in the FoamWin install folder.

For the current PowerShell window:

```powershell
$env:STOKES_FOAMWIN_ENV = "$env:LOCALAPPDATA\OpenFOAM-14-win\of14-env.ps1"
```

To keep it for every new window:

```powershell
[Environment]::SetEnvironmentVariable("STOKES_FOAMWIN_ENV", "$env:LOCALAPPDATA\OpenFOAM-14-win\of14-env.ps1", "User")
```

Use your own path if you chose another install folder.

## 5. Run Stokes

From the `stokes` folder:

```powershell
.\.venv\Scripts\python -m stokes.app
```

The window opens on the start screen: **New project**, **Open**, recent projects,
examples and tutorials. Useful options:

| Option | Effect |
|---|---|
| `--project PATH` | open a project directly |
| `--theme dark` | `dark`, `light`, `cupertino`, `cupertino-dark`, `ubuntu`, `ubuntu-dark`, `auto` |
| `--browser` | no Qt window; opens the same page in your default browser |
| `--no-frameless` | use the normal Windows title bar |

To use the browser instead of the window, or from another tool:

```powershell
.\.venv\Scripts\python -m stokes.server --port 8740
```

It prints a URL with a one-time token. The server listens on `127.0.0.1` only.

### A one-click launcher (optional)

Save this as `run-stokes.ps1` next to `.venv` and make a shortcut to
`powershell -ExecutionPolicy Bypass -File run-stokes.ps1`:

```powershell
$env:STOKES_FOAMWIN_ENV = "$env:LOCALAPPDATA\OpenFOAM-14-win\of14-env.ps1"
Set-Location $PSScriptRoot
& .\.venv\Scripts\python.exe -m stokes.app
```

## 6. Check it works

In the window, create a project, import a STEP file (**Geometry → Import**),
tag a face as an inlet and one as an outlet, and press **Mesh**. A small part
meshes in under a minute. Then follow
[USAGE.md](USAGE.md) for the full case.

From a terminal, without the window:

```powershell
.\.venv\Scripts\stokes new demo --name demo
.\.venv\Scripts\stokes param demo U_in velocity "2 [m/s]"
.\.venv\Scripts\stokes tree demo
```

## Where things are stored

| What | Where | Change it with |
|---|---|---|
| Projects (`.stokes` folders) | `Documents\Stokes\Projects` | `STOKES_USER_HOME` |
| Settings, logs, workspace layout | `%USERPROFILE%\.stokes` | `STOKES_HOME` |
| Job working folders (mesh and run data) | `%USERPROFILE%\.stokes\jobs` | `STOKES_AGENT_HOME` |

The app log is `.stokes\logs\app.log`; look there first when something fails.

## Troubleshooting

| Symptom | Fix |
|---|---|
| "running scripts is disabled" | Run PowerShell commands as shown, with `-ExecutionPolicy Bypass`, or `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`. |
| Mesh or Run says no engine, or the engine is not found | `STOKES_FOAMWIN_ENV` is unset or wrong. Check `Test-Path $env:STOKES_FOAMWIN_ENV`, then reopen the window from the same PowerShell session. |
| "Parallel mode requires the MPI-enabled FoamWin build and Microsoft MPI" | Install MS-MPI (step 3), or set cores to 1. |
| 3D view is blank or says it is unavailable | Update the graphics driver. On a virtual machine or remote desktop try `--browser`. |
| `npm run build` fails on engine version | Install Node.js 22.12 or newer (`node --version`). |
| `pip install` fails building a package | Use Python 3.13 or 3.14 64-bit; 3.12 and 32-bit builds are not supported. |
| The page says the build is missing or old | Run `npm run build` in `web`. |
| Very slow imports or Git errors | The checkout is on a network share. Clone to a local drive. |
| A path-length warning when opening a project | Move the project to a shorter folder, for example `C:\cfd\elbow.stokes`. |

## Updating

```powershell
git pull
.\.venv\Scripts\python -m pip install -e ".[agent,server,ui,cad,extract,explore,materials]"
cd web; npm install; npm run build; cd ..
```

## Uninstalling

Delete the checkout and `%USERPROFILE%\.stokes`. Your projects in
`Documents\Stokes\Projects` are yours; keep or delete them. Remove FoamWin with its own
uninstaller or by deleting its install folder.

Questions and bug reports: open an issue on GitHub. If Stokes is useful to you,
you can [buy me a coffee](https://buymeacoffee.com/csdhan).
