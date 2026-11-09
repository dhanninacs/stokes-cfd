# ADR-0002 · UI framework: PySide6 shell with Qt Quick panels and a VTK viewport

**Status:** superseded by ADR-0013 (web front end, three.js viewport) · 2026-10-02 · kept for the reasoning that still applies to the Qt host window

## Context

The brief asks for a modern, fast, intuitive desktop app in Python with
speed and responsiveness as the major criteria. You asked for something more
modern than the Widgets-only draft and floated three.js, leaving the choice
to me on suitability for large 3D data (50 k faces, 20 M triangles, 20 M-cell
meshes). fingen's desktop app uses PyQt5 (GPL or commercial), which is
unusable here.

## Options

| Option | Licence | Modern look | Large 3D data | Verdict |
|---|---|---|---|---|
| PySide6 Widgets + custom styling + VTK | LGPL-3 | achievable with effort | native, no limits | superseded |
| **PySide6 Widgets shell + Qt Quick (QML) panels + VTK viewport** | LGPL-3 | fluid, animated, GPU-composited chrome | native, no limits | **chosen** |
| Web front end (TypeScript) + vtk.js or three.js + Python backend | MIT | easiest | browser memory limits; above a few million triangles the viewport must stream server-rendered frames | rejected on the data criterion |
| Hybrid Qt shell with web panels and a native VTK viewport | LGPL-3 + MIT | good | native | rejected: two UI technologies and a bridge |
| Dear PyGui | MIT | distinctive, fast | own renderer; VTK integration awkward | rejected |

## Decision

- PySide6 6.11+ under LGPL-3 with dynamic linking, licence notices and a
  source offer (docs/04 §4).
- A Qt Widgets top-level window for docking, menus, native dialogs and the
  VTK viewport (`QVTKRenderWindowInteractor`), with the chrome built in Qt
  Quick and hosted through `QQuickWidget`: rail, top bar, adaptive context
  toolbar, inspector forms, assistant panel, notifications.
- The theme follows the OS; both palettes are first-class.
- Plots with pyqtgraph (MIT); Qt Charts and every other GPL-only Qt module
  are banned.
- Forms are generated from node schemas, not hand-built per panel.
- Week one of M0 prototypes the shell on Windows and Linux (focus, HiDPI,
  compositing between `QQuickWidget` and the native VTK widget); any panel
  that misbehaves falls back to Widgets without changing the architecture.

## Consequences

- The modern feel comes from QML motion and layout, not from a web stack;
  CAD and meshes stay in C++ memory with sub-frame picking.
- Two Qt paradigms coexist; the rule is "chrome in QML, data-heavy views in
  Widgets or VTK".
- LGPL obligations must be handled exactly; a Qt commercial licence remains
  available if ever needed.
