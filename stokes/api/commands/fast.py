"""``fast.*`` commands: the GPU preview of the case (ADR-0047).

``fast.start`` reads the case (the fluid's implicit field, its inlets, outlets and walls, the
material) and starts the lattice on the host's GPU; ``fast.configure`` changes the fidelity or the
plane shown; ``fast.status`` reports; ``fast.stop`` ends it. While it runs, model edits re-read the
case on their own, ``fast.frame`` arrives up to ten times a second, and the plane's mesh and
values are served at ``/api/fast/slice`` and ``/api/fast/field``. None of these changes the
model, so none is journaled as an edit; a preview is not a result, and nothing here writes a run.
"""

from __future__ import annotations

import threading
from typing import Any

from stokes.api.registry import CommandContext, ParamDef, command
from stokes.fast.grid import FIDELITY_DEFAULT

FIDELITY = ParamDef(
    "fidelity",
    "integer",
    required=False,
    description="1 (coarsest, quickest) to 5 (finest): cells across the fluid's smallest side "
    "(24, 32, 48, 64, 96).",
)
AXIS = ParamDef(
    "axis",
    "integer",
    required=False,
    description="The shown plane's normal: 0 (x), 1 (y), 2 (z), or -1 for the automatic plane "
    "(the mid-plane normal to the fluid box's shortest side, the default).",
)
OFFSET = ParamDef(
    "offset",
    "any",
    required=False,
    description='Where the shown plane cuts its axis: a length such as "2 [mm]", or a bare number '
    "number of metres. Default: the middle of the fluid.",
)
TURBULENCE = ParamDef(
    "turbulence",
    "string",
    required=False,
    description="The closure of a turbulent preview: auto (the default: shear_improved up to a "
    "cell Reynolds number of 30, smagorinsky above), smagorinsky, shear_improved, wale or vreman "
    "(the last two give a laminar shear no eddy viscosity).",
)
WARM_START = ParamDef(
    "warm_start",
    "boolean",
    required=False,
    description="Start a fine lattice from a coarser one's solution (default true): a run at "
    "fidelity 4 or 5 first settles at fidelity 2, and a fidelity raised while running keeps the "
    "flow. False starts every lattice from rest.",
)
STORAGE = ParamDef(
    "storage",
    "string",
    required=False,
    description="How the lattice keeps its populations: f32 (the default) or f16, half precision "
    "scaled for the lattice, which halves their memory so a finer lattice fits (arithmetic stays "
    "32-bit). A GPU without 16-bit shader storage runs f32 and says so.",
)

_PROBE = threading.Lock()
_available: dict[str, Any] | None = None  # Any: JSON; the adapter probe, once per process


@command("fast.check", params=(), requires_project=False, mutates=False)
def fast_check(ctx: CommandContext) -> dict[str, Any]:  # Any: JSON
    """Whether this computer can run Fast mode: the wgpu package and a hardware GPU adapter.
    Asked once per process (opening an adapter takes about a second)."""
    global _available  # noqa: PLW0603 - the probe's answer does not change while the process runs
    from stokes.errors import StokesError
    from stokes.fast.device import open_device

    with _PROBE:
        if _available is None:
            try:
                device, info = open_device()
                device.destroy()
                _available = {"available": True, "adapter": info.to_json()}
            except StokesError as exc:
                _available = {"available": False, "error": exc.to_dict()}
        return dict(_available)


@command(
    "fast.start",
    params=(FIDELITY, AXIS, OFFSET, TURBULENCE, WARM_START, STORAGE),
    mutates=False,
)
def fast_start(
    ctx: CommandContext,
    fidelity: int | None = None,
    axis: int | None = None,
    offset: Any = None,  # Any: a number of metres or a length's text
    turbulence: str | None = None,
    warm_start: bool | None = None,
    storage: str | None = None,
) -> dict[str, Any]:  # Any: JSON
    """Start the GPU preview of the project's case: one per session; a second one restarts."""
    from stokes.api.fast_inputs import offset_metres
    from stokes.api.fast_state import fast_of

    return fast_of(ctx.session).start(
        fidelity or FIDELITY_DEFAULT,
        axis,
        offset_metres(offset),
        turbulence,
        warm_start=warm_start is not False,
        storage=storage or "f32",
    )


@command("fast.configure", params=(FIDELITY, AXIS, OFFSET, TURBULENCE), mutates=False)
def fast_configure(
    ctx: CommandContext,
    fidelity: int | None = None,
    axis: int | None = None,
    offset: Any = None,  # Any: a number of metres or a length's text
    turbulence: str | None = None,
) -> dict[str, Any]:  # Any: JSON
    """Change the running preview's fidelity (a new grid) or the plane it shows."""
    from stokes.api.fast_inputs import offset_metres
    from stokes.api.fast_state import fast_of

    service = fast_of(ctx.session)
    service.configure(fidelity, axis, offset_metres(offset), turbulence)
    return service.status()


@command("fast.status", params=(), requires_project=False, mutates=False)
def fast_status(ctx: CommandContext) -> dict[str, Any]:  # Any: JSON
    """The preview's state (off, starting, running, stopped, failed), its grid, its last frame."""
    from stokes.api.fast_state import fast_of

    return fast_of(ctx.session).status()


@command("fast.stop", params=(), requires_project=False, mutates=False)
def fast_stop(ctx: CommandContext) -> dict[str, Any]:  # Any: JSON
    """Stop the preview and free the GPU; nothing happens when none is running."""
    from stokes.api.fast_state import fast_of

    return {"stopped": fast_of(ctx.session).stop()}
