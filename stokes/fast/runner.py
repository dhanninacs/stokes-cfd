"""The Fast runner (ADR-0047 decisions 7 and 8): one worker thread that owns the GPU and steps.

**Why a thread** (CLAUDE.md section 8): the lattice steps continuously while the user edits, and
every GPU call blocks until the device has finished; on the server's event loop that would freeze
the app. The thread is the only owner of the device. Commands and model edits only post requests
(``update``, ``configure``, ``stop``) and read the last frame, both under a lock; the thread never
touches the model, and it ends on ``stop``.

Each pass of the loop takes the newest request, then steps a batch sized to about 25 ms of GPU
time, and at most every ``FRAME_SECONDS`` samples the ports and the display plane into a ``Frame``
and calls ``emit("fast.frame", ...)``. A new case whose ``geometry_key`` is unchanged keeps the
lattice and the flow; one with a new field re-classifies in place (new fluid cells start at rest);
a new grid (another box or fidelity) starts from the old grid's solution (the warm start, ADR-0047
addendum 4), or from rest when told to.

The module is long because the worker's loop, what it applies and what it samples share one
thread's state: split, each part would reach into the others' locals.
"""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from numpy.typing import NDArray

from stokes.errors import StokesError
from stokes.fast.case import FastCase
from stokes.fast.errors import FastInvalid, FastTooFast, FastUnstable
from stokes.fast.grid import FIDELITY_DEFAULT, Grid, grid_for
from stokes.fast.health import Health, instability
from stokes.fast.lattice import SPEED, SPEED_MIN, Scaling, scaling_for
from stokes.fast.plane import plane_mesh, thermal_mesh
from stokes.fast.ports import OUTLET, inlet_gains, pack_ports
from stokes.fast.steady import Sample, settle_steps, steady
from stokes.fast.zones import (
    PortSample,
    cells_across,
    describe,
    disconnected,
    fluid_diffusivity,
    port_flow,
    reference_temperature,
    streams,
    zone_rows,
    zone_texts,
)
from stokes.log import fields, get_logger

log = get_logger(__name__)

#: The longest time between two frames, and the GPU time a batch of steps aims at.
FRAME_SECONDS = 0.1
#: **The most of its time the worker spends sampling.** A frame costs what its plane costs to read
#: and mesh, whatever the lattice did meanwhile: on a duct of 11.6 million cells a frame took
#: 0.09 s, so ten a second left the lattice a fifth of the time (34 steps a second of the 146 it
#: can do; measured 2026-11-23). The next frame waits until the last one's cost is this share of
#: the time since: fewer frames on a large lattice, the same ten a second on a small one.
SAMPLE_SHARE = 0.2
BATCH_SECONDS = 0.025
SETTLE_SECONDS = 3.0
#: Frames in a row the guard must act on a turbulent preview before its lattice speed is halved.
RESTRICTIVE_FRAMES = 10
#: **Frames in a row the guard may act on a laminar lattice before the LES takes over.** The inlet's
#: cell Reynolds number can be laminar while the flow runs twice as fast between obstacles (measured
#: on a pin bank, 2026-11-22: the laminar lattice blew up to hundreds of m/s at a 6.3 inlet value).
GUARD_FRAMES = 10
#: Steps over which the inlets ramp from rest to their speed after a start or a reset, and the
#: largest batch while they do, so the ramp is smooth.
RAMP_STEPS = 2000
RAMP_BATCH = 100
FIELDS = ("U", "Ux", "Uy", "Uz", "p", "T")
#: The turbulence closures a run takes: the lattice's own choice by the cell Reynolds number,
#: or one of ``stokes.fast.lattice.CLOSURES``.
AUTO = "auto"
TURBULENCE = (AUTO, "smagorinsky", "shear_improved", "wale", "vreman")
#: How a run may keep its populations (``stokes.fast.wgsl.STORAGES``).
STORAGE_DEFAULT = "f32"
#: **A steady run holds**: after this many steady frames in a row the lattice steps one batch,
#: then waits ``HOLD_SECONDS``, so the GPU is the viewport's again (about a tenth of the load).
#: Any request, and any frame that is not steady, ends the hold.
HOLD_FRAMES = 10
HOLD_SECONDS = 0.25
#: **Steps of the temperature per lattice step once the flow is steady and the heat is not.** A
#: solid heated by a flux warms at its stepped heat capacity (measured 2026-11-22: 2.76 W of 3 W
#: after 500,000 steps); with the flow settled the temperature may run ahead of it to the same
#: steady field.
THERMAL_CATCH_UP = 16
#: **The warm start** (ADR-0047 addendum 4). A run asked for at fidelity ``WARM_FROM`` or finer
#: first runs at ``WARM_FIDELITY`` until its flow is steady, or for ``WARM_CAP`` times the steady
#: window at most (a turbulent preview never is), and the fine lattice then starts from that
#: solution; so does any lattice built while another one is running (a fidelity raised by hand).
WARM_FROM = 4
WARM_FIDELITY = 2
WARM_CAP = 8
#: The longest the status says which lattice the flow came from (``warmed_from``): until the
#: flow is steady, or this long for a preview that never is.
WARMED_SECONDS = 10.0
#: What each sign of a runaway (``stokes.fast.health.instability``) means, for a message.
_SIGNS = {
    "not_finite": "it went NaN",
    "density": "the pressure left the lattice's range",
    "guard": "the stability guard held much of the flow",
    "imbalance": "the outflow and the inflow drifted apart",
}
#: ``axis`` for the automatic plane: the mid-plane normal to the fluid box's shortest side.
AUTOMATIC = -1

Emit = Callable[..., None]


@dataclass
class Display:
    """The plane shown: an axis (0, 1, 2) and a position along it (m); None for the mid-plane."""

    axis: int | None = None
    offset: float | None = None


@dataclass
class Frame:
    """What the last sample found. Velocities m/s, pressures kinematic (m2/s2) unless named _pa."""

    step: int = 0
    mlups: float = 0.0
    dp: float = 0.0
    dp_pa: float = 0.0
    inlet_speed: float = 0.0
    flow_in: float = 0.0
    """The volume flow the inlets deliver, m3/s."""
    flow_out: float = 0.0
    """The volume flow leaving through the outlets, m3/s (negative: flowing back in)."""
    guard: int = 0
    unstable: bool = False
    """True when the frame is not valid: the lattice went NaN, or it shows a sign of a runaway
    (``stokes.fast.health``); its numbers are not to be read."""
    instability: str = ""
    """Which sign (``stokes.fast.health.instability``), or ""."""
    steady: bool = False
    restrictive: bool = False
    geometry_version: int = 0
    axis: int = 2
    plane: int = 0
    positions: NDArray[np.float32] = field(default_factory=lambda: np.zeros((0, 3), np.float32))
    triangles: NDArray[np.uint32] = field(default_factory=lambda: np.zeros((0, 3), np.uint32))
    values: dict[str, NDArray[np.float32]] = field(default_factory=dict)
    thermal_positions: NDArray[np.float32] | None = None
    """The temperature's own mesh (it covers the solids too), with ``thermal_triangles``; its
    values are ``values["T"]``. None when the temperature is not solved."""
    thermal_triangles: NDArray[np.uint32] | None = None
    streams: list[dict[str, Any]] = field(default_factory=list)  # Any: JSON
    """One entry per fluid zone (``stokes.fast.zones.streams``)."""
    holding: bool = False
    """True while the lattice steps slowly because the flow is steady."""
    catching_up: bool = False
    """True while the temperature takes several steps per lattice step (the flow is steady)."""
    warming: bool = False
    """True while the run is on the coarse lattice of a warm start: never called steady."""
    warmed_from: int | None = None
    """The fidelity of the lattice this one took its flow from, until it is steady; None for a
    lattice started from rest, and once it is steady."""
    fidelity: int = 0
    """The fidelity of the grid this frame was sampled on, and its fluid cells (0 before they are
    counted): a client's header follows the frame, not a status it asked for a second ago."""
    fluid_cells: int = 0

    def mesh_of(self, name: str) -> tuple[str, NDArray[np.float32], NDArray[np.uint32]]:
        """Which mesh a value is drawn on (its key, positions and triangles): the
        temperature's own, or the flow's."""
        if name == "T" and self.thermal_positions is not None:
            assert self.thermal_triangles is not None
            return "t", self.thermal_positions, self.thermal_triangles
        return "f", self.positions, self.triangles

    @property
    def heat_balance(self) -> float | None:
        """The sum of the streams' heats, W: 0 for an exchanger that loses nothing; None
        without the temperature."""
        heats = [s["heat"] for s in self.streams if "heat" in s]
        return float(sum(heats)) if heats else None

    @property
    def imbalance(self) -> float:
        """Outflow less inflow, as a share of the inflow: 0 once the flow is steady and the
        lattice conserves it; what is left is the walls' and the ports' discretisation."""
        return (self.flow_out - self.flow_in) / self.flow_in if self.flow_in > 0.0 else 0.0

    def to_json(self) -> dict[str, Any]:  # Any: JSON
        return {
            "step": self.step,
            "mlups": round(self.mlups, 1),
            "dp": self.dp,
            "dp_pa": self.dp_pa,
            "inlet_speed": self.inlet_speed,
            "flow_in": self.flow_in,
            "flow_out": self.flow_out,
            "imbalance": self.imbalance,
            "guard": self.guard,
            "unstable": self.unstable,
            "instability": self.instability,
            "steady": self.steady,
            "restrictive": self.restrictive,
            "geometry_version": self.geometry_version,
            "axis": self.axis,
            "plane": self.plane,
            "vertices": int(self.positions.shape[0]),
            "streams": self.streams,
            "heat_balance": self.heat_balance,
            "holding": self.holding,
            "catching_up": self.catching_up,
            "warming": self.warming,
            "warmed_from": self.warmed_from,
            "fidelity": self.fidelity,
            "fluid_cells": self.fluid_cells,
        }


class FastRunner:
    """One Fast run: ``update`` a case, ``configure`` the display or fidelity, ``stop`` it."""

    def __init__(self, emit: Emit, density: float = 1000.0) -> None:
        self._emit = emit
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = False
        self._pending: FastCase | None = None
        self._case: FastCase | None = None
        self._fidelity = FIDELITY_DEFAULT
        self._fidelity_pending: int | None = None
        self._display = Display()
        self._frame = Frame()
        self._state = "starting"
        self._error: dict[str, Any] | None = None  # Any: JSON
        self._grid: Grid | None = None
        self._scaling: Scaling | None = None
        self._adapter: dict[str, Any] = {}  # Any: JSON
        self.density = density
        self._reference = 0.0
        """The lowest outlet pressure (kinematic): lattice density 1 is this pressure."""
        self._force_les = False
        """Set once the laminar lattice needed the guard: the LES then stays on for this grid."""
        self._fluid_cells = 0
        self._resolution: list[dict[str, Any]] = []  # Any: JSON
        """Each fluid's cells across its passages (``stokes.fast.zones.cells_across``)."""
        self._turbulence = AUTO
        """The closure asked for: ``auto`` or a key of ``stokes.fast.lattice.CLOSURES``."""
        self._reapply = False
        self._warm_start = True
        """Whether a fine lattice starts from a coarser one's solution."""
        self._storage = STORAGE_DEFAULT
        """How the populations are kept: asked for at the start, f32 where the GPU has no f16."""
        self._warm_target: int | None = None
        """The fidelity asked for while the coarse pass of a warm start runs; None otherwise."""
        self._warmed = False
        """Set by ``_apply``: the lattice it built started from the one before it."""
        self._warmed_from: int | None = None
        """The fidelity of the lattice the running one took its flow from, until it is steady."""
        self._warmed_until = 0.0
        """When ``_warmed_from`` is dropped whether or not the flow is steady (monotonic time)."""
        self._gains: dict[str, float] = {}
        """Each inlet's gain (``stokes.fast.ports.inlet_gains``), from the last classification."""
        self._flags: NDArray[np.uint32] | None = None
        """The cell flags of ``_apply``'s last classification; None when it did not classify."""
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------ the API side (any thread)

    def start(
        self,
        case: FastCase,
        fidelity: int | None = None,
        warm_start: bool = True,
        storage: str = STORAGE_DEFAULT,
    ) -> None:
        from stokes.fast.wgsl import STORAGES

        if storage not in STORAGES:
            raise FastInvalid(
                f"the storage is one of {list(STORAGES)}; got {storage!r}",
                details={"storage": storage},
            )
        self._storage = storage
        self._warm_start = warm_start
        self.update(case)
        if fidelity is not None:
            self.configure(fidelity=fidelity)
        self._thread = threading.Thread(target=self._run, name="stokes-fast", daemon=True)
        self._thread.start()

    def update(self, case: FastCase) -> None:
        with self._lock:
            self._pending = case
        self._wake.set()

    def configure(
        self,
        fidelity: int | None = None,
        axis: int | None = None,
        offset: float | None = None,
        turbulence: str | None = None,
    ) -> None:
        if turbulence is not None and turbulence not in TURBULENCE:
            raise FastInvalid(
                f"the turbulence closure is one of {list(TURBULENCE)}; got {turbulence!r}",
                details={"turbulence": turbulence},
            )
        if fidelity is not None and fidelity not in (1, 2, 3, 4, 5):
            raise FastInvalid(
                f"the fidelity is 1 to 5; got {fidelity}", details={"fidelity": fidelity}
            )
        if axis is not None and axis not in (AUTOMATIC, 0, 1, 2):
            raise FastInvalid(
                f"the plane's axis is 0, 1 or 2, or -1 for the automatic plane; got {axis}",
                details={"axis": axis},
            )
        if offset is not None and not math.isfinite(offset):
            raise FastInvalid(
                "the plane's offset is not a finite length", details={"offset": offset}
            )
        with self._lock:
            if fidelity is not None:
                self._fidelity_pending = fidelity
            if turbulence is not None and turbulence != self._turbulence:
                self._turbulence = turbulence
                self._reapply = True
            if axis == AUTOMATIC:
                self._display = Display(None, offset)
            elif axis is not None or offset is not None:
                self._display = Display(axis if axis is not None else self._display.axis, offset)
        self._wake.set()

    def stop(self, wait: float = 5.0) -> None:
        with self._lock:
            self._stop = True
        self._wake.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(wait)

    @property
    def alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def frame(self) -> Frame:
        with self._lock:
            return self._frame

    def status(self) -> dict[str, Any]:  # Any: JSON
        with self._lock:
            out: dict[str, Any] = {  # Any: JSON
                "state": self._state,
                "fidelity": self._warm_target or self._fidelity,
                "warm_start": self._warm_start,
                "storage": self._storage,
                "warming": self._warm_target is not None,
                "warmed_from": self._warmed_from,
                "frame": self._frame.to_json(),
                "adapter": dict(self._adapter),
                "density": self.density,
                "turbulence": self._turbulence,
                "automatic_plane": self._display.axis is None,
            }
            if self._grid is not None:
                out["grid"] = {**self._grid.to_json(), "fluid_cells": self._fluid_cells}
                out["resolution"] = list(self._resolution)
            if self._scaling is not None:
                out["tau"] = self._scaling.tau
                out["dt"] = self._scaling.dt
                out["les"] = self._scaling.les
                out["closure"] = self._scaling.closure
                if self._case is not None:
                    out["cell_reynolds"] = self._scaling.reynolds
                    out["zones"] = describe(self._case.all_zones)
                    out["thermal"] = self._case.thermal
                    out["time_means"] = list(self._case.time_means)
            if self._error is not None:
                out["error"] = dict(self._error)
            return out

    # ------------------------------------------------------------------ the worker thread

    def _run(self) -> None:
        from stokes.fast.device import open_device

        lattice: Any = None  # Any: stokes.fast.device.Lattice, imported with wgpu
        try:
            device, adapter = open_device()
            with self._lock:
                self._adapter = adapter.to_json()
                short = self._storage == "f16" and not adapter.f16
                if short:
                    self._storage = "f32"
            if short:
                self._emit(
                    "fast.warning",
                    code="fast.no_f16",
                    message="this GPU cannot store 16-bit numbers in a shader; "
                    "the preview keeps its populations in 32 bits",
                )
            lattice = self._loop(device)
        except StokesError as exc:
            self._fail(exc.to_dict())
        except Exception:
            log.exception("the Fast run failed")
            self._fail(
                {"code": "fast.internal", "message": "the Fast run failed; the log has the details"}
            )
        finally:
            if lattice is not None:
                lattice.destroy()
            with self._lock:
                if self._state != "failed":
                    self._state = "stopped"
            self._emit("fast.stopped", **self.status())

    def _fail(self, error: dict[str, Any]) -> None:  # Any: JSON
        with self._lock:
            self._state = "failed"
            self._error = error

    # The worker's one loop: each pass reads the requests, steps, then samples, in that order.
    def _loop(self, device: Any) -> Any:  # noqa: PLR0912, PLR0915  # Any: device in, Lattice out
        lattice: Any = None  # Any: Lattice
        speed = SPEED
        cells_of_port: list[NDArray[np.uint32]] = []
        batch = 20
        last_frame = 0.0
        sample_cost = 0.0
        history: list[Sample] = []
        settled_from = time.monotonic()
        steps = 0
        ramp_start = 0
        swinging = 0
        guarding = 0
        geometry_version = 0
        geometry_key = ""
        steady_frames = 0
        flowing: list[Sample] = []
        catch_up = 1
        promote = False
        health = Health()
        sampled_at = 0
        while True:
            with self._lock:
                if self._stop:
                    return lattice
                case, self._pending = self._pending, None
                fidelity, self._fidelity_pending = self._fidelity_pending, None
                reapply, self._reapply = self._reapply, False
            if fidelity is not None:
                with self._lock:
                    self._warm_target = None  # a fidelity asked for by hand ends the coarse pass
            elif promote and self._warm_target is not None:
                with self._lock:
                    fidelity, self._warm_target = self._warm_target, None
            promote = False
            if fidelity is not None and fidelity != self._fidelity:
                self._fidelity = fidelity
                self._force_les = False  # a new grid tries laminar again
                if self._case is not None and case is None:
                    case = self._case
                geometry_key = ""  # a new fidelity is a new grid
            if reapply and case is None and self._case is not None:
                case = self._case  # another closure: the same case under a new scaling
            first = case is not None and lattice is None
            if first and self._warm_start and self._fidelity >= WARM_FROM:
                with self._lock:
                    self._warm_target, self._fidelity = self._fidelity, WARM_FIDELITY
            if case is not None:
                steady_frames = 0
                catch_up = 1
                flowing.clear()
                try:
                    lattice, rebuilt = self._apply(device, lattice, case, speed, geometry_key)
                except StokesError as exc:
                    if lattice is None or self._case is None:
                        raise
                    # **An edit never ends the run**: it stays on the last case that read, says why.
                    self._emit("fast.warning", code=exc.code, message=exc.message)
                    case = None
                    rebuilt = False
            if case is not None:
                if rebuilt or case.geometry_key != geometry_key:
                    geometry_key = case.geometry_key
                    geometry_version += 1
                    flags = self._flags if self._flags is not None else lattice.read_flags()
                    cells_of_port = _port_cells(flags, len(case.ports))
                    with self._lock:
                        codes = flags & 0xFF
                        shaped = codes.reshape(lattice.grid.nz, lattice.grid.ny, lattice.grid.nx)
                        self._resolution = cells_across(shaped, case.all_zones)
                        self._fluid_cells = int(np.count_nonzero((codes != 1) & (codes < 0x80)))
                    self._calibrate(lattice, case, flags, cells_of_port)
                    history.clear()
                    flowing.clear()
                    health = Health()
                    settled_from = time.monotonic() + SETTLE_SECONDS
                    if rebuilt:
                        # A lattice started from another one's solution has its inflow already.
                        ramp_start = steps - RAMP_STEPS if self._warmed else steps
                if self._warm_target is not None and lattice.grid.fidelity >= self._warm_target:
                    with self._lock:
                        self._warm_target = None  # the flow needed the fine cells from the start
                self._case = case
                with self._lock:
                    self._state = "running"
            if lattice is None or self._case is None:
                self._wake.wait(0.2)
                self._wake.clear()
                continue
            if steady_frames >= HOLD_FRAMES and self._wake.wait(HOLD_SECONDS):
                steady_frames = 0  # a request: back to full speed until steady again
            self._wake.clear()
            ramp = min(1.0, (steps - ramp_start) / RAMP_STEPS)
            if ramp < 1.0:
                batch = min(batch, RAMP_BATCH)
            lattice.set_params(self._params(lattice, ramp=ramp))
            started = time.perf_counter()
            lattice.step(batch, thermal_steps=catch_up)
            lattice.finish()
            spent = max(time.perf_counter() - started, 1e-6)
            steps += batch
            mlups = lattice.grid.cells * batch / spent / 1e6
            batch = int(min(2000, max(1, batch * BATCH_SECONDS / spent)))
            now = time.monotonic()
            if now - last_frame < max(FRAME_SECONDS, sample_cost / SAMPLE_SHARE):
                continue
            last_frame = now
            frame, swing, nan = self._sample(lattice, cells_of_port, steps, mlups, geometry_version)
            sample_cost = time.monotonic() - now
            assert self._scaling is not None
            laminar = not self._scaling.les
            # **A frame is judged once the inlets have ramped and the flow had time to settle**
            # (ADR-0047 addendum 5); NaN at any time. A frame with a sign of a runaway is not
            # valid: it says so, and it is never part of a steady window.
            judged = (now >= settled_from and steps - ramp_start >= RAMP_STEPS) or nan
            cell_steps = float(max(steps - sampled_at, 1)) * max(self._fluid_cells, 1)
            sampled_at = steps
            sign = (
                instability(
                    frame.dp,
                    frame.flow_in,
                    frame.flow_out,
                    swing,
                    frame.guard,
                    cell_steps,
                    nan,
                    before=list(health.imbalances),
                    reynolds=self._scaling.reynolds,
                )
                if judged
                else ""
            )
            frame.unstable, frame.instability = bool(sign), sign
            if sign:
                history.clear()
                flowing.clear()
            guarding = guarding + 1 if (frame.guard > 0 or nan) and now >= settled_from else 0
            if laminar and (nan or guarding >= GUARD_FRAMES):
                log.info("Fast run switches on the LES: the laminar lattice needed the guard")
                self._force_les = True
                lattice, _ = self._apply(device, lattice, self._case, speed, "")
                lattice.reset()
                self._from_rest()
                lattice.classify(self._params(lattice, init=True))
                history.clear()
                flowing.clear()
                health.clear()
                guarding = 0
                settled_from = time.monotonic() + SETTLE_SECONDS
                ramp_start = steps
                continue
            if judged and health.judge(sign, frame.imbalance):
                # **A runaway is recovered from, or the run fails**: never reported as valid.
                reason = health.reason() or sign
                if self._warm_target is not None:
                    # The coarse pass of a warm start ran away: go to the asked-for cells.
                    log.info(
                        "Fast run skips its coarse pass: it ran away", extra=fields(reason=reason)
                    )
                    promote = True
                    health = Health()
                    with self._lock:
                        self._frame = frame
                    self._emit("fast.frame", **frame.to_json())
                    continue
                slower = max(SPEED_MIN, 0.5 * speed)
                if health.spent or slower >= speed or not self._slows(lattice, slower):
                    # **A restart that would replay the same lattice is not made**: a viscous
                    # flow's lattice speed is already held below the asked-for one
                    # (``NU_LATTICE_MAX``), so halving the latter changes nothing.
                    with self._lock:
                        self._frame = frame
                    self._emit("fast.frame", **frame.to_json())
                    raise self._unstable(lattice, reason, speed, health.recoveries)
                log.info(
                    "Fast run restarts slower: the lattice ran away",
                    extra=fields(reason=reason, speed=speed),
                )
                self._emit(
                    "fast.warning",
                    code=FastUnstable.code,
                    message=f"the lattice went unstable ({_SIGNS[reason]}): "
                    "it starts again from rest at half the lattice speed",
                )
                health.restart()
                speed = slower
                swinging = guarding = 0
                lattice, _ = self._apply(device, lattice, self._case, speed, "")
                lattice.reset()
                self._from_rest()
                lattice.classify(self._params(lattice, init=True))
                history.clear()
                flowing.clear()
                settled_from = time.monotonic() + SETTLE_SECONDS
                ramp_start = steps
                with self._lock:
                    self._frame = frame
                self._emit("fast.frame", **frame.to_json())
                continue
            # **A core that speeds the flow up lowers the lattice speed**: the guard holds a cell
            # whose speed passes 0.3, and in a restrictive core the throats run many times the
            # inlet's speed (measured 2026-11-22 on a gyroid plate exchanger: more than six times,
            # 200,000 cells held at the inlet's 0.05). The speed is halved until the guard rests.
            # The density is no part of it: with the incompressible equilibrium it is the pressure
            # alone, and it is not capped.
            swinging = (
                swinging + 1 if frame.guard > 0 and not laminar and now >= settled_from else 0
            )
            if swinging >= RESTRICTIVE_FRAMES and speed > SPEED_MIN:
                log.info(
                    "Fast run lowers the lattice speed: the guard is holding the flow",
                    extra=fields(guard=frame.guard, swing=swing),
                )
                speed = max(SPEED_MIN, 0.5 * speed)
                swinging = 0
                health.clear()
                lattice, _ = self._apply(device, lattice, self._case, speed, "")
                lattice.reset()
                self._from_rest()
                history.clear()
                flowing.clear()
                settled_from = time.monotonic() + SETTLE_SECONDS
                ramp_start = steps
                continue
            frame.restrictive = speed < SPEED
            heat = sum(abs(s.get("heat", 0.0)) for s in frame.streams)
            history.append(Sample(now, steps, frame.dp, frame.flow_out, heat))
            flowing.append(Sample(now, steps, frame.dp, frame.flow_out))
            settled = steady(flowing, settle_steps(lattice.grid, self._scaling))
            frame.steady = not frame.unstable and steady(
                history, settle_steps(lattice.grid, self._scaling)
            )
            if frame.steady or now >= self._warmed_until:
                with self._lock:
                    self._warmed_from = None
            frame.warmed_from = self._warmed_from
            frame.fidelity = lattice.grid.fidelity
            frame.fluid_cells = self._fluid_cells
            if self._warm_target is not None:
                frame.warming = True
                frame.steady = False
                window = settle_steps(lattice.grid, self._scaling)
                promote = settled or steps - ramp_start >= RAMP_STEPS + WARM_CAP * window
            steady_frames = steady_frames + 1 if frame.steady else 0
            frame.holding = steady_frames >= HOLD_FRAMES
            catch_up = (
                THERMAL_CATCH_UP if settled and not frame.steady and self._case.thermal else 1
            )
            frame.catching_up = catch_up > 1
            with self._lock:
                self._frame = frame
            self._emit("fast.frame", **frame.to_json())

    def _slows(self, lattice: Any, speed: float) -> bool:  # Any: Lattice
        """Whether asking for the lattice speed ``speed`` lowers the one the lattice runs at."""
        assert self._scaling is not None and self._case is not None
        try:
            scaling = self._scale(lattice.grid.dx, self._case, speed)
        except StokesError:
            return False
        return scaling.inlet_lattice_speed < self._scaling.inlet_lattice_speed * (1.0 - 1e-6)

    def _scale(self, dx: float, case: FastCase, speed: float) -> Scaling:
        """The scaling of ``case`` on cells of size ``dx`` at the asked-for lattice speed."""
        flowing = any(zone.kind != "solid" for zone in case.all_zones)
        # Solids alone conduct: there is no flow to scale, and any step serves.
        return scaling_for(
            dx,
            case.fastest if flowing else 1.0,
            case.nu if flowing else dx,
            speed,
            les=self._force_les,
            closure=None if self._turbulence == AUTO else self._turbulence,
            zones=[
                (case.fastest_in(index), zone.nu)
                for index, zone in enumerate(case.all_zones)
                if zone.kind != "solid"
            ],
            diffusivity=fluid_diffusivity(case) if case.thermal else 0.0,
        )

    def _unstable(
        self,
        lattice: Any,  # Any: Lattice
        reason: str,
        speed: float,
        restarts: int,
    ) -> FastUnstable:
        """The failure of a run that ran away and could not be brought back."""
        assert self._scaling is not None
        fidelity = lattice.grid.fidelity
        return FastUnstable(
            f"the lattice went unstable at fidelity {fidelity} ({_SIGNS[reason]}; cell Reynolds "
            f"number {self._scaling.reynolds:.3g}, tau {self._scaling.tau:.5f}) and restarting "
            "it slower did not help, so its numbers are not valid: raise the fidelity or lower "
            "the inlet speed",
            details={
                "reason": reason,
                "fidelity": fidelity,
                "cell_reynolds": self._scaling.reynolds,
                "tau": self._scaling.tau,
                "lattice_speed": speed,
                "restarts": restarts,
            },
        )

    def _apply(
        self, device: Any, lattice: Any, case: FastCase, speed: float, geometry_key: str
    ) -> tuple[Any, bool]:  # Any: wgpu device and Lattice
        """Bring the lattice in line with ``case``: the same grid kept, a new one built."""
        from stokes.fast.device import Lattice
        from stokes.fast.grid import MAX_CELLS, cell_limit

        limit = MAX_CELLS
        if self._adapter.get("max_binding"):
            # The populations are one buffer, and a buffer binding has a largest size.
            limit = cell_limit(int(self._adapter["max_binding"]), self._storage)
        if case.fastest <= 0.0 and any(zone.kind != "solid" for zone in case.all_zones):
            raise FastInvalid("the case has no inlet with a speed; give an inlet a velocity")
        # **The fidelity rises on its own when the cells are too coarse for the flow** (as Discovery
        # picks its resolution): the first fidelity from the asked-for one up whose lattice is
        # stable; refused only when even the finest is not.
        refused: StokesError | None = None
        for fidelity in range(self._fidelity, 6):
            grid = grid_for(case.low, case.high, fidelity, max_cells=limit)
            try:
                scaling = self._scale(grid.dx, case, speed)
            except FastTooFast as exc:
                refused = exc
                continue
            break
        else:
            assert refused is not None
            raise refused
        rebuilt = lattice is None or lattice.grid != grid
        previous: Any = None  # Any: the Lattice a new grid replaces
        before = self._scaling
        if rebuilt:
            previous, lattice = lattice, Lattice(device, grid, self._storage)
        reference = min((p.pressure for p in case.outlets), default=0.0)
        densities = {p.name: scaling.density(p.pressure - reference) - 1.0 for p in case.outlets}
        same_geometry = not rebuilt and case.geometry_key == geometry_key
        # The gains are the last classification's; a new one calibrates them before it steps.
        del densities
        with self._lock:
            self._reference = reference
        table, masks = self._pack(case, scaling)
        # A value-only edit (a speed, a pressure) rewrites the port table; a new field recompiles.
        texts = zone_texts(case)
        key = repr((texts, case.thermal))
        if rebuilt or lattice.field_text != key or not lattice.update_ports(table, masks):
            lattice.load(key, table, masks, zones=texts, thermal=case.thermal)
        params = _params(lattice.grid, scaling, case)
        lattice.set_params(params)
        self._flags = None
        if not same_geometry:
            lattice.classify(_params(lattice.grid, scaling, case, init=rebuilt))
            cut = self._cut(lattice, case)
            if cut is not None:
                return self._refuse(
                    device, lattice, previous, rebuilt, case, speed, geometry_key, cut
                )
        self._warmed = rebuilt and self._replace(previous, before, lattice, scaling, case)
        with self._lock:
            self._grid = grid
            self._scaling = scaling
            self._reference = reference
        return lattice, rebuilt

    def _refuse(
        self,
        device: Any,  # Any: wgpu device
        lattice: Any,  # Any: Lattice
        previous: Any,  # Any: Lattice or None
        rebuilt: bool,
        case: FastCase,
        speed: float,
        geometry_key: str,
        cut: FastInvalid,
    ) -> tuple[Any, bool]:  # Any: Lattice
        """**A fluid in pieces is refused, never run** (ADR-0047 addendum 5). A new lattice is
        dropped (the one it replaced runs on); a lattice reclassified for an edit goes back to the
        last case. The coarse pass of a warm start may close a throat the asked-for fidelity
        resolves: it is skipped, and that fidelity is judged instead."""
        if rebuilt:
            lattice.destroy()
        if self._warm_target is not None and lattice.grid.fidelity < self._warm_target:
            with self._lock:
                self._fidelity, self._warm_target = self._warm_target, None
            return self._apply(device, previous if rebuilt else lattice, case, speed, geometry_key)
        if not rebuilt and self._case is not None and self._case is not case:
            self._apply(device, lattice, self._case, speed, "")
        raise cut

    def _cut(self, lattice: Any, case: FastCase) -> FastInvalid | None:  # Any: Lattice
        """Read the flags the classification just wrote (kept in ``_flags``) and refuse a fluid
        whose inlets and outlets they do not join (``stokes.fast.zones.disconnected``)."""
        flags = lattice.read_flags()
        grid = lattice.grid
        shaped = (flags & 0xFF).reshape(grid.nz, grid.ny, grid.nx)
        cut = disconnected(shaped, _port_cells(flags, len(case.ports)), case, grid.fidelity)
        if cut is None:
            self._flags = flags
        return cut

    def _from_rest(self) -> None:
        """The lattice was put back to rest (it went NaN, the LES took over, or the lattice speed
        was lowered): whatever flow a warm start gave it is gone, and the status stops saying so."""
        with self._lock:
            self._warmed_from = None

    def _replace(
        self,
        previous: Any,  # Any: Lattice or None
        before: Scaling | None,
        lattice: Any,  # Any: Lattice
        scaling: Scaling,
        case: FastCase,
    ) -> bool:
        """A new grid takes over: it starts from the old one's solution where it can (True), the
        old one is destroyed, and the old grid's cell counts are dropped (they are not this
        grid's; the loop reads this grid's cells next)."""
        warmed = previous is not None and self._start_from(previous, before, lattice, scaling, case)
        came_from = previous.grid.fidelity if warmed else None
        if previous is not None:
            previous.destroy()
        with self._lock:
            self._warmed_from = came_from
            self._warmed_until = time.monotonic() + WARMED_SECONDS
            self._fluid_cells = 0
            self._resolution = []
        return warmed

    def _start_from(
        self,
        previous: Any,  # Any: Lattice
        before: Scaling | None,
        lattice: Any,  # Any: Lattice
        scaling: Scaling,
        case: FastCase,
    ) -> bool:
        """**The warm start**: give a new lattice the solution of the one it replaces (the same
        case on other cells), not rest. False when there is nothing to take: no warm start, a
        lattice that never stepped, or other bodies."""
        if (
            not self._warm_start
            or before is None
            or previous.steps == 0
            or self._case is None
            or not _alike(self._case, case)
        ):
            return False
        # A lattice velocity is worth ``dx / dt`` m/s, a density deviation its square.
        ratio = before.speed / scaling.speed
        lattice.prolong(
            previous, ratio, ratio * ratio, _params(lattice.grid, scaling, case, start=True)
        )
        return True

    def _calibrate(
        self,
        lattice: Any,  # Any: Lattice
        case: FastCase,
        flags: NDArray[np.uint32],
        cells_of_port: list[NDArray[np.uint32]],
    ) -> None:
        """Set every inlet's gain from the cells the classification gave it, so that the cells
        deliver the port's own area times its speed."""
        assert self._scaling is not None
        shares = [(flags[cells] >> 16).astype(np.float64) / 65535.0 for cells in cells_of_port]
        self._gains = inlet_gains(list(case.ports), shares, lattice.grid.dx)
        table, masks = self._pack(case, self._scaling)
        lattice.update_ports(table, masks)

    def _pack(
        self, case: FastCase, scaling: Scaling
    ) -> tuple[NDArray[np.float32], NDArray[np.uint32]]:
        """The port table with the zones' rows behind it, and the masks."""
        densities = {
            p.name: scaling.density(p.pressure - self._reference) - 1.0 for p in case.outlets
        }
        return pack_ports(
            list(case.ports),
            scaling.speed,
            densities,
            gains=self._gains,
            zone_rows=zone_rows(case, scaling),
            reference_temperature=reference_temperature(case),
            cell_size=scaling.dx,
        )

    def _params(self, lattice: Any, init: bool = False, ramp: float = 1.0) -> bytes:  # Any: Lattice
        assert self._scaling is not None and self._case is not None
        return _params(lattice.grid, self._scaling, self._case, init=init, ramp=ramp)

    def _sample(
        self,
        lattice: Any,  # Any: Lattice
        cells_of_port: list[NDArray[np.uint32]],
        steps: int,
        mlups: float,
        geometry_version: int,
    ) -> tuple[Frame, float, bool]:
        """A frame from the ports and the display plane; and the ports' density swing; and NaN."""
        case, scaling, grid = self._case, self._scaling, lattice.grid
        assert case is not None and scaling is not None
        guard, nan = lattice.take_counters()
        inlet_p: list[float] = []
        outlet_p: list[float] = []
        flow_in = flow_out = inlet_area = 0.0
        swing = 0.0
        samples: list[PortSample] = []
        for port, cells in zip(case.ports, cells_of_port, strict=False):
            if cells.size == 0:
                continue
            got, warmth = lattice.gather_all(cells)
            sample = PortSample(port, got[:, :3], got[:, 3], warmth)
            samples.append(sample)
            excess = float(got[:, 3].mean())  # the density's deviation from 1
            swing = max(swing, abs(excess))
            pressure = self._reference + scaling.pressure(1.0 + excess)
            through = port_flow(sample, scaling)
            if port.kind == OUTLET:
                outlet_p.append(pressure)
                flow_out += through
            else:
                inlet_p.append(pressure)
                flow_in -= through
                inlet_area += port.area
        dp = (float(np.mean(inlet_p)) - float(np.mean(outlet_p))) if inlet_p and outlet_p else 0.0
        with self._lock:
            display = self._display
        axis, plane = _plane_of(grid, case, display)
        both = lattice.sample_plane(
            axis, plane, _params(grid, scaling, case, axis=axis, plane=plane)
        )
        positions, triangles, values = plane_mesh(
            grid, axis, plane, both[0], scaling, self._reference
        )
        warm_positions = warm_triangles = None
        if case.thermal:
            warm_positions, warm_triangles, values["T"] = thermal_mesh(
                grid, axis, plane, both[1], reference_temperature(case)
            )
        frame = Frame(
            step=steps,
            mlups=mlups,
            dp=dp,
            dp_pa=dp * self.density,
            inlet_speed=flow_in / inlet_area if inlet_area > 0.0 else 0.0,
            flow_in=flow_in,
            flow_out=flow_out,
            guard=guard,
            geometry_version=geometry_version,
            axis=axis,
            plane=plane,
            positions=positions,
            triangles=triangles,
            values=values,
            thermal_positions=warm_positions,
            thermal_triangles=warm_triangles,
            streams=streams(case, samples, scaling, self._reference),
        )
        return frame, swing, nan > 0


def _params(
    grid: Grid,
    scaling: Scaling,
    case: FastCase,
    init: bool = False,
    axis: int = 2,
    plane: int = 0,
    ramp: float = 1.0,
    start: bool = False,
) -> bytes:
    from stokes.fast.device import WALLS_INTERPOLATED, params_bytes
    from stokes.fast.lattice import CLOSURES, MEAN_STEPS, closure_constant

    smag = closure_constant(scaling.closure) if scaling.les else 0.0
    return params_bytes(
        grid,
        scaling,
        WALLS_INTERPOLATED,
        len(case.ports),
        init=init,
        axis=axis,
        plane=plane,
        ramp=ramp,
        smag=smag,
        closure=CLOSURES[scaling.closure],
        ema=1.0 / MEAN_STEPS,
        zones=len(case.all_zones),
        thermal=case.thermal,
        start=start,
    )


def _alike(one: FastCase, other: FastCase) -> bool:
    """Whether two cases have the same bodies in the same order, so a cell's code means the
    same body in both and one's solution can start the other."""
    names = [(z.name, z.kind) for z in one.all_zones]
    return names == [(z.name, z.kind) for z in other.all_zones] and one.thermal == other.thermal


def _port_cells(flags: NDArray[np.uint32], ports: int) -> list[NDArray[np.uint32]]:
    """The fluid cells next to each port, from the flags' second byte (port index + 1)."""
    adjacency = (flags >> 8) & 0xFF
    return [np.flatnonzero(adjacency == k + 1).astype(np.uint32) for k in range(ports)]


def _plane_of(grid: Grid, case: FastCase, display: Display) -> tuple[int, int]:
    """The plane's axis and cell index: the asked-for one, else the mid-plane normal to the
    fluid box's shortest side."""
    sides = [case.high[a] - case.low[a] for a in range(3)]
    axis = display.axis if display.axis is not None else int(np.argmin(sides))
    counts = (grid.nx, grid.ny, grid.nz)
    if display.offset is None:
        position = 0.5 * (case.low[axis] + case.high[axis])
    else:
        position = display.offset
    index = int((position - grid.origin[axis]) / grid.dx)
    return axis, min(max(index, 0), counts[axis] - 1)
