"""Stokes: CAD-to-results CFD workbench on OpenFOAM, built for engineers and their agents.

Import rules for the whole package are in CLAUDE.md §1 and §2. In short: the
core (units, model, api, cli, license, compile, engines, run, native loader) is
headless and never imports Qt, VTK or OCP; only viewport, geometry, ui, app and
post may.
"""

__version__ = "0.0.1.dev0"
