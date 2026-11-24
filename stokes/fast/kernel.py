# ruff: noqa: E501 - the shader source is WGSL, one statement per line, as the GPU compiles it.
"""The lattice's step as WGSL (ADR-0047 decision 5 and its addenda): streaming with the boundary
links, the collision, and the turbulent preview's closures.

One invocation per cell does, in order: read the populations that arrive (a wall, an inlet or an
outlet link is completed here), take the density's deviation and the momentum, choose what leaves
(the equilibrium layers next to a port, the regularised collision with an eddy viscosity, or two
relaxation times), and store it with the cell's velocity and density. ``P.init_flag == 2`` builds
what leaves from the velocity and density the cell already holds, not from what arrived: that is
how a lattice is started from another one's solution (``Lattice.prolong``).

**One buffer, in place** (addendum 4; the layout and its proof are ``stokes.fast.inplace``). A
cell reads and writes the same nineteen places. Over a link to its own fluid the place swaps
slots every step, which is the streaming. Over a link to a wall or a port the place never swaps:
it holds what the cell sent to the wall, already completed where the interpolated wall is more
than half a cell away (``a{d}`` is the share of the sent population in it), to be completed on
reading where it is nearer. What a completed link adds to the cell's mass comes off the rest
population when it is stored, as it comes off on reading for the others.

**The closures** (addendum 4). Smagorinsky and the shear-improved one take the stress from the
populations. WALE (Nicoud and Ducros 1999) and Vreman (2004) need the whole velocity gradient,
which the populations do not hold: it is taken by central differences of the neighbours'
velocities, a wall half-way to a neighbour that is not this fluid with the fluid at rest on it.
The neighbours' velocities are the ones their own invocations store, this step's or the last
one's as the GPU happens to schedule them: one step's change in a velocity is far below what the
closure resolves, and a steady flow has none. Both closures are written from the published
formulas; ``stokes.fast.lattice.eddy_viscosity`` is the same arithmetic in numpy, which the tests
hold to the properties the papers claim (no eddy viscosity in a laminar shear).

The module is long because the shader is one straight-line program with every direction unrolled.
"""

from __future__ import annotations

from stokes.fast.inplace import at_cell
from stokes.fast.lattice import CX, CY, CZ, OPP, W
from stokes.fast.wgsl import (
    _CELL,
    _PORTS,
    CLOCK,
    PARAMS,
    _c,
    _comp,
    _equilibrium,
    _f32,
    _moments,
    _offset,
    load,
    storage_header,
    store,
    zone_fields,
)

#: The six components of a symmetric tensor, as the shader names them, with their axes.
_TENSOR = {"xx": (0, 0), "yy": (1, 1), "zz": (2, 2), "xy": (0, 1), "xz": (0, 2), "yz": (1, 2)}

#: The velocity gradient of a cell (column ``j`` is the velocity's derivative along axis ``j``)
#: and the eddy viscosity of the two closures that need it, in lattice units (the filter width is
#: the cell). The gradient is scaled to unit size first, so the powers neither overflow nor vanish
#: in f32 whatever the lattice speed: both viscosities are of first degree in the gradient.
_CLOSURES = """
fn diffOf(id: u32, off: u32, code: u32, uc: vec3f) -> vec3f {
  var up = -uc; var um = -uc;
  if ((flags[id + off] & 0xffu) == code) { up = vel[id + off].xyz; }
  if ((flags[id - off] & 0xffu) == code) { um = vel[id - off].xyz; }
  return 0.5 * (up - um);
}
fn gradOf(id: u32, code: u32, uc: vec3f) -> mat3x3f {
  return mat3x3f(diffOf(id, 1u, code, uc), diffOf(id, P.nx, code, uc), diffOf(id, P.nx * P.ny, code, uc));
}
fn sizeOf(m: mat3x3f) -> f32 { return dot(m[0], m[0]) + dot(m[1], m[1]) + dot(m[2], m[2]); }
fn nutOf(g: mat3x3f, kind: u32, c: f32) -> f32 {
  let n2 = sizeOf(g);
  if (n2 < 1e-30) { return 0.0; }
  let n = sqrt(n2);
  let h = g * (1.0 / n);
  if (kind == 2u) {
    let h2 = h * h;
    let tr = (h2[0][0] + h2[1][1] + h2[2][2]) / 3.0;
    let a = sizeOf(0.5 * (h2 + transpose(h2)) - mat3x3f(tr, 0.0, 0.0, 0.0, tr, 0.0, 0.0, 0.0, tr));
    let b = sizeOf(0.5 * (h + transpose(h)));
    let den = b * b * sqrt(b) + a * sqrt(sqrt(a));
    if (den < 1e-30) { return 0.0; }
    return c * n * a * sqrt(a) / den;
  }
  let bt = h * transpose(h);
  let big = bt[0][0] * bt[1][1] - bt[0][1] * bt[0][1] + bt[0][0] * bt[2][2] - bt[0][2] * bt[0][2] + bt[1][1] * bt[2][2] - bt[1][2] * bt[1][2];
  return c * n * sqrt(max(big, 0.0));
}
"""


def _rebuilt(keep: str, indent: str = "    ") -> list[str]:
    """Lines setting every ``o{d}`` to the equilibrium ``e{d}`` plus ``keep`` times the part the
    momentum flux ``pxx``..``pyz`` alone gives (the regularised form, Latt and Chopard 2006)."""
    lines = []
    for d in range(19):
        c = (CX[d], CY[d], CZ[d])
        parts = [
            f"{_f32(c[a] * c[a] - 1.0 / 3.0)} * p{name}"
            for a, name in enumerate(("xx", "yy", "zz"))
        ]
        parts += [
            f"{_f32(2.0 * c[a] * c[b])} * p{name}"
            for (a, b), name in (((0, 1), "xy"), ((0, 2), "xz"), ((1, 2), "yz"))
            if c[a] * c[b]
        ]
        lines.append(
            f"{indent}o{d} = e{d} + {keep} * {_f32(4.5 * W[d])} * (" + " + ".join(parts) + ");"
        )
    return lines


def _reads(nx: int, ny: int, storage: str) -> list[str]:
    """Every arriving population, read in place. First the links to the cell's own fluid (the
    slot of the step's parity), then the wall, inlet and outlet links (the slot that never
    swaps), which need the first: a wall nearer than half a cell is completed with the
    population that arrives from the fluid side."""
    lines = [
        "  let evenStep = (clock.step & 1u) == 0u;",
        "  var phx = 0.0; var havePhx = false;",
        "  f0 = " + load(storage, "id") + ";",
    ]
    for d in range(1, 19):
        o, off = OPP[d], _offset(d, nx, ny)
        # The cell the direction arrives from, and the cell whose memory holds the link's place.
        lines += [
            f"  let s{d} = u32(i32(id) - ({off}));",
            f"  let c{d} = {'id' if at_cell(d) else f's{d}'};",
            f"  let r{d} = select({o}u, {d}u, evenStep) * N + c{d};",
            f"  var a{d} = -1.0;",
            f"  if ((flags[s{d}] & 0xffu) == code) {{ f{d} = {load(storage, f'r{d}')}; }} else {{ a{d} = 1.0; }}",
        ]
    for d in range(1, 19):
        o = OPP[d]
        w = _f32(W[d])
        lines += [
            f"  if (a{d} > 0.0) {{",
            f"    let held = {load(storage, f'{o}u * N + c{d}')};",
            "    if (!havePhx) { phx = phiOf(code, pos); havePhx = true; }",
            f"    let phs = phiOf(code, pos - {_c(d)} * P.dx);",
            "    let den = phx - phs; var q = 0.5; if (abs(den) > 1e-20) { q = clamp(phx / den, 0.0, 1.0); }",
            f"    let port = portAt(pos - {_c(d)} * (P.dx * q));",
            "    if (port < 0) {",
            f"      if (P.mode == 0u) {{ f{d} = held; }}",
            f"      else if (q < 0.5) {{ if (a{o} < 0.0) {{ f{d} = 2.0 * q * held + (1.0 - 2.0 * q) * f{o}; }} else {{ f{d} = held; }} leak += f{d} - held; }}",
            f"      else {{ f{d} = held; a{d} = 0.5 / q; }}",
            "    } else if (portF(u32(port), 3u) < 0.5) {",
            f"      f{d} = held + 6.0 * {w} * inflow * dot({_c(d)}, portV(u32(port), 20u));",
            "    } else {",
            # An outlet link: anti-bounce-back at the outlet's pressure, with the velocity the
            # cell had after its last step.
            f"      let lu = vel[id].xyz; let cu = dot({_c(d)}, lu);",
            f"      f{d} = -held + 2.0 * {w} * (portF(u32(port), 19u) + 4.5 * cu * cu - 1.5 * dot(lu, lu));",
            "    }",
            "  }",
        ]
    return lines


def _stores(storage: str) -> list[str]:
    """What leaves, written where it was read: over a link to the cell's own fluid the
    population sent along it; over a wall or port link what is kept for the next step."""
    lines = ["  var back = 0.0;"]
    for d in range(1, 19):
        o = OPP[d]
        lines += [
            f"  if (a{d} < 0.0) {{ {store(storage, f'r{d}', f'o{o}')} }} else {{",
            f"    let held = a{d} * o{o} + (1.0 - a{d}) * o{d}; back += held - o{o};",
            f"    {store(storage, f'{o}u * N + c{d}', 'held')}",
            "  }",
        ]
    lines.append("  " + store(storage, "id", "o0 - back"))
    return lines


def step_shader(fields: list[tuple[str, bool]], nx: int, ny: int, storage: str = "f32") -> str:
    """One lattice step, in place: the arriving populations with the boundary links, the
    collision, and what leaves written where it was read."""
    lines = [
        storage_header(storage),
        PARAMS,
        "struct Counters { guard: atomic<u32>, nan: atomic<u32> };",
        f"@group(0) @binding(0) var<storage, read_write> f: array<{storage}>;",
        "@group(0) @binding(1) var<storage, read> flags: array<u32>;",
        "@group(0) @binding(2) var<uniform> P: Params;",
        "@group(0) @binding(3) var<storage, read> ports: array<f32>;",
        "@group(0) @binding(4) var<storage, read> masks: array<u32>;",
        "@group(0) @binding(5) var<storage, read_write> counters: Counters;",
        # Each fluid cell's velocity and, in w, its density's deviation from 1: for the
        # temperature's step, the closures' gradient, the samples and the plane. Written by the
        # cell's own invocation only.
        "@group(0) @binding(6) var<storage, read_write> vel: array<vec4f>;",
        # The running mean of each cell's stress, for the shear-improved closure.
        "@group(0) @binding(7) var<storage, read_write> mean: array<f32>;",
        # Which step of its pass this is (bound at a dynamic offset into the device's table):
        # its parity, and with ``P.base`` the lattice's step count, which seeds a 16-bit store.
        CLOCK,
        "@group(0) @binding(8) var<uniform> clock: Clock;",
        _CELL,
        _PORTS,
        zone_fields(fields),
        _CLOSURES,
        "@compute @workgroup_size(64) fn main(@builtin(workgroup_id) wg: vec3u, @builtin(local_invocation_index) li: u32) {",
        "  let id = cellId(wg, li); if (id >= P.n) { return; }",
        "  let code = flags[id] & 0xffu;",
        "  if (!isFluid(code)) { return; }",
        "  let zone = zoneOf(code);",
        "  let N = P.n;",
        "  let i = id % P.nx; let j = (id / P.nx) % P.ny; let k = id / (P.nx * P.ny);",
        "  let pos = posOf(i, j, k);",
        "  let wp = zoneF(zone, 0u); let wm = zoneF(zone, 1u);",
        # **What the interpolated walls return less what was sent to them**, taken off the rest
        # population below so the cell keeps its mass: interpolated bounce-back alone is not
        # conservative, and lost a tenth of the flow in channels eight cells wide (a gyroid plate
        # exchanger, measured 2026-11-22). The rest population carries no momentum, so the wall's
        # position is as the interpolation has it.
        "  var leak = 0.0;",
        # An inlet's velocity at this cell: the ramp, the profile's share and the port's gain.
        "  let adj = (flags[id] >> 8u) & 0xffu;",
        "  var inflow = 0.0;",
        "  if (adj > 0u) { inflow = P.ramp * f32(flags[id] >> 16u) / 65535.0 * portF(adj - 1u, 23u); }",
        "  var f0 = 0.0;",
        *(f"  var f{d} = 0.0;" for d in range(1, 19)),
        *_reads(nx, ny, storage),
        "  f0 -= leak;",
    ]
    lines += _moments("f{d}", "rho0", "mom")
    lines += [
        *(f"  var o{d} = 0.0;" for d in range(19)),
        "  var uw = vec3f(0.0); var rw = 0.0;",
        "  var kept = 0.0; if (P.closure == 1u) { kept = mean[id]; }",
        # **A start from another lattice's solution**: what leaves is the equilibrium of the
        # velocity and density the cell was given, and the part the velocity's own strain gives
        # at this lattice's relaxation time (first-order Chapman-Enskog: the momentum flux off
        # equilibrium is -2/3 tau S), as it stands after a collision. The layers next to a port
        # are equilibrium layers and start as such.
        "  if (P.init_flag == 2u) {",
        "    let last = vel[id]; rw = last.w; uw = last.xyz;",
        *(f"    let e{d} = {_equilibrium(d, 'rw', 'uw')};" for d in range(19)),
        "    var scale = 0.0; if (adj == 0u) { scale = -1.0 / (3.0 * wp); }",
        "    let g = gradOf(id, code, uw);",
        "    let pxx = 2.0 * scale * g[0][0]; let pyy = 2.0 * scale * g[1][1]; let pzz = 2.0 * scale * g[2][2];",
        "    let pxy = scale * (g[1][0] + g[0][1]); let pxz = scale * (g[2][0] + g[0][2]); let pyz = scale * (g[2][1] + g[1][2]);",
        "    let keep = 1.0 - wp;",
        *_rebuilt("keep"),
        # **The cells next to an inlet are an equilibrium layer**: the inlet's velocity at the
        # density the streaming brought. Bounce-back with a wall velocity alone gave the first
        # layer twice the inlet speed near tau's limit (measured with the LES, 2026-11-21); this
        # layer holds the inflow at any tau, as the prototype's inlet planes did.
        "  } else if (adj > 0u && portF(adj - 1u, 3u) < 0.5 && rho0 == rho0) {",
        "    rw = rho0; uw = portV(adj - 1u, 20u) * inflow;",
        *(f"    o{d} = {_equilibrium(d, 'rw', 'uw')};" for d in range(19)),
        # **The cells next to an outlet are an equilibrium layer too**: the outlet's density with
        # the momentum the streaming brought. Anti-bounce-back alone let the eddies of a turbulent
        # preview pile up at the outlet (measured 2026-11-22: 14 times the inlet speed in the last
        # layer at a cell Reynolds number of 62); the layer absorbs them.
        "  } else if (adj > 0u && portF(adj - 1u, 3u) >= 0.5 && rho0 == rho0) {",
        "    uw = mom; let sout = length(uw);",
        "    if (sout > 0.3) { uw = uw * (0.3 / sout); atomicAdd(&counters.guard, 1u); }",
        "    rw = portF(adj - 1u, 19u);",
        *(f"    o{d} = {_equilibrium(d, 'rw', 'uw')};" for d in range(19)),
        "  } else if (rho0 != rho0) {",
        "    atomicAdd(&counters.nan, 1u);",
        "    kept = 0.0;",
        "  } else {",
        # **The guard holds the speed only.** With the incompressible equilibrium the density is
        # the pressure and nothing else: a core whose pressure drop is hundreds of inlet dynamic
        # heads has a density deviation above 1 and is none the worse for it, where a cap on the
        # density held a quarter of its flow back (a gyroid plate exchanger, measured 2026-11-22).
        "    let rho = rho0; var u = mom;",
        "    if (P.guard == 1u) {",
        "      let speed = length(u);",
        "      if (speed > 0.3) {",
        "        atomicAdd(&counters.guard, 1u);",
        "        u = u * (0.3 / speed);",
        "      }",
        "    }",
        "    rw = rho; uw = u;",
        *(f"    let e{d} = {_equilibrium(d, 'rho', 'u')};" for d in range(19)),
        # The Smagorinsky model (Hou et al. 1996): the non-equilibrium momentum flux gives the
        # strain, and the strain an eddy viscosity added to the molecular one through an
        # effective relaxation time. **With it the collision is the regularised one** (Latt and
        # Chopard 2006): the populations are rebuilt from the equilibrium and that flux alone,
        # which drops the modes the lattice carries but the flow does not. Two relaxation times
        # near tau's limit left cell-to-cell noise of the inlet speed's size behind an inlet
        # (measured 2026-11-22 at a cell Reynolds number of 62).
        "    if (P.smag > 0.0) {",
    ]
    for name, (a, b) in _TENSOR.items():
        terms = [
            f"{_comp(d, a) * _comp(d, b)}.0 * (f{d} - e{d})"
            for d in range(1, 19)
            if _comp(d, a) * _comp(d, b)
        ]
        lines.append(f"      let p{name} = " + " + ".join(terms) + ";")
    lines += [
        "      let t0 = 1.0 / wp;",
        "      var te = t0;",
        "      if (P.closure >= 2u) {",
        # WALE and Vreman: an eddy viscosity from the velocity gradient, none in a laminar shear.
        "        te = t0 + 3.0 * nutOf(gradOf(id, code, u), P.closure, P.smag);",
        "      } else {",
        "        let q = sqrt(pxx * pxx + pyy * pyy + pzz * pzz + 2.0 * (pxy * pxy + pxz * pxz + pyz * pyz));",
        # **The shear-improved closure** (Leveque et al. 2007) takes the stress's running mean
        # off before the eddy viscosity: a steady, laminar shear then has none, where the plain
        # Smagorinsky model read a laminar duct's friction 5 to 7 % high.
        "        var qe = q;",
        "        if (P.closure == 1u) { qe = max(q - kept, 0.0); kept = kept + P.ema * (q - kept); }",
        "        te = 0.5 * (t0 + sqrt(t0 * t0 + P.smag * qe));",
        "      }",
        "      let keep = 1.0 - 1.0 / te;",
        *_rebuilt("keep", "      "),
        "    } else {",
        "      o0 = f0 - wp * (f0 - e0);",
    ]
    for d in range(1, 19):
        o = OPP[d]
        if o < d:
            continue
        lines += [
            f"      {{ let fp = 0.5 * (f{d} + f{o}); let fm = 0.5 * (f{d} - f{o});",
            f"        let ep = 0.5 * (e{d} + e{o}); let em = 0.5 * (e{d} - e{o});",
            f"        o{d} = f{d} - wp * (fp - ep) - wm * (fm - em);",
            f"        o{o} = f{o} - wp * (fp - ep) + wm * (fm - em); }}",
        ]
    lines += [
        "    }",
        "  }",
        *_stores(storage),
        "  vel[id] = vec4f(uw, rw);",
        "  if (P.closure == 1u) { mean[id] = kept; }",
        "}",
    ]
    return "\n".join(lines)
