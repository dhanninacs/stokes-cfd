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
