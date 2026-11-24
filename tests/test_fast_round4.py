"""Fast mode, round 4 (ADR-0047 addendum 4), without a GPU: the closures' formulas hold the
properties their papers claim, and the options reach the lattice."""

from __future__ import annotations

import math

import numpy as np
import pytest

from stokes.fast import lattice
from stokes.fast.errors import FastInvalid
from stokes.fast.kernel import step_shader
from stokes.fast.runner import TURBULENCE, FastRunner
from stokes.fast.wgsl import compile_field

# ------------------------------------------------------------------------------ the closures


def _shear(rate: float) -> np.ndarray:
    """A pure shear: the x velocity grows along y."""
    g = np.zeros((3, 3))
    g[0, 1] = rate
    return g


@pytest.mark.parametrize("closure", ["wale", "vreman"])
def test_a_laminar_shear_has_no_eddy_viscosity(closure: str) -> None:
    """What the plain Smagorinsky model lacks: a duct's or a pipe's developed laminar flow is a
    shear in two directions, and both closures give it exactly none."""
    assert lattice.eddy_viscosity(closure, _shear(1e-3)) == 0.0
    duct = np.zeros((3, 3))
    duct[0, 1], duct[0, 2] = 2e-3, -7e-4  # u(y, z) along x
    assert lattice.eddy_viscosity(closure, duct) == pytest.approx(0.0, abs=1e-12)
    assert lattice.eddy_viscosity(closure, np.zeros((3, 3))) == 0.0


def test_the_closures_give_the_hand_worked_viscosity_of_a_plane_strain() -> None:
    """``diag(a, -a, 0)``: by hand, WALE is ``Cw^2 a (2/3)^1.5 / (2^2.5 + (2/3)^1.25)`` and Vreman
    ``c a / sqrt(2)``."""
    a = 2.5e-3
    strain = np.diag([a, -a, 0.0])
    wale = lattice.WALE**2 * a * (2 / 3) ** 1.5 / (2**2.5 + (2 / 3) ** 1.25)
    assert lattice.eddy_viscosity("wale", strain) == pytest.approx(wale, rel=1e-12)
    assert lattice.eddy_viscosity("vreman", strain) == pytest.approx(
        lattice.VREMAN * a / math.sqrt(2.0), rel=1e-12
    )


@pytest.mark.parametrize("closure", ["wale", "vreman"])
def test_an_eddy_viscosity_scales_with_the_gradient_and_ignores_the_frame(closure: str) -> None:
    """First degree in the gradient (so the lattice speed does not change the model), never
    negative, and the same in a frame turned about an axis."""
    rng = np.random.default_rng(4)
    g = rng.normal(size=(3, 3)) * 1e-3
    g -= np.trace(g) / 3.0 * np.eye(3)  # a solenoidal flow
    base = lattice.eddy_viscosity(closure, g)
    assert base > 0.0
    assert lattice.eddy_viscosity(closure, 1e-4 * g) == pytest.approx(1e-4 * base, rel=1e-9)
    c, s = math.cos(0.7), math.sin(0.7)
    turn = np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])
    assert lattice.eddy_viscosity(closure, turn @ g @ turn.T) == pytest.approx(base, rel=1e-9)
    with pytest.raises(FastInvalid, match="wale and vreman"):
        lattice.eddy_viscosity("smagorinsky", g)


def test_the_closures_are_offered_and_carry_their_own_constants() -> None:
    assert set(TURBULENCE) == {"auto", *lattice.CLOSURES}
    assert lattice.CLOSURES["wale"] == 2 and lattice.CLOSURES["vreman"] == 3
    assert lattice.closure_constant("wale") == pytest.approx(0.25)
    assert lattice.closure_constant("vreman") == pytest.approx(2.5 * 0.17**2)
    assert lattice.closure_constant("smagorinsky") == lattice.SMAGORINSKY_FACTOR
    for name in ("wale", "vreman"):
        assert lattice.scaling_for(1e-4, 0.5, 1e-6, closure=name).closure == name
        runner = FastRunner(lambda *_a, **_k: None)
        runner.configure(turbulence=name)
        assert runner.status()["turbulence"] == name


def test_the_step_takes_the_velocity_gradient_from_the_neighbours() -> None:
    """WALE and Vreman need the whole gradient, which the populations do not hold: central
    differences of the neighbours' velocities, a wall half-way with the fluid at rest on it."""
    step = step_shader([(compile_field("_0 var-x"), False)], 8, 8)
    assert "fn gradOf(id: u32, code: u32, uc: vec3f) -> mat3x3f" in step
    assert "var up = -uc; var um = -uc;" in step  # the mirrored velocity of a wall
    assert "te = t0 + 3.0 * nutOf(gradOf(id, code, u), P.closure, P.smag);" in step
    assert "if (P.closure == 1u) { mean[id] = kept; }" in step


# ------------------------------------------------------------------------------ the warm start


def test_a_new_lattice_is_started_from_the_same_bodies_only() -> None:
    """One lattice's solution starts another when a cell's code means the same body in both."""
    from stokes.fast.case import FastCase, Zone
    from stokes.fast.runner import _alike

    def case(*zones: Zone, thermal: bool = False) -> FastCase:
        return FastCase("_0 var-x", (0, 0, 0), (1, 1, 1), (), 1e-6, zones=zones, thermal=thermal)

    hot, cold = Zone("hot", "fluid", "_0 var-x", 1e-6), Zone("cold", "fluid", "_0 var-y", 1e-6)
    plate = Zone("plate", "solid", "_0 var-z")
    assert _alike(case(), case())
    assert _alike(case(hot, cold, plate), case(hot, cold, plate))
    assert not _alike(case(hot, cold, plate), case(cold, hot, plate))  # the codes would swap
    assert not _alike(case(hot, plate), case(hot, cold, plate))
    assert not _alike(case(hot, plate), case(hot, plate, thermal=True))


def test_the_prolongation_weighs_the_eight_cells_round_a_point_within_one_body() -> None:
    from stokes.fast.wgsl import prolong_shader

    text = prolong_shader()
    assert text.count("if ((cflags[cid] & 0xffu) == code)") == 8  # never across a wall
    assert "let w = (1.0 - t.x) * (1.0 - t.y) * (1.0 - t.z);" in text
    assert "let w = t.x * t.y * t.z;" in text
    # Another lattice's velocity and density deviation, in this lattice's units.
    assert "vec4f(v.xyz * (C.velocity / sum), v.w * (C.density / sum))" in text
    assert "if (sum <= 1e-6) { return; }" in text  # no cell of the body near: as classified


def test_a_start_from_velocities_is_its_own_mode_of_the_step() -> None:
    """``init_flag`` 2: the populations are the equilibrium of the cell's velocity and density
    plus what the velocity's strain gives at this lattice's relaxation time."""
    import struct

    from stokes.fast.device import params_bytes
    from stokes.fast.grid import grid_for

    grid = grid_for((0.0, 0.0, 0.0), (0.02, 0.01, 0.01), 1)
    scaling = lattice.scaling_for(grid.dx, 0.01, 1e-6)
    flag = [
        struct.unpack_from("<I", params_bytes(grid, scaling, 1, 0, **mode), 48)[0]
        for mode in ({}, {"init": True}, {"start": True})
    ]
    assert flag == [0, 1, 2]
    step = step_shader([(compile_field("_0 var-x"), False)], 8, 8)
    assert "if (P.init_flag == 2u) {" in step
    assert "var scale = 0.0; if (adj == 0u) { scale = -1.0 / (3.0 * wp); }" in step
    assert "let keep = 1.0 - wp;" in step  # as it stands after a collision


def test_the_warm_start_is_on_unless_the_start_says_otherwise() -> None:
    from stokes.api.commands.fast import WARM_START
    from stokes.fast.runner import WARM_FIDELITY, WARM_FROM, Frame

    runner = FastRunner(lambda *_a, **_k: None)
    status = runner.status()
    assert status["warm_start"] is True and status["warming"] is False
    assert WARM_FIDELITY < WARM_FROM
    assert Frame().to_json()["warming"] is False and Frame(warming=True).to_json()["warming"]
    assert WARM_START.type == "boolean" and not WARM_START.required


def test_sampling_is_held_to_a_share_of_the_workers_time() -> None:
    """A frame costs what its plane costs, whatever the lattice did meanwhile: ten a second left
    a lattice of 11.6 million cells a fifth of the time. The next frame waits for the last one's
    cost to be a fifth of the time since, and never comes sooner than a tenth of a second."""
    from stokes.fast import runner

    assert 0.0 < runner.SAMPLE_SHARE <= 0.25 and runner.FRAME_SECONDS == 0.1
    wait = max(runner.FRAME_SECONDS, 0.09 / runner.SAMPLE_SHARE)
    assert wait == pytest.approx(0.45)  # the large lattice: two frames a second
    assert max(runner.FRAME_SECONDS, 0.004 / runner.SAMPLE_SHARE) == runner.FRAME_SECONDS


# ------------------------------------------------------------------------------ in-place streaming


def _collide(arriving: np.ndarray) -> np.ndarray:
    """A stand-in for a collision: local to a cell, and mixing every direction."""
    return np.asarray(np.roll(arriving, 5, axis=0) * 1.01 + 0.1 * np.tanh(arriving.sum(axis=0)))


def test_in_place_streaming_moves_every_population_as_plain_streaming_does() -> None:
    """One buffer, read and written in place with the slots of each opposite pair swapping roles
    every step, carries exactly what two buffers and plain streaming carry."""
    from stokes.fast.inplace import arriving_of, step_in_place, step_plain

    stored = np.random.default_rng(7).normal(size=(19, 4, 5, 6))
    plain = arriving_of(stored, 0)
    for step in range(7):
        parity = step % 2
        assert np.array_equal(arriving_of(stored, parity), plain)
        stored = step_in_place(stored, parity, _collide)
        plain = step_plain(plain, _collide)


@pytest.mark.parametrize("parity", [0, 1])
def test_no_two_cells_share_a_place_whichever_links_are_walls(parity: int) -> None:
    """Every cell reads and writes nineteen places of its own, so the order the GPU runs the cells
    in does not matter: with every link regular, and with any set of links kept as wall links
    (a wall link is one for the cells on both its sides, and keeps its slot on every step)."""
    from stokes.fast.inplace import at_cell, boundary_slot, regular_slot

    n = 4
    rng = np.random.default_rng(parity)
    kind = rng.integers(0, 3, size=(n, n, n))  # three fluids: a link between two is a wall
    places: set[tuple[int, int, int, int]] = set()
    count = 0
    for x in np.ndindex(n, n, n):
        places.add((0, *x))
        count += 1
        for d in range(1, lattice.Q):
            source = tuple(
                (x[a] - c) % n for a, c in enumerate((lattice.CZ[d], lattice.CY[d], lattice.CX[d]))
            )
            wall = kind[source] != kind[x]
            slot = boundary_slot(d) if wall else regular_slot(d, parity)
            places.add((slot, *(x if at_cell(d) else source)))
            count += 1
    assert len(places) == count == 19 * n**3


def test_the_step_reads_and_writes_the_places_the_layout_names() -> None:
    """The shader's places are ``stokes.fast.inplace``'s: one buffer, bound once, read and written;
    over a link to the cell's own fluid the slot of the step's parity, over a wall or a port link
    the slot that never swaps, at the cell or at the neighbour the direction arrives from."""
    from stokes.fast.inplace import at_cell, boundary_slot, regular_slot

    step = step_shader([(compile_field("_0 var-x"), False)], 8, 8)
    assert "var<storage, read_write> f: array<f32>;" in step
    assert "src[" not in step and "dst[" not in step
    for d in range(1, lattice.Q):
        wall, even, odd = boundary_slot(d), regular_slot(d, 0), regular_slot(d, 1)
        assert odd == wall  # a wall link is a link that does not swap
        assert f"let r{d} = select({odd}u, {even}u, evenStep) * N + c{d};" in step
        assert f"let c{d} = {'id' if at_cell(d) else f's{d}'};" in step
        assert f"let held = f[{wall}u * N + c{d}];" in step
        assert f"f[{wall}u * N + c{d}] = held;" in step
        assert f"f[r{d}] = o{lattice.OPP[d]};" in step
    # A wall more than half a cell away is completed when it is stored, with what leaves the
    # cell in the same step; its share of the cell's mass comes off the rest population there.
    assert step.count("a1 = 0.5 / q;") == 1 and "let held = a1 * o2 + (1.0 - a1) * o1;" in step
    assert "f[id] = o0 - back;" in step


def test_sixteen_bit_storage_is_half_precision_scaled_for_the_lattice() -> None:
    """Lehmann et al.'s FP16S: half precision times 2^15, so its range is the +-2 the shifted
    populations live in; every sum and product stays in f32."""
    from stokes.fast.wgsl import STORAGES, classify_shader, population_bytes

    assert STORAGES == ("f32", "f16")
    assert population_bytes("f32") == 4 and population_bytes("f16") == 2
    field = [(compile_field("_0 var-x"), False)]
    for text in (step_shader(field, 8, 8, "f16"), classify_shader(field, 8, 8, "f16")):
        assert text.startswith("enable f16;")
        assert "var<storage, read_write> f: array<f16>;" in text
    half = step_shader(field, 8, 8, "f16")
    assert "let x = clamp(v * 32768.0, -65504.0, 65504.0);" in half
    # A cell put at rest stores zero: nothing to round, so the classification needs no draw.
    assert "f[0u * P.n + id] = f16(0.0);" in classify_shader(field, 8, 8, "f16")
    assert "toHalf" not in classify_shader(field, 8, 8, "f16")
    assert "fn fromHalf(v: f16) -> f32 { return f32(v) * 3.0517578125e-05; }" in half
    assert "f1 = fromHalf(f[r1]);" in half and "f[r1] = toHalf(o2, r1);" in half
    assert "enable f16" not in step_shader(field, 8, 8)
    # The arithmetic is the same text in both: only the loads and stores differ.
    plain = step_shader(field, 8, 8)
    assert half.count("let e0 = ") == plain.count("let e0 = ") == 2


def test_a_sixteen_bit_store_rounds_at_random_in_proportion() -> None:
    """Rounded to the nearer 16-bit number, a steady flow makes the same error at the same place
    every step and the errors add up (a duct's friction read 2.5 % low). The store rounds up or
    down with the odds of where the value lies between its two neighbours: either neighbour is a
    half-precision number, and the average over the draws is the value."""
    from stokes.fast.wgsl import ROUNDING, half_rounded, storage_header

    rng = np.random.default_rng(16)
    values = (rng.normal(size=4000) * 10.0 ** rng.uniform(-9, -1, size=4000)).astype(np.float32)
    nearest = half_rounded(values)
    # The nearer neighbour is what IEEE half precision keeps of the scaled value (ties apart).
    ieee = (values * np.float32(32768.0)).astype(np.float16).astype(np.float32) / np.float32(32768)
    assert np.mean(nearest == ieee) > 0.999
    down, up = half_rounded(values, np.ones(4000)), half_rounded(values, np.full(4000, 1e-7))
    assert np.all(np.abs(down) <= np.abs(values)) and np.all(np.abs(up) >= np.abs(values))
    normal = np.abs(values) > 2.0**-28
    assert np.all(np.abs(up - down)[normal] <= np.abs(values)[normal] * 2.0**-9)
    # One value, many draws: the mean is the value to far better than one rounding.
    value = np.float32(0.0123456)
    step = float(half_rounded(np.array([value]), np.array([1e-7]))[0]) - float(
        half_rounded(np.array([value]), np.array([1.0]))[0]
    )
    kept = half_rounded(np.full(40_000, value), rng.uniform(1e-7, 1.0, size=40_000))
    assert len(np.unique(kept)) == 2
    assert abs(float(kept.mean()) - float(value)) < 0.02 * step
    assert abs(float(half_rounded(np.array([value]))[0]) - float(value)) > 0.1 * step
    assert half_rounded(np.zeros(3, np.float32), rng.uniform(0.1, 1.0, size=3)).tolist() == [
        0,
        0,
        0,
    ]
    assert ROUNDING == "dither"
    assert "step(dither(at), q - low)" in storage_header("f16")
    assert "step(0.5, q - low)" in storage_header("f16", "nearest")
    # The draw is a hash of the place and of the lattice's step count, which cannot repeat in
    # 2^32 steps: times an odd constant, the counts map one to one.
    assert "(at ^ ((P.base + clock.step) * 2654435769u))" in storage_header("f16")
    counts = np.arange(1 << 16, dtype=np.uint32) * np.uint32(40503)  # a stride through 2^32
    assert np.unique(counts * np.uint32(2654435769)).size == counts.size and 2654435769 % 2 == 1


def test_the_cell_cap_is_what_one_buffer_holds() -> None:
    """One population buffer of 76 bytes a cell (38 in 16 bits) within the adapter's largest
    binding: 28 and 56 million cells at 2 GiB, where two buffers and a fixed cap gave 8 million."""
    from stokes.fast.grid import MAX_CELLS, bytes_per_cell, cell_limit, grid_for

    binding = 2_147_483_644
    assert cell_limit(binding) == 28_256_363 and cell_limit(binding, "f16") == 56_512_727
    assert cell_limit(1 << 40) == MAX_CELLS == 64_000_000
    assert bytes_per_cell() == 108 and bytes_per_cell("f16") == 70
    box = ((0.0, 0.0, 0.0), (0.06, 0.036, 0.01))
    fine = grid_for(*box, 5)
    assert not fine.coarsened and 20e6 < fine.cells < cell_limit(binding)
    assert grid_for(*box, 5, max_cells=8_000_000).coarsened


def test_every_step_reads_its_own_number_from_the_clock() -> None:
    """A run of steps is one pass, so a step's parity and its rounding's seed cannot be written
    between two steps: the step reads its number from a table bound at a dynamic offset, and
    the step count at the table's block 0 from the params. A pass starts at block 0 or 1 (the
    parity of the steps taken) and a run longer than the table is split into passes, so the
    count ``P.base + clock.step`` is the lattice's own and never repeats."""
    import inspect

    from stokes.fast.device import Lattice
    from stokes.fast.wgsl import CLOCK_STEPS

    step = step_shader([(compile_field("_0 var-x"), False)], 8, 8, "f16")
    assert "@group(0) @binding(8) var<uniform> clock: Clock;" in step
    assert "let evenStep = (clock.step & 1u) == 0u;" in step
    assert "P.parity" not in step and "P.epoch" not in step and "base: u32," in step
    source = inspect.getsource(Lattice.step)
    assert "first = self._clock & 1" in source
    assert "steps = min(remaining, CLOCK_STEPS - first)" in source  # never past the table
    assert "base = (self._clock - first) & 0xFFFFFFFF" in source
    # The passes of a long run, as ``Lattice.step`` cuts them: every step once, parity kept.
    clock, seen = 1, []
    remaining = 5000
    while remaining > 0:
        first = clock & 1
        steps = min(remaining, CLOCK_STEPS - first)
        seen += [(clock - first) + block for block in range(first, first + steps)]
        assert first + steps <= CLOCK_STEPS
        clock, remaining = clock + steps, remaining - steps
    assert seen == list(range(1, 5001))


def test_a_restart_from_rest_stops_saying_warm_start() -> None:
    """The NaN recovery, the hand-over to the LES and a lowered lattice speed all put the lattice
    back to rest: the flow a warm start gave is gone, and the status no longer claims it."""
    import inspect

    runner = FastRunner(lambda *_a, **_k: None)
    runner._warmed_from = 2
    assert runner.status()["warmed_from"] == 2
    runner._from_rest()
    assert runner.status()["warmed_from"] is None
    loop = inspect.getsource(FastRunner._loop)
    assert loop.count("lattice.reset()") == loop.count("self._from_rest()") == 3


def test_a_reset_clears_the_buffers_on_the_gpu() -> None:
    """Every population back to rest without a host array: nineteen floats a cell were 2 GB of
    host memory at 28 million cells (and 4.3 GB at 56 million in 16 bits, whose buffer is half
    that), on every start, fidelity change and recovery."""
    import inspect

    from stokes.fast.device import Lattice

    source = inspect.getsource(Lattice.reset)
    assert "clear_buffer" in source and "np.zeros" not in source and "write_buffer" not in source


def test_the_status_says_which_lattice_the_flow_came_from() -> None:
    from stokes.fast.runner import Frame

    assert Frame().to_json()["warmed_from"] is None
    assert Frame(warmed_from=2).to_json()["warmed_from"] == 2
    # A frame names its own grid, so a client's header does not wait for the next status.
    sent = Frame(fidelity=4, fluid_cells=1_572_864).to_json()
    assert (sent["fidelity"], sent["fluid_cells"]) == (4, 1_572_864)
    assert FastRunner(lambda *_a, **_k: None).status()["warmed_from"] is None
    from stokes.fast.runner import WARMED_SECONDS

    assert 2.0 < WARMED_SECONDS <= 30.0  # said long enough to read, not for ever


def test_the_storage_is_checked_before_anything_starts() -> None:
    from stokes.fast.case import FastCase

    runner = FastRunner(lambda *_a, **_k: None)
    assert runner.status()["storage"] == "f32"
    case = FastCase("_0 var-x", (0, 0, 0), (1, 1, 1), (), 1e-6)
    with pytest.raises(FastInvalid, match="storage"):
        runner.start(case, storage="f8")
    assert not runner.alive
