# Stokes third-party notices

Stokes 0.0.1.dev0, Windows (x86-64) build. Generated 2026-10-03.

## 1. About this file

Stokes is proprietary software; its own terms are in the `LICENSE` file. Stokes bundles or
links the open-source components listed below. Each component stays under its own licence.
Those licences govern the components, and nothing in the Stokes licence limits the rights they
give you.

The full text of every licence, with the copyright notice that each component carries, is in
the `licenses/` folder of the installed application. The build generates that folder (see
section 5). MIT, BSD, ISC and Apache-2.0 components require their notice and licence text to
travel with the software; the `licenses/` folder is how Stokes meets that requirement.
Section 4 gives the further notices that particular licences require.

Engines such as OPENFOAM® are separate programs that you install yourself. Stokes starts them
as separate processes and exchanges files, command-line arguments and standard streams with
them. They are not part of Stokes, Stokes does not distribute them, and this file does not
cover them.

OPENFOAM® is a registered trade mark of OpenCFD Limited, producer and distributor of the
OpenFOAM software via www.openfoam.com. This product is not approved or endorsed by OpenCFD
Limited.

## 2. Python components

69 packages. They are the dependencies and optional extras that Stokes declares in
`pyproject.toml` (every extra except `dev`), and everything those packages require in turn.
Development and build tools (pytest, hypothesis, ruff, mypy, pip-licenses, pip-audit,
pre-commit, hatchling and what they need) are not shipped and are not listed.

Versions, licence identifiers and URLs come from the package metadata. Where the metadata has
no usable SPDX identifier, the identifier was read from the package's licence file. For the Qt
packages, which offer a choice that matters, the licence column says which option Stokes uses.
"See 4.x" points to a notice in section 4.

| Package | Version | Licence | URL |
|---|---|---|---|
| about-time | 4.2.1 | MIT | https://github.com/rsalmei/about-time |
| alive-progress | 3.3.0 | MIT | https://github.com/rsalmei/alive-progress |
| annotated-doc | 0.0.5 | MIT | https://github.com/fastapi/annotated-doc |
| annotated-types | 0.8.0 | MIT | https://github.com/annotated-types/annotated-types |
| anyio | 4.15.1 | MIT | https://github.com/agronholm/anyio |
| autograd | 1.9.1 | MIT | https://github.com/HIPS/autograd |
| cadquery-ocp | 8.0.1.0.0 | Apache-2.0 (bindings); OCCT inside: LGPL-2.1 with exception; see 4.3, 4.7 | https://pypi.org/project/cadquery-ocp/ |
| cadquery-ocp-proxy | 8.0.1.0.0 | Apache-2.0 | https://pypi.org/project/cadquery-ocp-proxy/ |
| cantera | 3.2.0 | BSD-3-Clause; see 4.9 | https://github.com/Cantera/cantera |
| cffi | 2.1.1 | MIT-0 | https://github.com/python-cffi/cffi |
| click | 8.5.0 | BSD-3-Clause | https://github.com/pallets/click |
| cloudpickle | 3.1.2 | BSD-3-Clause | https://github.com/cloudpipe/cloudpickle |
| cma | 4.5.0 | BSD-3-Clause | https://github.com/CMA-ES/pycma |
| contourpy | 1.4.0 | BSD-3-Clause | https://github.com/contourpy/contourpy |
| CoolProp | 8.0.0 | MIT; see 4.9 | https://github.com/CoolProp/CoolProp |
| cryptography | 50.0.2 | Apache-2.0 OR BSD-3-Clause; see 4.7 | https://github.com/pyca/cryptography |
| cycler | 0.12.1 | BSD-3-Clause | https://github.com/matplotlib/cycler |
| Deprecated | 3.0.0 | MIT | https://github.com/laurent-laporte-pro/deprecated |
| dill | 0.4.1 | BSD-3-Clause | https://github.com/uqfoundation/dill |
| fastapi | 0.142.2 | MIT | https://github.com/fastapi/fastapi |
| flexcache | 0.3 | BSD-3-Clause | https://github.com/hgrecco/flexcache |
| flexparser | 0.4 | BSD-3-Clause | https://github.com/hgrecco/flexparser |
| fonttools | 4.66.1 | MIT | https://github.com/fonttools/fonttools |
| graphemeu | 0.7.2 | MIT | https://github.com/timendum/grapheme |
| h11 | 0.16.0 | MIT | https://github.com/python-hyper/h11 |
| httptools | 0.8.0 | MIT | https://github.com/MagicStack/httptools |
| idna | 3.20 | BSD-3-Clause | https://github.com/kjd/idna |
| joblib | 1.6.0 | BSD-3-Clause | https://github.com/joblib/joblib |
| kiwisolver | 1.5.1 | BSD-3-Clause | https://github.com/nucleic/kiwi |
| lark | 1.3.1 | MIT | https://github.com/lark-parser/lark |
| matplotlib | 3.11.2 | Matplotlib License (PSF-based); see 4.5, 4.7 | https://github.com/matplotlib/matplotlib |
| moocore | 0.3.2 | LGPL-2.1-or-later; see 4.4 | https://github.com/multi-objective/moocore |
| msgpack | 1.2.3 | Apache-2.0 | https://github.com/msgpack/msgpack-python |
| multiprocess | 0.70.19 | BSD-3-Clause | https://github.com/uqfoundation/multiprocess |
| narwhals | 2.26.0 | MIT | https://github.com/narwhals-dev/narwhals |
| numpy | 2.5.3 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0; see 4.7 | https://github.com/numpy/numpy |
| opentelemetry-api | 1.45.0 | Apache-2.0 | https://github.com/open-telemetry/opentelemetry-python |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause | https://github.com/pypa/packaging |
| pandas | 3.0.6 | BSD-3-Clause | https://github.com/pandas-dev/pandas |
| pillow | 12.3.0 | MIT-CMU; see 4.7 | https://github.com/python-pillow/Pillow |
| Pint | 0.26.1 | BSD-3-Clause | https://github.com/hgrecco/pint |
| platformdirs | 4.12.2 | MIT | https://github.com/tox-dev/platformdirs |
| pycparser | 3.0 | BSD-3-Clause | https://github.com/eliben/pycparser |
| pydantic | 2.13.5 | MIT | https://github.com/pydantic/pydantic |
| pydantic_core | 2.46.5 | MIT | https://github.com/pydantic/pydantic/tree/main/pydantic-core |
| pymoo | 0.6.2 | Apache-2.0 | https://pymoo.org |
| pyparsing | 3.3.3 | MIT | https://github.com/pyparsing/pyparsing |
| PySide6 | 6.11.2 | LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only; used under LGPL-3.0-only; see 4.1 | https://code.qt.io/cgit/pyside/pyside-setup |
| PySide6_Addons | 6.11.2 | LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only; used under LGPL-3.0-only; see 4.1 | https://code.qt.io/cgit/pyside/pyside-setup |
| PySide6_Essentials | 6.11.2 | LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only; used under LGPL-3.0-only; see 4.1 | https://code.qt.io/cgit/pyside/pyside-setup |
| python-dateutil | 2.9.0.post0 | BSD-3-Clause and Apache-2.0 (dual) | https://github.com/dateutil/dateutil |
| python-dotenv | 1.2.4 | BSD-3-Clause | https://github.com/theskumar/python-dotenv |
| PyYAML | 6.0.3 | MIT | https://github.com/yaml/pyyaml |
| ruamel.yaml | 0.19.1 | MIT | https://sourceforge.net/p/ruamel-yaml/ |
| SALib | 1.6.0 | MIT | https://github.com/SALib/SALib |
| scikit-learn | 1.9.1 | BSD-3-Clause | https://github.com/scikit-learn/scikit-learn |
| scipy | 1.18.1 | BSD-3-Clause; see 4.7 | https://github.com/scipy/scipy |
| shiboken6 | 6.11.2 | LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only; used under LGPL-3.0-only; see 4.1 | https://code.qt.io/cgit/pyside/pyside-setup |
| six | 1.17.0 | MIT | https://github.com/benjaminp/six |
| starlette | 1.7.0 | BSD-3-Clause | https://github.com/Kludex/starlette |
| threadpoolctl | 3.7.0 | BSD-3-Clause | https://github.com/joblib/threadpoolctl |
| typing_extensions | 4.16.0 | PSF-2.0 | https://github.com/python/typing_extensions |
| typing-inspection | 0.4.4 | MIT | https://github.com/pydantic/typing-inspection |
| tzdata | 2026.4 | Apache-2.0 | https://github.com/python/tzdata |
| uvicorn | 0.54.0 | BSD-3-Clause | https://github.com/Kludex/uvicorn |
| vtk | 9.6.2 | BSD-3-Clause; see 4.7 | https://vtk.org |
| watchfiles | 1.3.0 | MIT | https://github.com/samuelcolvin/watchfiles |
| websockets | 17.1 | BSD-3-Clause | https://github.com/python-websockets/websockets |
| wrapt | 2.5.0 | BSD-2-Clause | https://github.com/GrahamDumpleton/wrapt |

## 3. JavaScript components

8 packages are bundled into the web front end (`web/dist`). They are the production
dependencies in `web/package.json` and what those require. Build tools (Vite, Tailwind CSS,
TypeScript and their dependencies) are not shipped; section 4.6 covers the generated code that
two of them leave in the bundle. Licences are taken from each package's `package.json`; for
lucide-react the package's licence file adds a second licence for part of the icons.

| Package | Version | Licence | URL |
|---|---|---|---|
| @fontsource-variable/inter | 5.3.0 | OFL-1.1; see 4.5 | https://fontsource.org/fonts/inter |
| lucide-react | 1.49.0 | ISC (icons derived from Feather: MIT); see 4.6 | https://lucide.dev |
| react | 19.3.0 | MIT; see 4.6 | https://react.dev |
| react-dom | 19.3.0 | MIT; see 4.6 | https://react.dev |
| scheduler | 0.28.0 | MIT; see 4.6 | https://react.dev |
| three | 0.186.1 | MIT; see 4.6 | https://threejs.org |
| uplot | 1.6.32 | MIT; see 4.6 | https://github.com/leeoniya/uPlot |
| zustand | 5.0.15 | MIT; see 4.6 | https://github.com/pmndrs/zustand |

The TypeScript declaration packages `@types/react` 19.3.0 and `csstype` 3.2.3 (both MIT) also
appear in the production dependency tree, as optional peer dependencies. They contain no
runtime code and are not part of `web/dist`.

## 4. Notices required by specific licences

Each notice applies to the components it names. Where a component has a choice of licences, the
notice says which one Stokes uses.

### 4.1 Qt and PySide6 (LGPL-3.0)

The desktop window uses Qt 6.11.2 through PySide6 6.11.2 (the packages PySide6,
PySide6_Essentials, PySide6_Addons and shiboken6). Qt is Copyright (C) The Qt Company Ltd. and
other contributors. **Stokes uses Qt under the GNU Lesser General Public License, version 3
(LGPL-3.0-only).**

- **Modules.** Stokes uses QtCore, QtGui, QtWidgets, QtWebChannel, QtWebEngineCore and
  QtWebEngineWidgets, and the Qt libraries that these need. It does not use the Qt modules that
  Qt offers to open-source users only under the GPL (for example Qt Charts and Qt Data
  Visualization), and the application does not include them.
- **Dynamic linking.** The Qt libraries and the PySide6 and shiboken6 extension modules are
  separate files in the application folder, and Stokes loads them at run time. Stokes is not
  statically linked to Qt.
- **Replacing Qt.** You may replace the Qt libraries with your own build of the same Qt 6.11
  release series, and Stokes will load them. You may also reverse engineer Stokes as far as
  you need to in order to debug such a replacement.
- **No changes.** Stokes does not modify Qt. The libraries are the ones published in the
  PySide6 6.11.2 packages.
- **Source code.** Qt 6.11.2: https://download.qt.io/official_releases/qt/6.11/6.11.2/.
  PySide6 and shiboken6: https://code.qt.io/cgit/pyside/pyside-setup.git/.
- **Licence texts.** LGPL-3.0: https://www.gnu.org/licenses/lgpl-3.0.html. It adds permissions
  to the GNU GPL-3.0: https://www.gnu.org/licenses/gpl-3.0.html. Both texts are in the
  `licenses/` folder.
- **Third-party code in Qt.** Qt itself includes third-party code under other licences, which
  Qt lists at https://doc.qt.io/qt-6/licenses-used-in-qt.html. A copy of that listing is in the
  `licenses/` folder.

### 4.2 Chromium inside Qt WebEngine

The interface is displayed by Qt WebEngine, which is part of Qt (4.1) and embeds the Chromium
browser engine. Chromium is Copyright The Chromium Authors and is available under a BSD
licence (BSD-3-Clause). Chromium also contains third-party components, each under its own
licence. Qt lists them, with their licences and credits, on its "Qt WebEngine Licensing" page:
https://doc.qt.io/qt-6/qtwebengine-licensing.html. According to Qt, the most restrictive
licence among them is the LGPL-2.1 (FFmpeg, for example), and Qt WebEngine does not link to or
distribute GPL binary code. A copy of that listing is in the `licenses/` folder. You can
replace Qt WebEngine like the other Qt libraries, with a build made from the Qt sources.

### 4.3 Open CASCADE Technology (inside cadquery-ocp)

CAD import and geometry use Open CASCADE Technology (OCCT) 8.0.1 through the cadquery-ocp
8.0.1.0.0 package (the OCP Python bindings, Apache-2.0, https://github.com/CadQuery/OCP).

> This software makes use of, and is based in part on, facilities provided by the Open CASCADE
> Technology software (OCCT), © OPEN CASCADE SAS, https://dev.opencascade.org.

OCCT is free software under the GNU Lesser General Public License, version 2.1, with the Open
CASCADE exception, version 1.0. **Stokes uses OCCT under those terms.**

- **The exception, in short.** Object code that incorporates material from OCCT header files
  may be distributed under terms of the distributor's choice, provided that the supporting
  documentation gives prominent notice that it makes use of, or is based on, facilities
  provided by OCCT. The notice above is that notice. Full text:
  https://github.com/Open-Cascade-SAS/OCCT/blob/V8_0_1/OCCT_LGPL_EXCEPTION.txt.
- **Dynamic linking.** OCCT is in separate shared libraries, one DLL per OCCT toolkit (for
  example TKernel, TKMath, TKBRep and TKDESTEP), which the OCP extension loads at run time.
  Stokes does not modify them. You may replace them with your own build of OCCT 8.0.1 under
  the same file names.
- **Source code.** https://github.com/Open-Cascade-SAS/OCCT/tree/V8_0_1.
- **Licence text.** LGPL-2.1: https://www.gnu.org/licenses/old-licenses/lgpl-2.1.html. It is in
  the `licenses/` folder with the exception.

### 4.4 moocore (LGPL-2.1-or-later)

moocore 0.3.2 comes with pymoo, in the optional `explore` extra, and provides pymoo's
non-dominated sorting and hypervolume calculation. It is licensed LGPL-2.1-or-later: most of
its C files are MPL-2.0, but they use an AVL-tree library under the LGPL-2.1-or-later, so the
combined library is LGPL-2.1-or-later (see the `COPYRIGHTS` file in the package).
**Stokes uses moocore under the LGPL-2.1-or-later.**

- **Dynamic linking.** The compiled part is a separate shared library (`_libmoocore`) that
  Python loads at run time. Stokes does not modify it. You may replace it with your own build
  of moocore.
- **Source code.** The C sources are included in the package (`moocore/libmoocore`) and are at
  https://github.com/multi-objective/moocore/.
- **Licence text.** LGPL-2.1: https://www.gnu.org/licenses/old-licenses/lgpl-2.1.html (in the
  `licenses/` folder).

### 4.5 Fonts and the SIL Open Font License

**Inter.** The interface font is Inter Variable, from the `@fontsource-variable/inter`
package. Seven WOFF2 files are in `web/dist/assets` (the latin, latin-ext, cyrillic,
cyrillic-ext, greek, greek-ext and vietnamese subsets).

> Copyright 2016 The Inter Project Authors (https://github.com/rsms/inter)

This Font Software is licensed under the SIL Open Font License, Version 1.1. The licence text,
with a FAQ, is at https://openfontlicense.org and in the `licenses/` folder.

The SIL Open Font License requires that each copy of the font software carries the copyright
notice above and the licence text; that the font is not sold by itself; that a modified version
does not use a Reserved Font Name (the Inter licence declares none); and that the font stays
under this licence. Stokes meets each condition.

**Fonts in matplotlib.** The matplotlib package carries its own fonts and data: the STIX, AMS
and Last Resort fonts (SIL OFL-1.1), the DejaVu and BaKoMa fonts, and others. Their licences and
copyright notices are in matplotlib's `LICENSE` file and in the `LICENSE_DEJAVU` and
`LICENSE_STIX` files beside the fonts; the build copies them into `licenses/`.

### 4.6 three.js, React and the other MIT, BSD and ISC components

The MIT and ISC licences require the copyright notice and the permission notice to be included
in all copies or substantial portions of the software. The BSD licences require the notice, the
conditions and the disclaimer to be reproduced with a binary distribution. The `licenses/`
folder holds these texts for every component in sections 2 and 3. The notices of the front-end
components are:

- three.js 0.186.1 (MIT): Copyright © 2010-2026 three.js authors. https://threejs.org/
- React 19.3.0, React DOM 19.3.0 and scheduler 0.28.0 (MIT): Copyright (c) Meta Platforms, Inc.
  and affiliates. https://react.dev/
- zustand 5.0.15 (MIT): Copyright (c) 2019 Paul Henschel.
- uPlot 1.6.32 (MIT): Copyright (c) 2022 Leon Sorokin.
- lucide-react 1.49.0 (ISC): Copyright (c) 2026 Lucide Icons and Contributors. The icons that
  Lucide derives from the Feather project are MIT-licensed: Copyright (c) 2013-present Cole
  Bemis.

Two build tools leave generated code in `web/dist`. Tailwind CSS 4.3.3 (MIT, Copyright (c)
Tailwind Labs, Inc.) generated the application style sheet, which includes Tailwind's base
(reset) styles. Vite 8.3.2 (MIT, Copyright (c) 2019-present, VoidZero Inc. and Vite
contributors) bundled the front end and inlined its small module-preload helper.

### 4.7 Libraries carried inside binary packages

Binary packages carry compiled libraries of their own. Where a library is offered under a
choice of licences, Stokes uses it under an option that is not the GPL.

- **FreeType** (in the Pillow, matplotlib, VTK and cadquery-ocp packages) is used under the
  FreeType License (FTL), https://freetype.org. For binary distributions the FTL requires a
  statement in the documentation: *This software is based in part on the work of the FreeType
  Team.*
- **Independent JPEG Group** (libjpeg-turbo in Pillow and cadquery-ocp, libjpeg in VTK). For
  binary distributions the IJG License requires a statement in the documentation: *This
  software is based in part on the work of the Independent JPEG Group.* https://www.ijg.org
- **OpenBLAS, LAPACK and the GCC runtime library** (in numpy and scipy). Their compiled
  OpenBLAS library (BSD-3-Clause, with LAPACK under BSD-3-Clause-Open-MPI) statically includes
  parts of the GCC Fortran runtime library, which is licensed GPL-3.0-or-later with the GCC
  Runtime Library Exception, version 3.1. The exception exists so that programs that are not
  under the GPL, proprietary programs included, can use these runtime libraries. Text:
  https://www.gnu.org/licenses/gcc-exception-3.1.html. Source:
  https://gcc.gnu.org/git/?p=gcc.git;a=tree;f=libgfortran.
- **OpenSSL** (in cryptography). The package carries a statically linked OpenSSL (4.0.3 in
  this build), licensed Apache-2.0. https://github.com/openssl/openssl.
- **VTK's third-party libraries.** The VTK package (BSD-3-Clause) carries third-party
  libraries as separate DLLs, among them HDF5, NetCDF, libxml2, expat, FreeType, libjpeg,
  libpng, libtiff, zlib, LZ4, XZ, PROJ and gl2ps. The VTK sources list their licences
  (directory `ThirdParty`); the build copies them into `licenses/`. gl2ps is offered under the
  LGPL or the GL2PS License; Stokes uses it under the GL2PS License.
- **Libraries vendored in cadquery-ocp.** The package carries the libraries that OCCT's data
  exchange modules use, as separate DLLs: FreeImage, LibRaw, OpenEXR with Imath, libpng,
  libjpeg-turbo, libtiff, libwebp, OpenJPEG, OpenJPH, Little CMS, FreeType, Lerc, libdeflate
  and liblzma (XZ Utils), together with the Microsoft Visual C++ runtime (redistributed under
  Microsoft's redistribution terms). The package carries no licence texts for them; the build
  adds them to `licenses/`. FreeImage is offered under the GPL (version 2 or 3) or the FreeImage
  Public License 1.0; Stokes uses it under the FreeImage Public License, and its source is at
  https://freeimage.sourceforge.io/. LibRaw is offered under the LGPL-2.1 or the CDDL-1.0;
  Stokes uses it, as a separate DLL that Stokes does not modify, under the CDDL-1.0, and its
  source is at https://github.com/LibRaw/LibRaw.

### 4.8 Python runtime

Stokes includes the CPython 3.13 interpreter and standard library, under the Python Software
Foundation License, version 2: Copyright (c) 2001-2024 Python Software Foundation; All Rights
Reserved. https://www.python.org/. The standard library includes code under other licences,
which Python's `LICENSE.txt` lists. The build copies that file into `licenses/`.

### 4.9 Property libraries: CoolProp, Cantera and NIST REFPROP

The optional `materials` extra adds two property libraries, used only inside Stokes' materials
worker process:

- **CoolProp** 8.0.0, MIT License, Copyright (c) 2012-2018 Ian H. Bell and other CoolProp
  developers. https://github.com/CoolProp/CoolProp. Its Windows wheel carries the Microsoft
  Visual C++ runtime (`msvcp140`), redistributed under Microsoft's redistribution terms.
- **Cantera** 3.2.0, BSD-3-Clause, Copyright (c) 2001-2009 California Institute of Technology,
  Copyright (c) 2009 Sandia Corporation (under Contract AC04-94AL85000 the U.S. Government
  retains certain rights), Copyright (c) 2011-2025 Cantera Developers. https://cantera.org.
  Its Windows wheel carries, as separate DLLs, HDF5 (The HDF Group's BSD-style licence),
  SUNDIALS (BSD-3-Clause, Lawrence Livermore National Security and Southern Methodist
  University), yaml-cpp (MIT) and the Microsoft Visual C++ runtime; the build copies their
  licence texts into `licenses/`. Cantera reads its YAML files with ruamel.yaml (MIT).

**NIST REFPROP is not part of Stokes.** It is a separately licensed product of the National
Institute of Standards and Technology (Standard Reference Database 23). Stokes ships none of its
files: when a user has installed their own licensed copy, Stokes finds it on the user's machine
and reaches it through CoolProp's REFPROP backend.

## 5. How this file is produced

This file is generated. Do not edit it by hand.

- **Python table (section 2).** The generator reads the dependencies and extras in
  `pyproject.toml` (every extra except `dev`) and follows the requirements recorded in the
  metadata of the installed packages in the build environment, with environment markers
  evaluated for the target platform. Version, licence and URL come from the same metadata;
  where it holds no usable identifier, the generator uses the package's licence file.
- **JavaScript table (section 3).** `npm ls --omit=dev --all --json` in `web/` gives the
  production dependency tree. Licence and URL are read from each package's `package.json` in
  `web/node_modules`. Packages that put nothing into the bundle, such as type declarations,
  are left out.
- **Audits.** Two scripts check the installed packages against the allow-list in
  `tools/license_allowlist.toml`, and the release stops if either fails:
  `python tools/audit_licenses.py` for the Python packages (it runs pip-licenses) and
  `python tools/audit_js_licenses.py` for the packages in `web/node_modules`.
- **Release build.** The build pipeline regenerates this file and the `licenses/` folder for
  every release and every platform, after both audits pass, so that the list matches what the
  build contains. The `licenses/` folder is built from the licence files that each package
  carries, and from the licence texts that section 4 links to.
