# Installing Stokes on Linux

Stokes ships no engine. On Linux it drives an existing OpenFOAM Foundation 14 install as separate
processes (ADR-0001); nothing links to OpenFOAM or cfMesh. This is the quick-start that
`docs/LINUX-READINESS.md` refers to.

## 1. OpenFOAM Foundation 14

Ubuntu 22.04/24.04/26.04 have an official package repository:

```bash
sudo sh -c "wget -qO - https://dl.openfoam.org/gpg.key > /etc/apt/trusted.gpg.d/openfoam.asc"
sudo add-apt-repository -y "http://dl.openfoam.org/ubuntu main"
sudo apt-get update
sudo apt-get install -y openfoam14
```

The package lands in `/opt/openfoam14`. A user-space source build works too; point
`STOKES_OPENFOAM_BASHRC` at the env script that exports the MPI paths.

## 2. MPI

The OpenFOAM package brings OpenMPI. `cores > 1` uses OpenMPI's launcher; Stokes adds
`--oversubscribe` when the install's environment identifies OpenMPI, because OpenMPI counts physical
cores and a 4-thread VM otherwise refuses four ranks. MS-MPI and MPICH are not passed that flag.

## 3. cfMesh

**Do not use cfMesh 1.2.0 against modern OpenFOAM.** It compiles but its octree mapper throws
`Point ... is not inside the initial cube`; the meshes have inverted cells and thousands of
"Could not find a boundary region" warnings. The Foundation-13 port
[`Ultravis66/cfMesh-OpenFOAM13`](https://github.com/Ultravis66/cfMesh-OpenFOAM13) builds against 14
with two small fixes, which `.github/scripts/patch-cfmesh.py` applies:

```bash
git clone https://github.com/Ultravis66/cfMesh-OpenFOAM13.git ~/src/cfmesh
git -C ~/src/cfmesh checkout 2e36fe7359dd8bbe76ca126c008f884a40ff38fc
python3 .github/scripts/patch-cfmesh.py ~/src/cfmesh
source /opt/openfoam14/etc/bashrc
cd ~/src/cfmesh && ./Allwmake -j "$(nproc)"
```

`Allwmake` installs into `$FOAM_USER_APPBIN` and `$FOAM_USER_LIBBIN`, which OpenFOAM's `bashrc` puts
on `PATH` and `LD_LIBRARY_PATH`. Check with:

```bash
source /opt/openfoam14/etc/bashrc
cartesianMesh -help && pMesh -help && tetMesh -help
```

## 4. Stokes

```bash
python3.13 -m venv ~/.venvs/stokes
~/.venvs/stokes/bin/pip install -e ".[dev,agent]"
cd web && npm install && npm run build && cd ..
```

The engine probe finds an install from, in order:

1. `STOKES_OPENFOAM_BASHRC` — an absolute env script (the Linux twin of `STOKES_FOAMWIN_ENV`);
2. `$WM_PROJECT_DIR` / `$FOAM_INST_DIR` (the install, or the parent of installs);
3. `which foamRun` — a shell that already sourced `etc/bashrc` needs nothing else;
4. the standard `/opt`, `/usr/lib/openfoam` and `$HOME` globs.

The capture keeps `PATH`, `LD_LIBRARY_PATH`, `USER` and the OpenMPI prefixes, because `etc/bashrc`
assumes a sourced shell and builds `FOAM_USER_APPBIN` from `$USER`.

Run it:

```bash
source /opt/openfoam14/etc/bashrc
python -m stokes.server --port 8740        # prints the URL with its token
python -m stokes.app                       # desktop window
```

On a machine without a usable GPU, QtWebEngine may blocklist WebGL 2; the window then says "3D view
unavailable on this display" and every panel, command and result still works. To get the 3D view on
such a machine (a VM, a remote desktop, a blocklisted driver), start the app with
`--software-webgl` (or set `STOKES_SOFTWARE_WEBGL=1`): it runs WebGL on SwiftShader with software
compositing. It costs frames on a real GPU, which is why it is not the default.

## 5. Desktop dependencies

The Qt X11 plugin needs `libxcb-cursor0` (Ubuntu package `libxcb-cursor0`) in addition to the
libraries PySide6 brings. Without it `QT_QPA_PLATFORM=xcb` fails with
`Could not load the Qt platform plugin "xcb"`. Wayland does not need it.
