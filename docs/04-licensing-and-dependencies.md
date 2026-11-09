# 04 · Licensing and dependencies

*Revised 2026-10-02. Licence facts marked "verified" were read from the
source pages today; the rest are from memory and are re-checked at the first
audit (M0). Engineering guidance, not legal advice; a lawyer reviews it before
the first public release.*

## 1. Policy

| Class | Licences | Rule |
|---|---|---|
| Allowed | MIT, BSD-2, BSD-3, Apache-2.0, MPL-2.0, PSF-2.0, ISC, zlib, BSL-1.0, OFL (fonts), CC0, HPND | Use freely; keep the notice. MPL files modified by us are published. |
| Conditional | LGPL-2.1, LGPL-3.0, EPL-2.0 | Unmodified and dynamically linked (or imported as a separate Python package); ship the licence text; provide the library source or a link; keep the library replaceable. Qt and OCCT live here. |
| Forbidden in the desktop and the agent | GPL (any version), AGPL, SSPL, "non-commercial", "evaluation only", unlicensed code | Never imported, linked, vendored or ported. |
| Allowed only as external processes | GPL executables: OpenFOAM, snappyHexMesh, cfMesh, coupledSimpleFoam, Gmsh | Driven by files and argv. Stokes does not ship them; the build helper obtains source from public repositories. |
| User-supplied commercial software | ANSYS Fluent, NIST REFPROP | Driven through permissively licensed wrappers (PyFluent MIT, CoolProp MIT); never shipped; the user's licence terms govern their use. |

Enforcement: `pip-licenses` against a curated allow-list in CI; an
import-time test that fails if any forbidden package is importable; a
generated `THIRD-PARTY-NOTICES.md` per release; vendored C++ in
`stokes/native/third_party/` with licence files.

## 2. Dependency audit

### Desktop application

| Component | Used for | Licence | Verdict | Notes |
|---|---|---|---|---|
| Python 3.13 (3.14 in CI) | runtime | PSF-2.0 | ok | |
| PySide6 6.11.2 + shiboken6 (QtWidgets, QtQuick, QtQml, QtQuickWidgets) | UI | LGPL-3.0-only OR GPL | ok under LGPL obligations (§4) | verified PyPI 2026-10-02; Python 3.10–3.14 |
| Qt GPL-only modules (Qt Charts, Qt Data Visualization, Qt Virtual Keyboard, Qt Wayland Compositor, others listed by Qt) | — | GPL-3.0 for open-source users | **banned** | exact list for Qt 6.11 confirmed at M0 |
| VTK 9.7.1 | viewport, OpenFOAM reader, extraction, EnSight reader | BSD-3 | ok | verified PyPI |
| cadquery-ocp 8.0.1 (OCP) | OCCT bindings | Apache-2.0 | ok | verified PyPI; Python 3.11–3.14 |
| Open CASCADE Technology (inside OCP) | CAD kernel | LGPL-2.1 + Open CASCADE exception | ok, dynamic, prominent notice required | exception text verified |
| numpy, scipy | arrays, numerics | BSD-3 | ok | |
| pint | quantities and unit systems | BSD-3 | ok | |
| lark | dictionary and expression grammars | MIT | ok | |
| uPlot (web front end) | live plots | MIT | ok | replaces Qt Charts; pyqtgraph left with the Qt Widgets shell (ADR-0013) |
| nanobind | C++ bindings | BSD-3 | ok | |
| scikit-build-core, CMake, Ninja | native build | MIT / BSD-3 / Apache-2.0 | ok | |
| msgpack | agent protocol | Apache-2.0 | ok | |
| pydantic | node schemas, API schemas | MIT | ok | |
| FastAPI + uvicorn (or aiohttp) | HTTP/JSON-RPC server, OpenAPI | MIT + BSD-3 (Apache-2.0) | ok | |
| mcp (Model Context Protocol Python SDK) | MCP server | MIT | ok | |
| anthropic | assistant provider | MIT | ok | |
| openai | assistant provider, also for OpenAI-compatible local servers (LM Studio, Ollama) | Apache-2.0 | ok | |
| google-genai | assistant provider | Apache-2.0 | ok | |
| keyring | provider keys in the OS keychain | MIT | ok | |
| cryptography | Ed25519 licence signatures | Apache-2.0 OR BSD-3 | ok | |
| CoolProp | fluid properties; REFPROP backend when the user has REFPROP | MIT | ok | REFPROP itself is NIST commercial software, user-supplied, never shipped |
| Cantera | gas mixtures | BSD-3 | ok | |
| ansys-fluent-core (PyFluent) 0.42.1 | Fluent engine (paid feature) | MIT (copyright Synopsys, Inc. and ANSYS, Inc.) | ok | verified 2026-10-02; requires the user's licensed Fluent 2024 R2 SP05 or later; Ansys academic and student licences exclude commercial use, which is the user's responsibility |
| Jinja2 | report templates | BSD-3 | ok | |
| Pillow | images | HPND | ok | |
| matplotlib (reports only) | static figures | PSF-based | ok | |
| OpenSSH client (system `ssh`, `scp`, `rsync`) | remote runner | BSD / GPL (rsync) | ok as separate processes | no SSH library in-process |
| Nuitka | compilation, distribution | Apache-2.0 | ok | |
| PyInstaller (fallback) | bundling | GPL-2.0 with bootloader exception | ok for proprietary bundling | verified |
| Inno Setup / WiX | Windows installer | Inno Setup licence / MS-RL | ok | |
| Icons: Lucide (ISC), Phosphor (MIT) or Tabler (MIT) | icons | permissive | ok | |
| Inter | UI font | OFL-1.1 | ok | |

### Agent

| Component | Licence | Verdict |
|---|---|---|
| Python, numpy, msgpack, VTK | as above | ok |

### Driven, never shipped

| Component | Licence | How Stokes uses it |
|---|---|---|
| OpenFOAM 14 (Foundation) and its utilities | GPL-3.0 | detected on the user's machine; driven by files and argv |
| coupledSimpleFoam (+ cht, + gpu) | GPL-3.0 (own code; GPL because it links OpenFOAM) | built from the public GitHub repositories at a pinned tag by the helper, inside the user's environment |
| cfMesh | GPL-3.0 | detected or built by the helper; process only |
| Gmsh | GPL-2.0-or-later | detected (`gmsh` on PATH or a user-provided binary); process only, `.geo` in, `.msh` out |
| PETSc, hypre, Eigen, Open MPI | BSD-2, Apache-2.0/MIT, MPL-2.0, BSD-3 | prerequisites the helper detects or installs through the distro's package manager |
| ParaView | BSD-3 | detected for "open in ParaView" |
| ANSYS Fluent | commercial | driven through PyFluent; the user's licence |
| NIST REFPROP | commercial | reached through CoolProp's REFPROP backend when present |

### Rejected

| Component | Licence | Why rejected |
|---|---|---|
| PyQt5 / PyQt6 | GPL-3.0 or commercial (Riverbank) | fingen's choice; unusable here; ported code switches to PySide6 |
| pythonocc-core | LGPL-3.0 | usable in principle; OCP has the Apache licence and the better wheels |
| Gmsh SDK (linking) | GPL-2.0+ | linking makes the app GPL |
| TetGen | AGPL-3.0 | copyleft with network clause |
| CGAL (most packages) | GPL-3.0 | not needed |
| PyFoam, fluidfoam | GPL | own parser instead |
| Qt Charts | GPL-3.0 only | uPlot in the web front end instead |
| Parasolid, ACIS, HOOPS Exchange, Datakit | commercial | out by decision D2 and D3: OCCT only, STEP-class formats only |
| Any shipped OpenFOAM or WSL image | GPL distribution burden | out by decision B2: attach to existing installs |

## 3. The GPL boundary: rules

1. The desktop and the agent never link, `dlopen` or import any GPL library.
2. No OpenFOAM headers are included anywhere in Stokes code; `stokes.native`
   builds without OpenFOAM present.
3. Dictionaries are read and written by Stokes' own parser and printer; the
   format is an interface, the implementation inside OpenFOAM is not copied.
4. No GPL source is copied, translated or ported into Stokes.
5. Communication with GPL programs happens only through files, argv,
   environment variables, standard streams and exit codes. No shared memory,
   no plugins loaded into their processes; `coded` function objects are not
   generated in v1.
6. Results are read with VTK's `vtkOpenFOAMReader` (BSD).
7. Stokes ships no GPL software. The build helper clones public repositories
   at pinned tags and compiles in the user's environment; the source is the
   distribution. If Stokes ever distributes a GPL binary, the complete
   corresponding source accompanies it.
8. In-house GPL work (coupledSimpleFoam) stays GPL in its own repositories;
   proprietary value stays on the desktop and agent side.
9. A lawyer reviews this document and the notices before the first public
   release.

## 4. LGPL handling (Qt, OCCT)

Qt's stated obligations (read 2026-10-02): deliver or offer the complete
corresponding source of the library as used, keep it dynamically linked and
replaceable, provide the licence text and a prominent notice, no locked-down
devices. The OCCT exception (verified text) allows object code incorporating
OCCT header material to be distributed under terms of our choice, provided
the documentation gives prominent notice that the software uses Open CASCADE
Technology.

- Dynamic linking only; the Nuitka standalone build keeps the shared
  libraries as separate files.
- A "Third-party software" dialog and `THIRD-PARTY-NOTICES.md` list Qt and
  OCCT with exact versions and licence texts.
- Source archives for the exact Qt and OCCT versions shipped are mirrored on
  our own server and linked from the notices.
- The About box and the manual carry the OCCT notice.
- Qt and OCCT are not modified; a patch, if ever needed, is published.
- A replacement test (swap the Qt shared libraries for a stock build and
  start the app) is performed once per major release and recorded.

**moocore (added 2026-10-03).** pymoo 0.6.1.6 and later depend on `moocore`
(LGPL-2.1-or-later), a CFFI extension that pymoo imports at run time. It is
accepted under the same conditions as Qt and OCCT: unmodified, loaded as a
separate file that a user can replace (frozen builds must not embed it in the
executable), licence text and a source link in the notices. It arrives only
with the optional `explore` extra. Should these conditions ever be
inconvenient, the fallback is pinning pymoo to 0.6.1.5 or shipping the extra
without pymoo (NSGA-II/III then raise a typed error).

**Dual-licensed libraries inside wheels (elections of 2026-10-03).** Where a
bundled library offers a choice, Stokes elects the non-GPL option: FreeImage
and FreeType inside cadquery-ocp under the FreeImage Public License and the
FreeType License; LibRaw inside cadquery-ocp under CDDL-1.0; gl2ps inside VTK
under the GL2PS License. The GCC Fortran runtime inside the numpy and scipy
wheels is GPL-3.0 with the GCC Runtime Library Exception, which permits
proprietary use. `THIRD-PARTY-NOTICES.md` records every bundled component with
these elections and the attribution sentences FreeType and the Independent JPEG
Group require; the build must add the licence texts the wheels omit (Qt's LGPL
text and Chromium credits, OCCT's LGPL-2.1 and exception) and must exclude the
GPL-only Qt modules (Charts, Data Visualization) that the PySide6 wheels carry.

## 5. OpenFOAM trade mark

From OpenCFD's policy (read 2026-10-02):

- Never use "OpenFOAM" in the product name, company name, domain or
  subdomain; never alter the mark.
- Use ® on the first reference on each screen or document: OPENFOAM®.
- Acknowledgement: "OPENFOAM® is a registered trade mark of OpenCFD Limited,
  producer and distributor of the OpenFOAM software via www.openfoam.com."
- Disclaimer, prominently: "This offering is not approved or endorsed by
  OpenCFD Limited, producer and distributor of the OpenFOAM software via
  www.openfoam.com, and owner of the OPENFOAM® and OpenCFD® trade marks."
- Describe compatibility factually; never claim to be "OpenFOAM developers".

Ansys and Fluent are trade marks of ANSYS, Inc.; Stokes describes the
integration factually ("runs ANSYS Fluent through PyFluent") and claims no
endorsement.

## 6. Freemium licence design (decisions A2, I3, J1, J4)

- **Free tier**: the full local workflow with no licence file at all. No
  limits on cells, cores or concurrent jobs, ever.
- **Paid features**, as flags in a licence file: `remote` (SSH hosts;
  schedulers and containers when they ship), `fluent`, `assistant` (embedded
  assistant and MCP server), `reports` (HTML/PDF reports, run comparison,
  parametric sweeps when they ship).
- **Licence file**: JSON payload (holder, product, flags, machine fingerprint
  or "any", issue and expiry dates, grace days) plus an Ed25519 signature;
  the public key is compiled into the app; the private key stays offline.
- **Node-locked** by salted hardware fingerprint with tolerance for one
  component change; a floating server only if ever needed.
- **Offline activation**: request file and licence file exchanged by email;
  online activation optional.
- **Enforcement**: the flagged features check the licence at the API level,
  so UI, CLI, HTTP and MCP are gated identically. GPL engines themselves are
  never restricted.
- **Trial**: a signed 30-day licence with all flags.
- Licence events logged locally; nothing phones home without opt-in.

## 7. Code protection (decision J2)

- Nuitka standalone builds of `stokes` and `agent`; shared libraries kept
  separate for LGPL replaceability.
- Code signing: Windows Authenticode (EV certificate to avoid SmartScreen
  warnings); Linux tarballs and AppImages signed with GPG.
- No obfuscation. The licence public key is the only embedded key.
- Debug symbols stripped from releases, kept internally for crash
  symbolication.

## 8. Intellectual property rules (decisions A4, IP1, K3)

- Code from fingen, finmesh and the solver repositories is yours and may be
  ported. Every ported file gets a Stokes licence header and a review for
  employer identifiers.
- employer designs, the internal component library (internal databases and design files),
  internal study results, and any measured employer data never enter this
  repository, its fixtures, tutorials or documentation.
- Validation uses classic benchmarks, coupledSimpleFoam's own tutorials and
  public verification cases (NASA TMR, ERCOFTAC) only.

## 9. Audit checklist per release

- [ ] `pip-licenses` output matches the allow-list (CI gate).
- [ ] `THIRD-PARTY-NOTICES.md` regenerated and shipped.
- [ ] No GPL binaries in the installer; the build helper pins public tags.
- [ ] LGPL libraries are separate files; the replacement test is recorded.
- [ ] Trade-mark wording present in About, installer, website.
- [ ] The forbidden-import test passes in the release environment.
- [ ] No employer identifiers or data in the repository (grep gate).
