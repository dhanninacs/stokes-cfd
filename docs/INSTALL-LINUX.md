# Installing Stokes on Linux

Stokes runs natively on x86-64 Linux. It ships no engine: it drives an existing
**OpenFOAM Foundation 14** install as separate processes (nothing links to
OpenFOAM or cfMesh). Ubuntu 22.04, 24.04 and 26.04 are the reference systems;
other distributions work if they provide OpenFOAM 14 and Python 3.13 or newer.

There is no installer for Stokes yet. You run it from a checkout, which takes
about fifteen minutes plus the cfMesh build.

Use the **`linux`** branch. It holds everything on `windows` plus the
Linux engine discovery, OpenMPI handling and the software-rendering switch. The
only end-to-end Linux run recorded so far is in
[LINUX-READINESS.md](LINUX-READINESS.md); read it for what is verified.

> New to this? Do steps 1 to 6 in order, then open [USAGE.md](USAGE.md).

## Requirements

| What | Version | Why |
|---|---|---|
| Linux | x86-64, glibc 2.35 or newer (Ubuntu 22.04+) | |
| Python | 3.13 or 3.14 | the application and the agent |
| Node.js | 22.12 or newer (24 recommended) | builds the front end once |
| Git | any recent | to get the source |
| OpenFOAM | Foundation **14** | meshing and solving |
| MPI | OpenMPI (comes with OpenFOAM) | runs on more than one core |
| cfMesh | the OpenFOAM 13 port, built against 14 (step 3) | tet, polyhedral and hex-dominant meshes; snappyHexMesh and blockMesh do not need it |
| Display | X11 or Wayland session | the window; `--browser` works without a Qt display |
| Graphics | GPU with WebGL 2, or software rendering (step 5) | the 3D view |
| Memory and disk | 8 GB RAM minimum, 16 GB or more for meshes over a million cells; 5 GB free | |

## 1. System packages

Ubuntu or Debian:

```bash
sudo apt-get update
sudo apt-get install -y git build-essential libxcb-cursor0 libegl1 libgl1 \
    libxkbcommon-x11-0 libnss3 libxcomposite1 libxdamage1 libxrandr2 libxtst6
```

`libxcb-cursor0` is required on X11: without it Qt stops with
`Could not load the Qt platform plugin "xcb"`. The others are the libraries the
Qt WebEngine window usually needs; if the window fails with another missing
library, install the package the error names. Wayland sessions do not need
`libxcb-cursor0`.

**Python 3.13 or newer.** Ubuntu 24.04 ships 3.12, so use the deadsnakes
repository or `uv`:

```bash
sudo add-apt-repository -y ppa:deadsnakes/ppa
sudo apt-get install -y python3.13 python3.13-venv
```

or

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh     # then: uv venv --python 3.13 .venv
```

**Node.js 22.12 or newer**, for example with nvm:

```bash
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh | bash
# open a new shell, then:
nvm install 24
```

## 2. OpenFOAM Foundation 14

Ubuntu has an official package repository:

```bash
sudo sh -c "wget -qO - https://dl.openfoam.org/gpg.key > /etc/apt/trusted.gpg.d/openfoam.asc"
sudo add-apt-repository -y "http://dl.openfoam.org/ubuntu main"
sudo apt-get update
sudo apt-get install -y openfoam14
```

It installs to `/opt/openfoam14` and brings OpenMPI. A user-space source build
also works; then set `STOKES_OPENFOAM_BASHRC` (step 4).

Check it:

```bash
source /opt/openfoam14/etc/bashrc
blockMesh -help | head -3
```

## 3. cfMesh (optional, for tet, polyhedral and hex-dominant meshes)

**Do not use cfMesh 1.2.0 with OpenFOAM 14.** It compiles, but its octree mapper
fails with `Point ... is not inside the initial cube` and the meshes come out
with inverted cells. Use the Foundation-13 port, pinned to the commit that was
tested:

```bash
git clone https://github.com/Ultravis66/cfMesh-OpenFOAM13.git ~/src/cfmesh
git -C ~/src/cfmesh checkout 2e36fe7359dd8bbe76ca126c008f884a40ff38fc
source /opt/openfoam14/etc/bashrc
cd ~/src/cfmesh && ./Allwmake -j "$(nproc)"
```

Against OpenFOAM 14 the build needed two small source fixes, which the
maintainer applied by script (the script is not published yet):

- add `#include <vector>` to `boundaryLayerCells.C`;
- where the compiler rejects `maxOp<labelList>()` (OpenFOAM 14 has no
  `max(List, List)`), reduce element by element instead.

Apply them if your build stops on those errors. cfMesh installs into
`$FOAM_USER_APPBIN` and `$FOAM_USER_LIBBIN`, which OpenFOAM's `bashrc` puts on
your path. Check:

```bash
source /opt/openfoam14/etc/bashrc
cartesianMesh -help | head -3 && pMesh -help | head -3 && tetMesh -help | head -3
```

## 4. Get Stokes and build it

```bash
git clone -b linux https://github.com/dhanninacs/stokes-cfd.git
cd stokes-cfd
python3.13 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -e ".[agent,server,ui,cad,extract,explore,materials]"
cd web && npm install && npm run build && cd ..
.venv/bin/stokes --help
```

The extras are: `agent` (the job agent), `server` (the local web server), `ui`
(PySide6, the window), `cad` (Open CASCADE, STEP import), `extract` (VTK, result
fields), `explore` (design studies) and `materials` (CoolProp and Cantera).

Stokes finds OpenFOAM, in this order:

1. `STOKES_OPENFOAM_BASHRC`, the absolute path of an env script that exports the
   MPI paths (the Linux counterpart of `STOKES_FOAMWIN_ENV` on Windows);
2. `$WM_PROJECT_DIR` or `$FOAM_INST_DIR`;
3. `which foamRun`, so a shell that already sourced `etc/bashrc` needs nothing
   more;
4. the usual places: `/opt`, `/usr/lib/openfoam` and `$HOME`.

The simplest setup is to source OpenFOAM in the shell you launch Stokes from.

## 5. Run Stokes

```bash
source /opt/openfoam14/etc/bashrc
.venv/bin/python -m stokes.app
```

`./run-app.sh` does the same from the checkout (and adds software-rendering flags
for machines without a GPU, see below).

| Option | Effect |
|---|---|
| `--project PATH` | open a project directly |
| `--theme dark` | `dark`, `light`, `cupertino`, `cupertino-dark`, `ubuntu`, `ubuntu-dark`, `auto` |
| `--browser` | no Qt window; opens the same page in your default browser |
| `--software-webgl` | draw the 3D view in software, for a VM, a remote desktop or a blocklisted driver (also `STOKES_SOFTWARE_WEBGL=1`) |

On a machine without a usable GPU, Chromium may disable WebGL 2. The window then
says "3D view unavailable on this display" while every panel, command and result
still works. Start with `--software-webgl` to get the 3D view; it costs frame
rate on a real GPU, which is why it is not the default.

A headless or remote machine can serve the page instead:

```bash
.venv/bin/python -m stokes.server --port 8740     # prints a URL with a token
```

It listens on `127.0.0.1` only. Reach it from another machine through an SSH
tunnel: `ssh -L 8740:127.0.0.1:8740 user@host`.

## 6. Check it works

In the window, create a project, import a STEP file (**Geometry → Import**), tag
an inlet and an outlet, and press **Mesh**. Then follow [USAGE.md](USAGE.md).

Without the window:

```bash
.venv/bin/stokes new demo --name demo
.venv/bin/stokes param demo U_in velocity "2 [m/s]"
.venv/bin/stokes tree demo
```

## MPI

Runs on more than one core use OpenMPI's launcher. Stokes adds `--oversubscribe`
when the install identifies OpenMPI, because OpenMPI counts physical cores and a
four-thread VM would otherwise refuse four ranks. MPICH and MS-MPI are not given
that flag.

## Where things are stored

| What | Where | Change it with |
|---|---|---|
| Projects (`.stokes` folders) | `~/Documents/Stokes/Projects` (or `~/Stokes/Projects`) | `STOKES_USER_HOME` |
| Settings, logs, workspace layout | `~/.stokes` | `STOKES_HOME` |
| Job working folders (mesh and run data) | `~/.stokes/jobs` | `STOKES_AGENT_HOME` |

The app log is `~/.stokes/logs/app.log`; look there first when something fails.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `Could not load the Qt platform plugin "xcb"` | `sudo apt-get install libxcb-cursor0`. |
| "3D view unavailable on this display" | Start with `--software-webgl`, or fix the GPU driver. |
| Mesh or Run says no engine | OpenFOAM is not found. `source /opt/openfoam14/etc/bashrc` in the launching shell, or set `STOKES_OPENFOAM_BASHRC`. |
| cfMesh meshes have inverted cells or "Could not find a boundary region" | You built cfMesh 1.2.0. Rebuild with the port in step 3. |
| `cartesianMesh: command not found` | cfMesh is not built or its bin folder is not on the path; re-source `etc/bashrc`. |
| Four ranks refused on a small VM | The OpenMPI `--oversubscribe` flag is only added when OpenMPI is identified; set `STOKES_OPENFOAM_BASHRC` to the install's env script. |
| `npm run build` fails on engine version | Install Node.js 22.12 or newer. |
| `pip install` says the Python is too old | Stokes needs 3.13 or 3.14 (step 1). |
| HiDPI or fractional scaling looks wrong on Wayland | See the Wayland notes in [LINUX-READINESS.md](LINUX-READINESS.md). |

## Updating

```bash
git pull
.venv/bin/pip install -e ".[agent,server,ui,cad,extract,explore,materials]"
cd web && npm install && npm run build && cd ..
```

Questions and bug reports: open an issue on GitHub. If Stokes is useful to you,
you can [buy me a coffee](https://buymeacoffee.com/csdhan).
