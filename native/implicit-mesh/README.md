# stokes-implicit-mesh

The mesher for implicit parts (ADR-0035, section E). It reads one body written in Fidget's text
format, evaluates it with Fidget's interpreter on an octree inside a cube, runs Manifold Dual
Contouring, welds vertices at the same f32 position, checks every edge, removes folded triangles,
optionally simplifies flat regions without moving the surface, and writes one binary mesh file.

It is a **separate Rust program**, not a `stokes.native` kernel: it has no pure-Python fallback,
it must be killable, and a crash or a runaway evaluation must not take the server down. The
geometry worker runs it as a child process (argv list, explicit `cwd`, minimal environment,
timeout, kill-on-close Job Object on Windows). Without the binary the implicit tree still edits,
validates and evaluates; meshing raises `implicit.mesher_missing`.

## Layout

```
Cargo.toml            our package; its own [workspace], fidget-mesh patched from vendor/
Cargo.lock            committed; builds use --locked
.cargo/config.toml    linker settings for the x86_64-pc-windows-gnullvm target only
src/main.rs           command line, pipeline, progress, statistics          (proprietary)
src/meshops.rs        weld, edge check, fold count, simplification, writer  (proprietary)
src/unfold.rs         fold removal (ADR-0035 decision 18)                   (proprietary)
vendor/fidget-mesh/   Fidget 0.5.1's mesher with marked changes             (MPL-2.0)
bin/                  where the built binary goes (gitignored)
```

## Build

Rust 1.95 or newer. Fidget is built with `default-features = false, features = ["mesh"]`: the
interpreter only, no JIT (ADR-0035 decision 15).

Build from inside `native/implicit-mesh`: cargo reads `.cargo/config.toml` from the folder it runs
in, and without it a Windows gnullvm build looks for a MinGW linker. `Cargo.lock` is committed and
every build uses `--locked`. It was regenerated on 2026-11-11 for
the interpreter-only build (no `fidget-jit`, `dynasm` or `dynasmrt`), and first built and tested
then with Rust 1.99.1 on Windows (`x86_64-pc-windows-gnullvm`). A Windows machine without Rust
gets the toolchain with the official installer from rustup.rs:
`rustup-init.exe -y --profile minimal --default-host x86_64-pc-windows-gnullvm`.

Linux:

```bash
cd native/implicit-mesh
cargo build --release --locked
mkdir -p bin && cp target/release/stokes-implicit-mesh bin/
cargo test --release --locked
```

Windows without the Microsoft compiler (the LLVM-based toolchain and its bundled linker; the
settings in `.cargo/config.toml` apply to this target only):

```powershell
rustup toolchain install stable-x86_64-pc-windows-gnullvm
cd native\implicit-mesh
cargo +stable-x86_64-pc-windows-gnullvm build --release --locked
New-Item -ItemType Directory -Force bin
Copy-Item target\release\stokes-implicit-mesh.exe bin\
# the binary needs the toolchain's unwinder beside it
Copy-Item "$(rustc +stable-x86_64-pc-windows-gnullvm --print sysroot)\lib\rustlib\x86_64-pc-windows-gnullvm\bin\libunwind.dll" bin\
```

(If `libunwind.dll` is not at that path in your toolchain, search the sysroot for it.)

The application finds the binary in `native/implicit-mesh/bin/` beside the package, or wherever
the environment variable `STOKES_IMPLICIT_MESHER` points (development). The binary's SHA-256 is
part of every cache key, so a rebuild invalidates cached surfaces.

## Command line

```
stokes-implicit-mesh --model FILE --centre X Y Z --half H --depth D --out FILE
                     [--simplify TOL] [--angle DEG] [--threads N] [--progress]
stokes-implicit-mesh --version
```

| Flag | Meaning |
|---|---|
| `--model FILE` | the body in Fidget's text format, negative inside; at most 64 MiB |
| `--centre X Y Z`, `--half H` | the meshing cube, `centre ± half`, model units |
| `--depth D` | octree depth, 1 to 15; the finest cell is `2 H / 2^D` |
| `--out FILE` | the mesh file; written to `FILE.part` and renamed when complete |
| `--simplify TOL` | lossless planar simplification with `TOL` (model units) the largest distance from the unsimplified surface; 0 or absent = off |
| `--angle DEG` | the largest normal change a collapse may make, below 10°; default derived from `TOL` and the cell |
| `--threads N` | worker threads for the whole program; 0 or absent = all cores |
| `--progress` | progress lines on stdout |

Numbers must be finite. A bad argument exits with code 2, a depth above 15 also with code 2 and
the code `implicit.too_fine`; any other failure exits with code 1. In both cases the last stdout
line is `{"error": "<code>", "message": "<text>"}`. Codes: `implicit.bad_argument`,
`implicit.too_fine`, `implicit.model_unreadable`, `implicit.model_too_large`,
`implicit.model_invalid`, `implicit.write_failed`, `implicit.cancelled`, `implicit.internal`.

The runner moves the cube off round coordinates by a fixed irrational fraction of the finest cell
(decision 19); the program meshes exactly the cube it is given.

## Stdout

JSON lines only. With `--progress`, at most five lines a second in all:

```json
{"stage":"build","done":1234,"total":56789}
```

Stages, in order: `build` (octree), `walk` (dual contouring), `weld`, `check` (edges and folds,
then fold removal),
`simplify` (only with `--simplify`), `write`. Units of `done`/`total` differ by stage; only the
ratio means anything.

The last line on success is the statistics:

```json
{"stats":{"vertices":0,"triangles":0,"welded":0,"separated":0,"collapsed":0,"pairs_dropped":0,
 "open_edges":0,"nonmanifold_edges":0,"misoriented_edges":0,"folds":0,"folds_raw":0,
 "unfold_removed":0,"unfold_flips":0,"unfold_moves":0,"unfold_collapses":0,"simplified":false,
 "simplify_collapses":0,"cell":0.03125,"threads":16,"version":"0.1.0","seconds":1.234}}
```

(one line in the output). `welded`: vertices merged into another at the same position;
`separated`: vertices pulled 0.002 cell apart instead, where two sheets touch along a line;
`collapsed`, `pairs_dropped`: triangles the weld made degenerate, and coincident opposite
pairs it removed; `open_edges`, `nonmanifold_edges`, `misoriented_edges`: the edge check of the
written mesh (all zero for a closed, consistently oriented surface); `folds`: edges whose two
triangles' normals are more than 170° apart, left after fold removal (decision 18; zero on every
body measured); `folds_raw`: the same count before it; `unfold_removed`, `unfold_flips`,
`unfold_moves`, `unfold_collapses`: what fold removal did (see below);
`simplified`: whether the simplified mesh was kept (it is discarded if the edge check, the fold
count or the bounding box got worse).

## Fold removal

Fidget's dual contouring meshes every cell edge the surface crosses as a fan of four triangles
around the crossing point. Where a cell's vertex lands beside or beyond that point the fan folds:
two triangles lie on each other facing opposite ways. Measured on 2026-11-11: about a tenth of a
sphere's triangles, and flaps along every sharp edge of a box. `src/unfold.rs` removes them, in
deterministic rounds, after the edge check:

1. a vertex of valence 3 to 6 with a fold around it is removed and its ring refilled by the best
   fan (the crossing point of a folded fan, almost always);
2. a folded edge is flipped;
3. when those find nothing more, a fallback stage also refills rings of up to 24, collapses a short
   folded edge into its midpoint projected onto the surface, and moves a vertex to the projected
   centre of its ring.

Each change must lower the folds among the triangles it touches and their neighbours (a removal or
collapse in the fallback may leave as many, or one more for a collapse, since it deletes a vertex
and the stage cannot cycle). The result is kept only if the mesh is still closed and manifold and
has fewer folds. On 36 synthetic bodies at four cell sizes it removed all of 198,870 folds; a sphere
at 0.05 mm cells, 35,311. It costs about 10 to 40 % of the meshing time.

## Mesh file

Little-endian, ADR-0035 decision 14:

| Bytes | Content |
|---|---|
| 8 | magic `STKIMSH1` |
| 4 | u32 vertex count `V` |
| 4 | u32 triangle count `T` |
| 12 V | f32 x, y, z per vertex |
| 12 T | u32 a, b, c per triangle, counter-clockwise seen from outside |
| 4 T | u32 face label per triangle |

Labels are all 0 in this slice; slice 2 fills them (decision 11). The same input gives the same
bytes at any thread count. The reader checks the magic, both counts against the file size, a cap
of 64 M triangles, every index against the vertex count and every position for finiteness.

## Licences

`Cargo.toml`, `src/` and this README are proprietary, part of Stokes
(`LicenseRef-Stokes-Proprietary`). `vendor/fidget-mesh/` is Fidget's mesher under the Mozilla
Public License 2.0, changed as listed in `vendor/fidget-mesh/CHANGES-STOKES.md`; the other Fidget
crates are used unchanged from crates.io. MPL-2.0 is a file-level copyleft: the binary links the
MPL files statically, and **their source (this `vendor/` directory) ships inside every
installer**, so the obligation does not depend on a public repository (ADR-0035 decision 32).
Crate licences are audited from `cargo metadata --locked` (decision 33).
