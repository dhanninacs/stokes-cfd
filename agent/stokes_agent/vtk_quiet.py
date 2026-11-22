"""Keep VTK from opening its own window.

On Windows VTK's default output window is a visible top-level window titled "vtkOutputWindow", and
any warning or error raised while the agent reads a case (the OpenFOAM reader is chatty) pops it
up on the user's desktop when a run finishes. The agent has no display of its own, so VTK's
messages are collected in memory and nothing is shown.
"""

from __future__ import annotations


def quiet_vtk() -> None:
    """Replace VTK's output window with a silent one. Idempotent; call before the first reader."""
    from vtkmodules.vtkCommonCore import vtkLogger, vtkOutputWindow, vtkStringOutputWindow

    if not isinstance(vtkOutputWindow.GetInstance(), vtkStringOutputWindow):
        vtkOutputWindow.SetInstance(vtkStringOutputWindow())
    # **Warnings also go to stderr through `vtkLogger`**, which the output window does not cover:
    # `vtkPolyhedron`'s "non-manifold ... cannot be contoured" reached the run log on every slice.
    # The slice now finds and re-cuts those cells itself and reports the count it could not draw
    # (`extract.cut_plane`, `skipped_cells`), so warnings are dropped here and errors still show.
    vtkLogger.SetStderrVerbosity(vtkLogger.VERBOSITY_ERROR)
