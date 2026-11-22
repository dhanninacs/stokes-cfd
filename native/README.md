# native — C++ kernels for Stokes

Built separately with nanobind (BSD-3) and scikit-build-core; the application
imports `stokes_native` when present and falls back to
`stokes/native/fallback.py` otherwise. Every kernel has a Python reference
implementation and a test that compares both.

Planned kernels (M1): id-buffer picking helpers, triangle adjacency,
feature-angle region growth, connected components, LOD decimation.

```bash
pip install ./native            # needs a C++17 compiler and CMake >= 3.26
python -c "import stokes_native; print(stokes_native.__doc__)"
```

The directory also holds `implicit-mesh/`, the mesher for implicit parts (ADR-0035): a separate
Rust program, `stokes-implicit-mesh`, with its own Cargo workspace and lockfile, that the
geometry worker runs as a child process. It is not a nanobind kernel and is not part of
`stokes_native`; its README has the build steps.

Rules for the C++ kernels (docs/engineering/coding-standards.md): buffers in (`nb::ndarray`), no
per-element allocation, release the GIL above a millisecond, no OpenFOAM
headers, no GPL sources.
