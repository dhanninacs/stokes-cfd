"""The GPU side of Fast mode (ADR-0047 decision 1): the only module that imports ``wgpu``.

``open_device`` finds a hardware adapter (Vulkan, Direct3D 12 or Metal) and refuses a software
one: a CPU-emulated GPU would be slower than the real solve. ``Lattice`` owns the buffers of one
grid and runs the shaders of ``stokes.fast.wgsl`` and ``stokes.fast.kernel``. Every method blocks
until the GPU has finished what it asked for, so it is called from the Fast runner's thread only,
never the server's.

The populations are one buffer, read and written in place (``stokes.fast.inplace``), in f32 or in
16 bits. A batch of steps is one compute pass that reads its params from a buffer written before
it, so what changes with every step (its parity, and the seed of a 16-bit store's rounding)
comes from a table of step numbers the step's bind group reads at a dynamic offset.

The module is longer than most because ``wgpu`` may be imported nowhere else: every buffer,
pipeline and bind group of a grid is made, used and destroyed here.
"""

from __future__ import annotations

import os
import struct
from dataclasses import dataclass
from typing import Any

import numpy as np
from numpy.typing import NDArray

from stokes.fast.errors import FastUnavailable
from stokes.fast.grid import Grid
from stokes.fast.kernel import step_shader
from stokes.fast.lattice import Q, Scaling
from stokes.fast.ports import zone_base
from stokes.fast.wgsl import (
    CLOCK_STEPS,
    classify_shader,
    compile_field,
    gather_shader,
    population_bytes,
    prolong_shader,
    slice_shader,
    thermal_shader,
)

#: Set to anything but 0 to switch Fast mode off (a shared machine, a driver that misbehaves):
#: every ``fast.*`` command then answers ``fast.unavailable``, as on a computer with no GPU.
DISABLE_ENV = "STOKES_FAST_DISABLE"
#: Workgroup size of the cell-wise shaders, and the most workgroups along one dispatch axis.
GROUP = 64
MAX_GROUPS = 65535
#: Interpolated (Bouzidi) walls; 0 is half-way bounce-back.
WALLS_INTERPOLATED = 1


@dataclass(frozen=True)
class AdapterInfo:
    name: str
    backend: str
    kind: str
    max_binding: int
    f16: bool = False
    """Whether shaders may store half-precision numbers (the ``shader-f16`` feature)."""

    def to_json(self) -> dict[str, Any]:  # Any: JSON
        return {
            "name": self.name,
            "backend": self.backend,
            "kind": self.kind,
            "max_binding": self.max_binding,
            "f16": self.f16,
        }


def open_device() -> tuple[Any, AdapterInfo]:  # Any: a wgpu GPUDevice
    """A device on the best hardware adapter, or ``FastUnavailable`` saying why there is none."""
    if os.environ.get(DISABLE_ENV, "").strip() not in ("", "0"):
        raise FastUnavailable(
            f"Fast mode is switched off on this computer ({DISABLE_ENV} is set)",
            details={"reason": "disabled"},
        )
    try:
        import wgpu
    except ImportError as exc:
        raise FastUnavailable(
            "Fast mode needs the wgpu package: install Stokes with the 'fast' extra",
            details={"reason": "no_package"},
        ) from exc
    try:
        adapter = wgpu.gpu.request_adapter_sync(power_preference="high-performance")
    except Exception as exc:  # wgpu raises its own error types for a missing driver
        raise FastUnavailable(
            f"no GPU adapter could be opened: {exc}", details={"reason": "no_adapter"}
        ) from exc
    if adapter is None:
        raise FastUnavailable("this computer has no GPU adapter", details={"reason": "no_adapter"})
    info = adapter.info
    kind = str(info.get("adapter_type", ""))
    if "cpu" in kind.lower():
        raise FastUnavailable(
            "the only GPU adapter is a software one; Fast mode needs a real GPU",
            details={"reason": "software_adapter", "adapter": str(info.get("device", ""))},
        )
    limits = adapter.limits
    binding = int(limits["max-storage-buffer-binding-size"])
    half = "shader-f16" in adapter.features
    device = adapter.request_device_sync(
        required_features=["shader-f16"] if half else [],
        required_limits={
            "max-storage-buffer-binding-size": binding,
            "max-buffer-size": int(limits["max-buffer-size"]),
            "max-storage-buffers-per-shader-stage": max(
                8, int(limits["max-storage-buffers-per-shader-stage"])
            ),
        },
    )
    return device, AdapterInfo(
        name=str(info.get("device", "")),
        backend=str(info.get("backend_type", "")),
        kind=kind,
        max_binding=binding,
        f16=half,
    )


def params_bytes(
    grid: Grid,
    scaling: Scaling,
    walls: int,
    ports: int,
    init: bool = False,
    axis: int = 2,
    plane: int = 0,
    guard: bool = True,
    ramp: float = 1.0,
    smag: float = 0.0,
    closure: int = 0,
    ema: float = 0.0,
    zones: int = 1,
    thermal: bool = False,
    start: bool = False,
) -> bytes:
    """The ``Params`` block of ``stokes.fast.wgsl``: lengths in mm, as the field has them.
    ``init`` is a classification that starts every cell at rest; ``start`` a step that builds the
    populations from the cells' velocities (``Lattice.prolong``)."""
    return struct.pack(
        "<4I2f2I4f4I2fIf4I",
        grid.nx,
        grid.ny,
        grid.nz,
        grid.cells,
        scaling.omega_plus,
        scaling.omega_minus,
        walls,
        ports,
        grid.origin[0] * 1e3,
        grid.origin[1] * 1e3,
        grid.origin[2] * 1e3,
        grid.dx * 1e3,
        2 if start else (1 if init else 0),
        axis,
        plane,
        1 if guard else 0,
        ramp,
        smag,
        closure,
        ema,
        zone_base(ports),
        zones,
        1 if thermal else 0,
        0,
    )


class Lattice:
    """The populations, flags and port tables of one grid, and the shaders that work on them.
    ``storage`` is how a population is kept: ``f32``, or ``f16`` (half the memory; the device
    must have the ``shader-f16`` feature)."""

    def __init__(self, device: Any, grid: Grid, storage: str = "f32") -> None:  # Any: GPUDevice
        import wgpu

        self._wgpu = wgpu
        self.device = device
        self.grid = grid
        self.storage = storage
        cells = grid.cells
        kept = wgpu.BufferUsage.STORAGE
        # One set of populations, read and written in place (``stokes.fast.inplace``); a buffer's
        # size is a multiple of four bytes.
        size = (Q * cells * population_bytes(self.storage) + 3) // 4 * 4
        self.f = device.create_buffer(size=size, usage=kept | wgpu.BufferUsage.COPY_DST)
        # The clock (``stokes.fast.wgsl.CLOCK``): a table of step numbers, one block a step of the
        # cycle, bound at a dynamic offset so each step of a batch reads its own.
        self._stride = max(16, int(device.limits["min-uniform-buffer-offset-alignment"]))
        table = np.zeros((CLOCK_STEPS, self._stride // 4), dtype=np.uint32)
        table[:, 0] = np.arange(CLOCK_STEPS, dtype=np.uint32)
        self.clock = device.create_buffer_with_data(data=table, usage=wgpu.BufferUsage.UNIFORM)
        self._clock = 0
        """Lattice steps taken (mod 2^32): its lowest bit is the next step's parity, and the
        whole of it seeds a 16-bit store's rounding."""
        self.flags = device.create_buffer(size=cells * 4, usage=kept | wgpu.BufferUsage.COPY_SRC)
        self.params = device.create_buffer(
            size=96, usage=wgpu.BufferUsage.UNIFORM | wgpu.BufferUsage.COPY_DST
        )
        self.slice_params = device.create_buffer(
            size=96, usage=wgpu.BufferUsage.UNIFORM | wgpu.BufferUsage.COPY_DST
        )
        # Each cell's velocity and density (the step writes them; the samples, the plane, the
        # temperature and the closures read them), its stress's running mean, and the
        # temperature in two buffers read and written in turn.
        self.vel = device.create_buffer(size=cells * 16, usage=kept | wgpu.BufferUsage.COPY_DST)
        self.mean = device.create_buffer(size=cells * 4, usage=kept | wgpu.BufferUsage.COPY_DST)
        self.t = [
            device.create_buffer(size=cells * 4, usage=kept | wgpu.BufferUsage.COPY_DST)
            for _ in range(2)
        ]
        self.tcurrent = 0
        self.thermal = False
        self._thermal: Any = None
        self._thermal_groups: list[Any] = []
        self.counters = device.create_buffer(
            size=8, usage=kept | wgpu.BufferUsage.COPY_SRC | wgpu.BufferUsage.COPY_DST
        )
        self.ports: Any = None
        self.masks: Any = None
        self.field_text = ""
        self.steps = 0
        """Lattice steps taken on this grid."""
        self._prolong: Any = None
        self._step_group: Any = None
        self._step: Any = None
        self._classify: Any = None
        self._slice = device.create_compute_pipeline(
            layout="auto",
            compute={
                "module": device.create_shader_module(code=slice_shader()),
                "entry_point": "main",
            },
        )
        self._gather = device.create_compute_pipeline(
            layout="auto",
            compute={
                "module": device.create_shader_module(code=gather_shader()),
                "entry_point": "main",
            },
        )
        self._params_value = b""

    # ------------------------------------------------------------------ set-up

    def update_ports(self, ports: NDArray[np.float32], masks: NDArray[np.uint32]) -> bool:
        """Rewrite the port tables in place when their sizes are unchanged (a speed or a pressure
        edited); False when they need ``load``."""
        if (
            self.ports is None
            or self.ports.size != ports.nbytes
            or self.masks is None
            or self.masks.size != masks.nbytes
        ):
            return False
        self.device.queue.write_buffer(self.ports, 0, np.ascontiguousarray(ports, np.float32))
        self.device.queue.write_buffer(self.masks, 0, np.ascontiguousarray(masks, np.uint32))
        return True

    def load(
        self,
        field_text: str,
        ports: NDArray[np.float32],
        masks: NDArray[np.uint32],
        zones: list[tuple[str, bool]] | None = None,
        thermal: bool = False,
    ) -> None:
        """Compile the zones' fields into the classify and step shaders and upload the port
        tables. ``zones`` holds each zone's field text and whether it is a solid; without it
        ``field_text`` is the one fluid. ``thermal`` adds the temperature's step."""
        wgpu = self._wgpu
        device = self.device
        texts = zones if zones is not None else [(field_text, False)]
        field = [(compile_field(text), solid) for text, solid in texts]
        self.thermal = thermal
        storage = wgpu.BufferUsage.STORAGE | wgpu.BufferUsage.COPY_DST
        for old in (self.ports, self.masks):
            if old is not None:
                old.destroy()
        self.ports = device.create_buffer_with_data(
            data=np.ascontiguousarray(ports, np.float32), usage=storage
        )
        self.masks = device.create_buffer_with_data(
            data=np.ascontiguousarray(masks, np.uint32), usage=storage
        )
        self.field_text = field_text
        nx, ny = self.grid.nx, self.grid.ny
        self._classify = device.create_compute_pipeline(
            layout="auto",
            compute={
                "module": device.create_shader_module(
                    code=classify_shader(field, nx, ny, self.storage)
                ),
                "entry_point": "main",
            },
        )
        # The step's layout is written out, not derived: its clock is bound at a dynamic offset.
        kinds = wgpu.BufferBindingType
        bindings = [
            kinds.storage,  # the populations
            kinds.read_only_storage,  # the flags
            kinds.uniform,  # the params
            kinds.read_only_storage,  # the ports
            kinds.read_only_storage,  # the masks
            kinds.storage,  # the counters
            kinds.storage,  # the velocities
            kinds.storage,  # the stress's mean
        ]
        layout = device.create_bind_group_layout(
            entries=[
                *(
                    {
                        "binding": index,
                        "visibility": wgpu.ShaderStage.COMPUTE,
                        "buffer": {"type": kind},
                    }
                    for index, kind in enumerate(bindings)
                ),
                {
                    "binding": 8,
                    "visibility": wgpu.ShaderStage.COMPUTE,
                    "buffer": {"type": kinds.uniform, "has_dynamic_offset": True},
                },
            ]
        )
        self._step = device.create_compute_pipeline(
            layout=device.create_pipeline_layout(bind_group_layouts=[layout]),
            compute={
                "module": device.create_shader_module(
                    code=step_shader(field, nx, ny, self.storage)
                ),
                "entry_point": "main",
            },
        )
        self._step_group = device.create_bind_group(
            layout=layout,
            entries=[
                _entry(0, self.f),
                _entry(1, self.flags),
                _entry(2, self.params),
                _entry(3, self.ports),
                _entry(4, self.masks),
                _entry(5, self.counters),
                _entry(6, self.vel),
                _entry(7, self.mean),
                {"binding": 8, "resource": {"buffer": self.clock, "offset": 0, "size": 16}},
            ],
        )
        self._thermal = device.create_compute_pipeline(
            layout="auto",
            compute={
                "module": device.create_shader_module(code=thermal_shader(nx, ny)),
                "entry_point": "main",
            },
        )
        layout = self._thermal.get_bind_group_layout(0)
        self._thermal_groups = [
            device.create_bind_group(
                layout=layout,
                entries=[
                    _entry(0, self.t[a]),
                    _entry(1, self.t[1 - a]),
                    _entry(2, self.flags),
                    _entry(3, self.params),
                    _entry(4, self.ports),
                    _entry(5, self.masks),
                    _entry(6, self.vel),
                ],
            )
            for a in (0, 1)
        ]

    def set_params(self, value: bytes) -> None:
        if value != self._params_value:
            self.device.queue.write_buffer(self.params, 0, value)
            self._params_value = value

    def classify(self, init_params: bytes) -> None:
        """Re-classify every cell from the field; with ``init`` in the params, start at rest."""
        self.device.queue.write_buffer(self.params, 0, init_params)
        group = self.device.create_bind_group(
            layout=self._classify.get_bind_group_layout(0),
            entries=[
                _entry(0, self.f),
                _entry(1, self.flags),
                _entry(2, self.params),
                _entry(3, self.ports),
                _entry(4, self.masks),
                _entry(5, self.t[self.tcurrent]),
                _entry(6, self.vel),
                _entry(7, self.mean),
            ],
        )
        self._run(self._classify, group, self._groups(self.grid.cells))
        # Back to the stepping params; until they are set, the classify params stay (nothing steps).
        if self._params_value:
            self.device.queue.write_buffer(self.params, 0, self._params_value)

    def reset(self) -> None:
        """Every population back to rest: the buffers are cleared on the GPU. (They were filled
        from a host array of nineteen floats a cell: 2 GB of host memory and as much again to
        upload at 28 million cells, on every start, fidelity change and recovery.)"""
        encoder = self.device.create_command_encoder()
        for buffer in (self.f, self.vel, self.mean):
            encoder.clear_buffer(buffer)
        self.device.queue.submit([encoder.finish()])

    # ------------------------------------------------------------------ stepping and sampling

    def step(self, count: int, thermal_steps: int = 1) -> None:
        """``count`` lattice steps; with the temperature solved, each is followed by
        ``thermal_steps`` of its own (more than one lets a steady flow's temperature catch up)."""
        gx, gy = self._groups(self.grid.cells)
        remaining = count
        while remaining > 0:
            # One pass takes no more steps than the clock has blocks; it starts at block 0 or
            # 1, whichever has the parity of the steps taken, and the params' last word (after
            # ``set_params``, which writes 0) is the step count at block 0.
            first = self._clock & 1
            steps = min(remaining, CLOCK_STEPS - first)
            if self._params_value:
                base = (self._clock - first) & 0xFFFFFFFF
                self.device.queue.write_buffer(self.params, 92, struct.pack("<I", base))
            encoder = self.device.create_command_encoder()
            compute = encoder.begin_compute_pass()
            if not self.thermal:
                compute.set_pipeline(self._step)
            for block in range(first, first + steps):
                if self.thermal:
                    compute.set_pipeline(self._step)
                # The step's own block of the clock: its parity and, with the base, its draws.
                compute.set_bind_group(0, self._step_group, [block * self._stride])
                compute.dispatch_workgroups(gx, gy, 1)
                if self.thermal:
                    # The temperature's step, with the velocities the flow's step just wrote.
                    compute.set_pipeline(self._thermal)
                    for _ in range(thermal_steps):
                        compute.set_bind_group(0, self._thermal_groups[self.tcurrent])
                        compute.dispatch_workgroups(gx, gy, 1)
                        self.tcurrent = 1 - self.tcurrent
            compute.end()
            self.device.queue.submit([encoder.finish()])
            self._clock = (self._clock + steps) & 0xFFFFFFFF
            remaining -= steps
        self.steps += count

    def prolong(self, other: Lattice, velocity: float, density: float, start: bytes) -> None:
        """Start this lattice from ``other``'s solution of the same case (the warm start, ADR-0047
        addendum 4): its velocity, density and temperature interpolated to this lattice's cells,
        then the populations built from them. ``velocity`` and ``density`` are what the other
        lattice's velocity and density deviation are worth in this one's units; ``start`` is this
        lattice's params with ``start`` set. Call it after ``classify``."""
        wgpu = self._wgpu
        if self._prolong is None:
            self._prolong = self.device.create_compute_pipeline(
                layout="auto",
                compute={
                    "module": self.device.create_shader_module(code=prolong_shader()),
                    "entry_point": "main",
                },
            )
        grid = other.grid
        block = self.device.create_buffer_with_data(
            data=struct.pack(
                "<4I8f",
                grid.nx,
                grid.ny,
                grid.nz,
                0,
                grid.origin[0] * 1e3,
                grid.origin[1] * 1e3,
                grid.origin[2] * 1e3,
                grid.dx * 1e3,
                velocity,
                density,
                0.0,
                0.0,
            ),
            usage=wgpu.BufferUsage.UNIFORM,
        )
        group = self.device.create_bind_group(
            layout=self._prolong.get_bind_group_layout(0),
            entries=[
                _entry(0, other.vel),
                _entry(1, other.flags),
                _entry(2, other.t[other.tcurrent]),
                _entry(3, self.flags),
                _entry(4, self.vel),
                _entry(5, self.t[self.tcurrent]),
                _entry(6, self.params),
                _entry(7, block),
            ],
        )
        self._run(self._prolong, group, self._groups(self.grid.cells))
        # The populations are built as what an even step leaves, and the clock moves on.
        self.device.queue.write_buffer(self.params, 0, start)
        self._run(self._step, self._step_group, self._groups(self.grid.cells), [0])
        self._clock = 1
        if self._params_value:
            self.device.queue.write_buffer(self.params, 0, self._params_value)
        self.finish()
        block.destroy()

    def finish(self) -> None:
        """Wait for the queued steps: a tiny read waits for every submission before it."""
        self.device.queue.read_buffer(self.counters, 0, 8)

    def take_counters(self) -> tuple[int, int]:
        """(cells the guard acted on, cells that went NaN) since the last call; then zero them."""
        raw = np.frombuffer(self.device.queue.read_buffer(self.counters, 0, 8), dtype=np.uint32)
        self.device.queue.write_buffer(self.counters, 0, np.zeros(2, dtype=np.uint32))
        return int(raw[0]), int(raw[1])

    def read_flags(self) -> NDArray[np.uint32]:
        return np.frombuffer(self.device.queue.read_buffer(self.flags), dtype=np.uint32).copy()

    def sample_plane(self, axis: int, plane: int, params: bytes) -> NDArray[np.float32]:
        """``(2, n2, n1, 4)``: the plane's velocity and density deviation (1e30 for a cell that
        is not a fluid), then each cell's temperature (less the reference) and code."""
        wgpu = self._wgpu
        n1, n2 = _plane_dims(self.grid, axis)
        out = self.device.create_buffer(
            size=2 * n1 * n2 * 16, usage=wgpu.BufferUsage.STORAGE | wgpu.BufferUsage.COPY_SRC
        )
        self.device.queue.write_buffer(self.slice_params, 0, params)
        group = self.device.create_bind_group(
            layout=self._slice.get_bind_group_layout(0),
            entries=[
                _entry(0, self.vel),
                _entry(1, self.flags),
                _entry(2, self.slice_params),
                _entry(3, out),
                _entry(4, self.t[self.tcurrent]),
            ],
        )
        self._run(self._slice, group, ((n1 + 7) // 8, (n2 + 7) // 8))
        data = (
            np.frombuffer(self.device.queue.read_buffer(out), dtype=np.float32)
            .reshape(2, n2, n1, 4)
            .copy()
        )
        out.destroy()
        return data

    def gather(self, cells: NDArray[np.uint32]) -> NDArray[np.float32]:
        """``(m, 4)``: velocity and the density's deviation from 1 at the listed cells."""
        return self.gather_all(cells)[0]

    def gather_all(
        self, cells: NDArray[np.uint32]
    ) -> tuple[NDArray[np.float32], NDArray[np.float32]]:
        """``(m, 4)`` as ``gather``, and ``(m,)``: the cells' temperatures less the reference."""
        wgpu = self._wgpu
        if cells.size == 0:
            return np.zeros((0, 4), dtype=np.float32), np.zeros(0, dtype=np.float32)
        ids = self.device.create_buffer_with_data(
            data=np.ascontiguousarray(cells, np.uint32), usage=wgpu.BufferUsage.STORAGE
        )
        out = self.device.create_buffer(
            size=2 * cells.size * 16, usage=wgpu.BufferUsage.STORAGE | wgpu.BufferUsage.COPY_SRC
        )
        group = self.device.create_bind_group(
            layout=self._gather.get_bind_group_layout(0),
            entries=[
                _entry(0, self.vel),
                _entry(1, ids),
                _entry(2, out),
                _entry(3, self.t[self.tcurrent]),
            ],
        )
        self._run(self._gather, group, ((cells.size + GROUP - 1) // GROUP, 1))
        data = (
            np.frombuffer(self.device.queue.read_buffer(out), dtype=np.float32)
            .reshape(2, -1, 4)
            .copy()
        )
        ids.destroy()
        out.destroy()
        return data[0], data[1, :, 0].copy()

    def destroy(self) -> None:
        for buffer in (
            self.f,
            self.flags,
            self.params,
            self.clock,
            self.slice_params,
            self.counters,
            self.vel,
            self.mean,
            *self.t,
            self.ports,
            self.masks,
        ):
            if buffer is not None:
                buffer.destroy()

    # ------------------------------------------------------------------ helpers

    def _groups(self, count: int) -> tuple[int, int]:
        groups = (count + GROUP - 1) // GROUP
        return min(groups, MAX_GROUPS), (groups + MAX_GROUPS - 1) // MAX_GROUPS

    def _run(
        self,
        pipeline: Any,  # Any: wgpu objects
        group: Any,
        size: tuple[int, int],
        offsets: list[int] | None = None,
    ) -> None:
        encoder = self.device.create_command_encoder()
        compute = encoder.begin_compute_pass()
        compute.set_pipeline(pipeline)
        compute.set_bind_group(0, group, offsets or [])
        compute.dispatch_workgroups(size[0], size[1], 1)
        compute.end()
        self.device.queue.submit([encoder.finish()])


def _entry(binding: int, buffer: Any) -> dict[str, Any]:  # Any: a wgpu buffer; JSON-like entry
    return {"binding": binding, "resource": {"buffer": buffer, "offset": 0, "size": buffer.size}}


def _plane_dims(grid: Grid, axis: int) -> tuple[int, int]:
    """The plane's (first, second) in-plane cell counts, in the slice shader's order."""
    if axis == 0:
        return grid.ny, grid.nz
    if axis == 1:
        return grid.nx, grid.nz
    return grid.nx, grid.ny
