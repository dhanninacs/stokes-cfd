# Dependencies register

Every runtime or development dependency is listed here with its licence and
purpose before it is added. `tools/audit_licenses.py` checks installed Python
packages against `tools/license_allowlist.toml`; JavaScript packages are
checked by `tools/audit_js_licenses.py` against the same allow-list. Licences
marked *verified* were read from the package's own metadata on the date given.

## Python runtime, core (headless)

| Package | Version floor | Licence | Purpose | Added |
|---|---|---|---|---|
| numpy | 2.0 | BSD-3 | arrays everywhere | 2026-10-02 |
| scipy | 1.15 | BSD-3 | design-exploration numerics: `scipy.stats.qmc` samplers, `scipy.optimize` drivers, RBF interpolation, rank statistics; imported lazily and only inside `stokes.explore` (ADR-0016) | 2026-10-02 (verified) |
| pint | 0.24 | BSD-3 | quantities and unit systems | 2026-10-02 |
| lark | 1.2 | MIT | expression and dictionary grammars | 2026-10-02 |
| cryptography | 43 | Apache-2.0 OR BSD-3 | Ed25519 licence signatures | 2026-10-02 |

## Python runtime, extras

| Extra | Package | Licence | Purpose | Added |
|---|---|---|---|---|
| `server` | fastapi | MIT | WebSocket JSON-RPC server, static files | 2026-10-02 (verified) |
| `server` | uvicorn[standard] | BSD-3 | ASGI server | 2026-10-02 |
| `server` | websockets | BSD-3 | WebSocket support for uvicorn | 2026-10-02 |
| `agent` | msgpack | Apache-2.0 | agent protocol payloads | 2026-10-02 |
| `ui` | PySide6 6.11 (QtWebEngineWidgets, QtWebChannel) | LGPL-3.0; bundled Chromium under BSD and its third-party notices | desktop host window | 2026-10-02 (verified) |
| `extract` | vtk 9.6 | BSD-3 | server-side extraction, OpenFOAM reader | 2026-10-02 (verified) |
| `cad` | cadquery-ocp 8.0 | Apache-2.0 (OCCT inside: LGPL-2.1 + exception, notice required) | CAD kernel | 2026-10-02 (verified) |
| `explore` | pymoo 0.6 | Apache-2.0 | NSGA-II and NSGA-III in `stokes.explore.optimise` (ADR-0016) | 2026-10-02 (verified) |
| `explore` | SALib 1.5.1 | MIT | Sobol and Morris sensitivity in `stokes.explore.sensitivity`; 1.5.0 calls `ndarray.ptp`, which NumPy 2 removed, so the floor is 1.5.1 | 2026-10-02 (verified) |
| `explore` | scikit-learn 1.5 | BSD-3 | Gaussian-process surrogate in `stokes.explore.surrogate` | 2026-10-02 (verified) |
| `explore` (via pymoo 0.6.1.6 and later) | moocore 0.3 | **LGPL-2.1-or-later**, unmodified; a CFFI extension loaded dynamically, replaceable, C sources inside the wheel | pymoo's non-dominated sorting and hypervolume core | 2026-10-02 (verified; explicit entry in `tools/license_allowlist.toml`; **signed off 2026-10-03** under the conditions of docs/04 §4) |
| `materials` (ADR-0018 slice 2) | CoolProp 8.0 (one `cp312-abi3` wheel for Python 3.12–3.14; depends on numpy) | MIT (verified 2026-10-04 from the wheel's `LICENSE` and metadata) | property provider: HEOS fluids and mixtures, INCOMP liquids and solutions, and the REFPROP backend; imported only inside the materials worker process (`stokes.physics.materials.worker_main`), never by the API process, because it holds the GIL in every call and for seconds at import | 2026-10-04 (verified) |
| `materials` (ADR-0018 slice 2) | cantera 3.2 (wheels cp310–cp314 for win_amd64 and manylinux_2_28; depends on numpy, `ruamel.yaml`, `typing_extensions`; carries HDF5, SUNDIALS and yaml-cpp DLLs) | BSD-3-Clause (verified 2026-10-04 from the wheel's `License.txt` and `License-Expression`) | property provider: ideal-gas mixtures from YAML mechanisms with mixture-averaged transport and NASA-7 coefficients; imported only inside the materials worker process | 2026-10-04 (verified) |
| `materials` (via cantera) | ruamel.yaml 0.19 | MIT (verified 2026-10-04 from its metadata) | Cantera's YAML reader; Stokes never imports it | 2026-10-04 (verified) |
| `fast` (ADR-0047) | wgpu 0.33 (wgpu-py, with the wgpu-native library inside the wheel) | BSD-2-Clause; wgpu-native MIT or Apache-2.0 (verified 2026-11-21 from the wheel's metadata and licence file) | Fast mode: WGSL compute shaders on the desktop host's GPU through Vulkan, Direct3D 12 or Metal; imported only inside `stokes.fast.device` | 2026-11-21 (verified) |
| `fast` (via wgpu) | rendercanvas 3.0 | BSD-2-Clause (verified 2026-11-21 from its `LICENSE`) | a hard dependency of wgpu 0.33 for drawing to windows; Stokes never imports it | 2026-11-21 (verified) |
| `fast` (via wgpu) | cffi 2.1 | MIT-0 (verified 2026-11-21) | wgpu's bindings to wgpu-native; Stokes never imports it | 2026-11-21 (verified) |
| `fast` (via cffi) | pycparser 3 | BSD-3-Clause (verified 2026-11-22 from its metadata) | cffi's C parser; Stokes never imports it | 2026-11-21 (verified) |
| `mcp` (planned M3) | mcp | MIT | MCP server | planned |
| `llm` (planned M6) | anthropic, openai, google-genai, keyring | MIT, Apache-2.0, Apache-2.0, MIT | assistant providers, key storage | planned |
| `fluent` (planned M7) | ansys-fluent-core 0.42 | MIT (needs the user's licensed Fluent) | Fluent engine | planned |

The `materials` extra (ADR-0018) is optional at run time: without it the built-in catalogue
still answers, and the library providers raise `materials.provider_missing` naming the extra.
The CI installs it through `dev`. NIST REFPROP is never a dependency: it is the user's own
licensed program, found on the user's machine and reached through CoolProp's backend
(`stokes.physics.materials.providers.refprop`); a packaging test asserts that no REFPROP file
ships (`tests/test_materials_packaging.py`).

The `explore` extra (ADR-0016) is optional at run time: `stokes.explore` imports pymoo,
SALib and scikit-learn inside the functions that need them and raises
`explore.missing_dependency`, naming the extra, when one is absent. The CI installs
it through `dev`. Its transitive packages were checked by the audit on 2026-10-02 and
are all permissive: pandas, matplotlib, autograd, cma, alive-progress, Deprecated,
wrapt, joblib, threadpoolctl, narwhals, tzdata, dill, multiprocess and cloudpickle.
The last three are pickle-based libraries that pymoo and SALib pull in; Stokes code
never imports them and never enables their parallel paths.

**moocore is LGPL-2.1-or-later and arrives through pymoo.** pymoo 0.6.1.5 did not
need it; 0.6.1.6 and 0.6.2 do, and nothing newer is without it. The package is
unmodified and loads as a separate shared extension, which the allow-list policy
permits for LGPL, so the audit entry was added with the PySide6 obligations (notice and
source offer, docs/04 section 4). If the owner prefers no LGPL in the shipped tree, the
options are to ship the extra without pymoo (only NSGA-II and NSGA-III then raise the
typed error: the Gaussian process needs scikit-learn, Sobol indices fall back to a
built-in estimator and differential evolution is scipy) or to write NSGA-II in-house;
neither changes the numerics API.

## JavaScript (web/, bundled into the product as static files)

| Package | Licence | Purpose | Added |
|---|---|---|---|
| react, react-dom 19 | MIT | UI | 2026-10-02 |
| three 0.186 | MIT | viewport | 2026-10-02 |
| zustand 5 | MIT | client state | 2026-10-02 |
| lucide-react | ISC | icons | 2026-10-02 |
| uplot | MIT | residual plots | 2026-10-02 |
| @fontsource-variable/inter | OFL-1.1 (font and package, per its package.json; the OFL notice is in THIRD-PARTY-NOTICES.md) | typography | 2026-10-03 |
| vite, @vitejs/plugin-react, tailwindcss, @tailwindcss/vite, typescript, @types/* (dev) | MIT / MIT / MIT / MIT / Apache-2.0 / MIT | build and types | 2026-10-02 |
| vitest (dev) | MIT | front end unit tests (`npm test` in `web/`) | 2026-10-05 |
| @playwright/test 1.64.0 (dev, pinned; brings playwright and playwright-core) | Apache-2.0 | end-to-end GUI tests against the real server (`web/e2e/`, `npx playwright test`); also what the ad-hoc `web/*.mjs` shot scripts import. Browsers are downloaded by `npx playwright install chromium` into the user cache, never into the repository; nothing of it ships | 2026-10-22 |

Node.js 24 and npm are development tools only; nothing of them ships.

## Rust (native/implicit-mesh, linked into `stokes-implicit-mesh`; ADR-0035)

Audited by `tools/audit_rust_licenses.py` against the same allow-list (`cargo metadata --locked`,
normal dependencies only, procedural macros excluded) and by `cargo audit` in CI. The lockfile is
committed; builds use `--locked`.

| Crate | Licence | Purpose | Added |
|---|---|---|---|
| fidget 0.5.1 (features `mesh` only: no JIT) | MPL-2.0 | implicit-surface evaluation (interpreter) and octree meshing | 2026-11-11 |
| fidget-mesh 0.5.1, vendored and changed (`vendor/fidget-mesh`, changes in `CHANGES-STOKES.md`) | MPL-2.0 | the mesher; its source ships in every installer (ADR-0035 decision 32, G18) | 2026-11-11 |
| nalgebra 0.35 | Apache-2.0 | the mesher's matrices | 2026-11-11 |
| rayon 1 | MIT OR Apache-2.0 | parallel meshing and mesh operations | 2026-11-11 |

Every crate linked into the program, as `tools/audit_rust_licenses.py` resolves it for Linux and
Windows (`x86_64-unknown-linux-gnu`, `x86_64-pc-windows-gnullvm`) from the lockfile of 2026-11-11.
Build-time crates (procedural macros and what only they need) are not linked and not listed.

<details><summary>Linked crates</summary>

```
aho-corasick 1.1.5 (Unlicense OR MIT)
allocator-api2 0.2.21 (MIT OR Apache-2.0)
approx 0.5.1 (Apache-2.0)
arrayvec 0.7.8 (MIT OR Apache-2.0)
bytemuck 1.25.2 (Zlib OR Apache-2.0 OR MIT)
cfg-if 1.0.5 (MIT OR Apache-2.0)
chacha20 0.10.2 (MIT OR Apache-2.0)
const-fnv1a-hash 1.1.0 (MIT)
cpufeatures 0.3.1 (MIT OR Apache-2.0)
crossbeam-deque 0.8.8 (MIT OR Apache-2.0)
crossbeam-epoch 0.9.21 (MIT OR Apache-2.0)
crossbeam-utils 0.8.23 (MIT OR Apache-2.0)
either 1.19.0 (MIT OR Apache-2.0)
equivalent 1.0.3 (Apache-2.0 OR MIT)
facet 0.46.5 (MIT OR Apache-2.0)
facet-core 0.46.5 (MIT OR Apache-2.0)
facet-path 0.46.5 (MIT OR Apache-2.0)
facet-reflect 0.46.5 (MIT OR Apache-2.0)
fidget 0.5.1 (MPL-2.0)
fidget-core 0.5.1 (MPL-2.0)
fidget-mesh 0.5.1 (MPL-2.0)
foldhash 0.2.0 (Zlib)
getrandom 0.4.3 (MIT OR Apache-2.0)
glam 0.30.10 (MIT OR Apache-2.0)
glam 0.31.1 (MIT OR Apache-2.0)
glam 0.32.1 (MIT OR Apache-2.0)
glam 0.33.12 (MIT OR Apache-2.0)
hashbrown 0.17.1 (MIT OR Apache-2.0)
iddqd 0.4.6 (MIT OR Apache-2.0)
impls 1.0.3 (MIT OR Apache-2.0)
libc 0.2.191 (MIT OR Apache-2.0) [linux only]
matrixmultiply 0.3.11 (MIT/Apache-2.0)
memchr 2.8.3 (Unlicense OR MIT)
nalgebra 0.35.0 (Apache-2.0)
num-bigint 0.4.8 (MIT OR Apache-2.0)
num-complex 0.4.6 (MIT OR Apache-2.0)
num-integer 0.1.47 (MIT OR Apache-2.0)
num-rational 0.4.2 (MIT OR Apache-2.0)
num-traits 0.2.19 (MIT OR Apache-2.0)
ordered-float 5.5.1 (MIT)
rand 0.10.3 (MIT OR Apache-2.0)
rand_core 0.10.1 (MIT OR Apache-2.0)
rawpointer 0.2.1 (MIT/Apache-2.0)
rayon 1.12.0 (MIT OR Apache-2.0)
rayon-core 1.13.0 (MIT OR Apache-2.0)
regex 1.13.1 (MIT OR Apache-2.0)
regex-automata 0.4.18 (MIT OR Apache-2.0)
regex-syntax 0.8.11 (MIT OR Apache-2.0)
safe_arch 1.2.0 (Zlib OR Apache-2.0 OR MIT)
serde 1.0.229 (MIT OR Apache-2.0)
serde_core 1.0.229 (MIT OR Apache-2.0)
simba 0.10.2 (Apache-2.0)
smallvec 2.0.0-beta.4 (MIT OR Apache-2.0)
static_assertions 1.1.0 (MIT OR Apache-2.0)
strum 0.28.0 (MIT)
thiserror 2.0.21 (MIT OR Apache-2.0)
typenum 1.20.1 (MIT OR Apache-2.0)
wide 1.7.1 (Zlib OR Apache-2.0 OR MIT)
workspace-hack 0.1.0 (CC0-1.0)
```

</details>

## Development (Python)

| Package | Licence | Purpose |
|---|---|---|
| pytest | MIT | tests |
| hypothesis | MPL-2.0 | property tests |
| httpx | BSD-3 | server tests (TestClient) |
| ruff | MIT | lint and format |
| mypy | MIT | type checking |
| pip-licenses | MIT | licence audit |
| pip-audit | Apache-2.0 | vulnerability audit |
| pre-commit | MIT | hooks |
| hatchling | MIT | build backend |

## Driven as processes, never imported

OpenFOAM 14 (GPL-3.0), snappyHexMesh, cfMesh (GPL-3.0), Gmsh (GPL-2.0+),
coupledSimpleFoam (GPL-3.0), ParaView (BSD-3), ANSYS Fluent (commercial, the
user's), NIST REFPROP (commercial, the user's, reached through CoolProp).

## Bundled separate programs (GPL; shipped beside Stokes, never linked)

| Program | Licence | Source | Purpose | How it is reached | Decided |
|---|---|---|---|---|---|
| `adjointFoam` (the open-source adjoint) | **GPL-3.0-or-later**; its files keep their OpenFOAM Foundation and OpenCFD (ESI) copyright headers (`NOTICE.md` of its repository) | https://github.com/dhanninacs/of14-adjoint (`docs/00-open-questions.md` G26) | the flow split's adjoint gradient (ADR-0041) | the agent runs it as its own process with argv and the case files only, like the install's solvers; looked for beside the configured install's solvers first, then in `<install>/bin/openfoam-adjoint/` (`stokes_agent.bundled`) | owner, 2026-11-15 (G30) |

**Why this does not break rule 1.** Shipping a GPL program in the same installer as Stokes is an
aggregate: Stokes does not link, import, vendor or port its code, and talks to it only through
files, argv and standard streams. The program stays under its own licence.

**Obligations when it is staged into the installer** (the packaging milestone; G26 is settled, the repository is public):

- ship its `LICENSE` (GPL-3.0 text) and `NOTICE.md` in `bin/openfoam-adjoint/` beside the `.exe`;
- offer the corresponding source of the exact build shipped (the `of14-adjoint` commit the binary
  was built from, with its build script), for at least three years or alongside the download;
- the generated `THIRD-PARTY-NOTICES.md` gains a section for it, and its section 1 sentence that
  Stokes distributes no engine is reworded for this one program (the file is generated: change the
  generator's input at that step, not the file by hand);
- record the shipped build's commit and SHA-256 in the release notes.

`tools/audit_licenses.py` and `tools/license_allowlist.toml` cover Python and JavaScript packages
only; they have no place for a bundled binary, so nothing is added there and no gate changes.
The built binary is never committed (`.gitignore`: `bin/openfoam-adjoint/`).

## Forbidden

PyQt5, PyQt6, pythonocc-core (use OCP), Gmsh SDK bindings, PyFoam, fluidfoam,
TetGen bindings, CGAL bindings, Qt Charts, Qt Data Visualization, any package
whose licence is GPL, AGPL, SSPL, non-commercial or unknown. In JavaScript the
same classes apply; copyleft UI kits and "free for non-commercial" icon or
font packs are out.

## Adding a dependency

1. Check the licence against the allow-list; if LGPL, confirm dynamic linking
   and add the notice obligations.
2. Add a row above with purpose and date.
3. Python: add it to the right extra in `pyproject.toml` with a version floor.
   JavaScript: `npm install` it in `web/` so `package.json` records the range.
4. Regenerate `THIRD-PARTY-NOTICES.md` (the notices file every build ships), then run `python tools/audit_licenses.py`, `python tools/audit_js_licenses.py`
   and `pip-audit`.
5. Say in the PR why no existing dependency covers the need.
