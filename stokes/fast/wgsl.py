# ruff: noqa: E501 - the shader source is WGSL, one statement per line, as the GPU compiles it.
"""WGSL for Fast mode (ADR-0047 decisions 3 to 5): the field compiler and the lattice's shaders.

**The field compiler** reads the text ``stokes.implicit.graph.to_text`` writes (Fidget's format:
one ``_N op args`` line per node, shared nodes once) and writes one WGSL function,
``fn phi(p: vec3f) -> f32``, with one ``let`` per line. It accepts exactly the closed operation set
of ADR-0035 decision 4, and ``mod`` is the floored remainder the numpy evaluator uses. The text is
written by Stokes itself, but it is checked like any input: an unknown operation, a reference to a
node not yet defined or a non-finite constant is refused, so a WGSL compile error can only be ours.

**The shaders** are generated with every direction of the lattice unrolled, so the GPU sees
straight-line code and constant offsets. Populations are stored as ``f - w`` (the rest state
is all zeros, and round-off at density 1 is much smaller), in f32 or in 16 bits (``STORAGES``), in
one buffer that every step reads and writes in place (``stokes.fast.inplace``). Cell flags hold the cell's type in the low byte (0 fluid, 1 solid) and, for a
fluid cell next to a port, the port's index plus one in the next byte. The step itself is
``stokes.fast.kernel``; this module has the parts every shader shares, the classification, the
temperature's step, the prolongation and the samples (long, as generated shader text is).

Units in the shaders: positions and lengths in the field's millimetres; velocities and densities in
lattice units. The equilibrium is the incompressible one (He and Luo 1997): the velocity is the
momentum, and every density is carried as its deviation from 1.
"""

from __future__ import annotations

import math
import re

import numpy as np
from numpy.typing import NDArray

from stokes.fast.errors import FastInvalid
from stokes.fast.lattice import CX, CY, CZ, W
from stokes.fast.ports import PORT_STRIDE

#: How far off a port's plane (in cells) a wall crossing may lie and still be the port's. A whole
#: cell, as it was, also took the side walls' links of the second layer of cells (measured
#: 2026-11-22: 32 of them on a pipe 32 cells across).
PORT_PLANE_TOLERANCE = 0.3
#: **An inlet's profile falls to zero at the walls over this many cells** (and the port's gain
#: restores its flow rate): a uniform speed held right up to a no-slip wall loses flow there. The
#: wall distance is read ``INLET_PROBE`` cells inside the port.
INLET_TAPER = 1.0
INLET_PROBE = 3.0

#: The largest field the compiler takes: ADR-0035's graph limit.
MAX_NODES = 100_000

_UNARY = {
    "neg": "-({0})",
    "abs": "abs({0})",
    "square": "({0}) * ({0})",
    "sqrt": "sqrt({0})",
    "sin": "sin({0})",
    "cos": "cos({0})",
}
_BINARY = {
    "add": "{0} + {1}",
    "sub": "{0} - {1}",
    "mul": "{0} * {1}",
    "div": "{0} / {1}",
    "min": "min({0}, {1})",
    "max": "max({0}, {1})",
    "mod": "{0} - {1} * floor({0} / {1})",
    "atan2": "atan2({0}, {1})",
}
_LEAVES = {"var-x": "p.x", "var-y": "p.y", "var-z": "p.z"}
_NAME = re.compile(r"^_[0-9]+$")


def compile_field(text: str) -> str:
    """The WGSL function ``phi`` of a field's text."""
    lines: list[str] = []
    defined: set[str] = set()
    last = ""
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        name, op, args = parts[0], parts[1] if len(parts) > 1 else "", parts[2:]
        if not _NAME.match(name) or name in defined:
            raise FastInvalid(
                f"line {number} of the field names {name!r} badly", details={"line": number}
            )
        if op == "const":
            if len(args) != 1:
                raise FastInvalid(
                    f"line {number}: a constant takes one value", details={"line": number}
                )
            expr = _constant(args[0], number)
        elif op in _LEAVES:
            if args:
                raise FastInvalid(
                    f"line {number}: {op} takes no argument", details={"line": number}
                )
            expr = _LEAVES[op]
        elif op in _UNARY or op in _BINARY:
            arity = 1 if op in _UNARY else 2
            if len(args) != arity or any(a not in defined for a in args):
                raise FastInvalid(
                    f"line {number}: {op} takes {arity} node(s) defined above it",
                    details={"line": number},
                )
            template = _UNARY[op] if arity == 1 else _BINARY[op]
            expr = template.format(*(_var(a) for a in args))
        else:
            raise FastInvalid(f"line {number}: unknown operation {op!r}", details={"line": number})
        lines.append(f"  let {_var(name)} = {expr};")
        defined.add(name)
        last = name
        if len(defined) > MAX_NODES:
            raise FastInvalid("the field has too many nodes", details={"max": MAX_NODES})
    if not last:
        raise FastInvalid("the field is empty")
    return "fn phi(p: vec3f) -> f32 {\n" + "\n".join(lines) + f"\n  return {_var(last)};\n}}\n"


def _var(name: str) -> str:
    return "v" + name[1:]


def _constant(text: str, number: int) -> str:
    try:
        value = float(text)
    except ValueError:
        raise FastInvalid(
            f"line {number}: {text!r} is not a number", details={"line": number}
        ) from None
    if not math.isfinite(value):
        raise FastInvalid(f"line {number}: the constant is not finite", details={"line": number})
    return f"f32({_f32(value)})"


def _f32(value: float) -> str:
    """A float literal WGSL accepts: always with a decimal point or an exponent."""
    text = repr(float(value))
    return text if any(ch in text for ch in ".eE") else text + ".0"


# --------------------------------------------------------------------------------- shared parts

#: The uniform block every shader binds, 24 four-byte words (96 bytes). The order is the order
#: ``stokes.fast.device.params_bytes`` packs. ``ramp`` (0 to 1) scales every inlet's velocity: a flow
#: started at full speed from rest sends a pressure wave that a low-viscosity lattice does not damp.
#: ``smag`` is the closure's constant with a turbulent preview on (``stokes.fast.lattice.closure_constant``:
#: ``18 sqrt(2) Cs^2`` for the Smagorinsky model), 0 with it off; ``closure`` its number.
#: ``zbase`` is the row of the port table where the zones' rows start, ``thermal`` 1 with the
#: temperature solved. ``init_flag`` is 1 for a classification that starts every cell at rest and
#: 2 for a step that starts the populations from the cells' velocities (``stokes.fast.kernel``).
#: ``base`` is the number of lattice steps taken before block 0 of the clock in this pass (a pass
#: of steps reads this block from a buffer written before it, so nothing here can change from
#: step to step: the step's own number within the pass is ``CLOCK``'s).
PARAMS = """
struct Params {
  nx: u32, ny: u32, nz: u32, n: u32,
  wp: f32, wm: f32, mode: u32, nports: u32,
  ox: f32, oy: f32, oz: f32, dx: f32,
  init_flag: u32, axis: u32, plane: u32, guard: u32,
  ramp: f32, smag: f32, closure: u32, ema: f32,
  zbase: u32, nzones: u32, thermal: u32, base: u32,
};
"""

#: Words in the uniform block.
PARAM_WORDS = 24

#: **The lattice's clock**: which step of its pass a step is. A run of steps is one compute
#: pass, so what changes with every step cannot be written by the host between two of them. The
#: device keeps a table of ``CLOCK_STEPS`` such blocks, block ``k`` holding ``k``, and binds the
#: step's block at a dynamic offset: the step reads ``clock.step == k``. A pass starts at block 0
#: or 1, whichever has the parity of the steps taken so far, so ``clock.step``'s lowest bit is the
#: step's parity (which slot of a pair a link uses, ``stokes.fast.inplace``), and ``P.base +
#: clock.step`` is the number of steps the lattice has taken: a counter that does not repeat in
#: 2^32 steps, which seeds a 16-bit store's rounding.
CLOCK = "struct Clock { step: u32 };"
#: Blocks in the clock's table: the most steps one pass takes (less one when it starts at block
#: 1); ``Lattice.step`` splits a longer run into passes.
CLOCK_STEPS = 2048

#: **How the populations are stored** (ADR-0047 addendum 4, decision 23). ``f32`` as they are
#: computed. ``f16`` in half precision scaled by 2^15 (Lehmann et al. 2022, their ``FP16S``): the
#: shifted populations never leave +-2, so the scale moves half precision's best range down to
#: where they are, at no cost; arithmetic stays in f32. It needs the adapter's ``shader-f16``
#: feature: two halves packed in a word would be written by two cells at once under in-place
#: streaming.
STORAGES = ("f32", "f16")
_HALF_SCALE = 32768.0
#: The largest half-precision number: a stored value is held inside it.
_HALF_MAX = 65504.0
#: Half precision's smallest step (its subnormal spacing, 2^-24).
_HALF_STEP = 2.0**-24


def population_bytes(storage: str) -> int:
    """Bytes one population takes on the device."""
    return 2 if storage == "f16" else 4


#: **How a 16-bit store rounds.** ``dither``: up or down at random, in proportion to where the
#: value lies between its two 16-bit neighbours; the draw is a hash of the place and of the number
#: of steps the lattice has taken (``P.base + clock.step``, see ``CLOCK``). That number is
#: multiplied by an odd constant, which maps the 2^32 step numbers one to one, before it meets the
#: place: no two of 2^32 steps draw the same field.
#: ``nearest``: to the nearer neighbour. A steady flow stores the same
#: value at the same place every step, so rounding to nearest makes the same error every step and
#: the errors add up to a bias (measured 2026-11-23: a square duct's friction 2.5 % low, its
#: outflow 1 % over its inflow); dithered, the stored value is right on average. Either way the
#: rounding is done in f32 arithmetic, so it does not depend on how a GPU converts f32 to f16.
ROUNDINGS = ("dither", "nearest")
ROUNDING = "dither"


def storage_header(storage: str, rounding: str | None = None) -> str:
    """What a shader that binds the populations starts with: for 16-bit storage the feature and
    the two conversions, as functions (inside the step ``f16`` is a population's name).
    ``toHalf(v, at)`` stores ``v`` at place ``at``: scaled, then moved onto half precision's grid
    (2^(e - 11) apart for a number in [2^(e - 1), 2^e), never finer than its smallest step)."""
    if storage != "f16":
        return ""
    edge = "dither(at)" if (rounding or ROUNDING) == "dither" else "0.5"
    return "\n".join(
        [
            "enable f16;",
            # A number in (0, 1] from the place and the lattice's step count: a small integer
            # hash. The count times an odd constant is one to one over 2^32 counts.
            "fn dither(at: u32) -> f32 {",
            "  var h = (at ^ ((P.base + clock.step) * 2654435769u)) * 2654435761u;",
            "  h ^= h >> 15u; h *= 2246822519u; h ^= h >> 13u;",
            "  return (f32(h >> 8u) + 1.0) * 5.9604645e-08;",
            "}",
            "fn toHalf(v: f32, at: u32) -> f16 {",
            f"  let x = clamp(v * {_f32(_HALF_SCALE)}, {_f32(-_HALF_MAX)}, {_f32(_HALF_MAX)});",
            "  let size = abs(x);",
            # Half precision's step at this size, 2^(e - 10) for a number in [2^e, 2^(e + 1)) and
            # 2^-24 below 2^-14, built from the exponent's bits; and one over it likewise.
            "  let bits = max(bitcast<u32>(size) & 0x7f800000u, 0x38800000u) - 0x05000000u;",
            "  let q = size * bitcast<f32>(0x7f000000u - bits);",
            "  let low = floor(q);",
            f"  return f16(sign(x) * (low + step({edge}, q - low)) * bitcast<f32>(bits));",
            "}",
            f"fn fromHalf(v: f16) -> f32 {{ return f32(v) * {_f32(1.0 / _HALF_SCALE)}; }}",
        ]
    )


def half_rounded(
    values: NDArray[np.float32], draws: NDArray[np.float32] | None = None
) -> NDArray[np.float32]:
    """What a 16-bit store keeps of ``values``: ``toHalf`` and ``fromHalf`` in numpy, bit for bit,
    so the rounding is tested without a GPU. ``draws`` are the numbers in (0, 1] the shader's
    hash gives (one a value); without them the value goes to the nearer neighbour."""
    x = np.clip(np.asarray(values, np.float32) * np.float32(_HALF_SCALE), -_HALF_MAX, _HALF_MAX)
    size = np.abs(x).astype(np.float32)
    exponent = np.maximum(size.view(np.uint32) & np.uint32(0x7F800000), np.uint32(0x38800000))
    bits = (exponent - np.uint32(0x05000000)).astype(np.uint32)
    q = size * (np.uint32(0x7F000000) - bits).astype(np.uint32).view(np.float32)
    low = np.floor(q)
    edge = np.float32(0.5) if draws is None else np.asarray(draws, np.float32)
    kept = np.sign(x) * (low + (q - low >= edge)) * bits.view(np.float32)
    out: NDArray[np.float32] = kept.astype(np.float16).astype(np.float32)
    return out / np.float32(_HALF_SCALE)


def rest(storage: str, index: str) -> str:
    """The WGSL statement putting the population at ``index`` of the buffer ``f`` at rest (the
    stored value is the population less its weight, so rest is zero: nothing to round)."""
    return f"f[{index}] = {storage}(0.0);"


def load(storage: str, index: str) -> str:
    """The WGSL expression of the population at ``index`` of the buffer ``f``, as f32."""
    return f"fromHalf(f[{index}])" if storage == "f16" else f"f[{index}]"


def store(storage: str, index: str, value: str) -> str:
    """The WGSL statement storing ``value`` (f32) at ``index`` of the buffer ``f``."""
    if storage == "f16":
        return f"f[{index}] = toHalf({value}, {index});"
    return f"f[{index}] = {value};"


#: **A cell's code** (the low byte of its flags): 1 is outside every body; a fluid zone ``z`` is 0
#: for the first and ``z + 1`` after it; a solid zone is its fluid code with the top bit set. A
#: fluid's wall is any neighbour whose code is not its own, so two fluids never mix even where no
#: solid cell lies between them.
VOID = 1
SOLID_BIT = 0x80


def zone_code(zone: int, solid: bool) -> int:
    """The cell code of zone ``zone`` (see ``VOID``)."""
    code = 0 if zone == 0 else zone + 1
    return code | SOLID_BIT if solid else code


_CELL = """
fn cellId(wg: vec3u, li: u32) -> u32 { return (wg.y * 65535u + wg.x) * 64u + li; }
fn posOf(i: u32, j: u32, k: u32) -> vec3f {
  return vec3f(P.ox + (f32(i) + 0.5) * P.dx, P.oy + (f32(j) + 0.5) * P.dx, P.oz + (f32(k) + 0.5) * P.dx);
}
fn zoneOf(code: u32) -> u32 { let c = code & 0x7fu; if (c == 0u) { return 0u; } return c - 1u; }
fn isFluid(code: u32) -> bool { return code != 1u && code < 0x80u; }
"""

_PORTS = f"""
const PS: u32 = {PORT_STRIDE}u;
fn portF(k: u32, i: u32) -> f32 {{ return ports[k * PS + i]; }}
fn portV(k: u32, i: u32) -> vec3f {{ return vec3f(ports[k * PS + i], ports[k * PS + i + 1u], ports[k * PS + i + 2u]); }}
fn zoneF(z: u32, i: u32) -> f32 {{ return ports[(P.zbase + z) * PS + i]; }}
// The port whose footprint holds the point p (mm), or -1: within tol cells of its plane, inside its
// mask. A wall crossing is on the plane to round-off; the tolerance keeps the side walls' links out.
fn anyPortAt(p: vec3f, tol: f32) -> i32 {{
  for (var k = 0u; k < P.nports; k++) {{
    let r = p - portV(k, 0u);
    if (abs(dot(portV(k, 4u), r)) > tol * P.dx) {{ continue; }}
    let a = dot(portV(k, 8u), r) - portF(k, 16u);
    let b = dot(portV(k, 12u), r) - portF(k, 17u);
    if (a < 0.0 || b < 0.0) {{ continue; }}
    let pix = portF(k, 18u);
    let ix = u32(a / pix);
    let iy = u32(b / pix);
    let w = u32(portF(k, 11u));
    let h = u32(portF(k, 15u));
    if (ix >= w || iy >= h) {{ continue; }}
    if (masks[u32(portF(k, 7u)) + iy * w + ix] != 0u) {{ return i32(k); }}
  }}
  return -1;
}}
// An inlet or an outlet at a wall crossing (the thermal walls are not flow ports).
fn portAt(p: vec3f) -> i32 {{
  let k = anyPortAt(p, {_f32(PORT_PLANE_TOLERANCE)});
  if (k >= 0 && portF(u32(k), 3u) > 1.5) {{ return -1; }}
  return k;
}}
"""


def _c(d: int) -> str:
    return f"vec3f({CX[d]}.0, {CY[d]}.0, {CZ[d]}.0)"


def _comp(d: int, axis: int) -> int:
    return (CX, CY, CZ)[axis][d]


def _offset(d: int, nx: int, ny: int) -> int:
    return CX[d] + nx * (CY[d] + ny * CZ[d])


def _moments(read: str, rho: str, mom: str) -> list[str]:
    """Lines summing the density's deviation from 1 (the shifted populations) and the momentum of
    ``read(d)``. **The deviation, never the density**: a sum started at 1 in f32 rounds to 6e-8,
    which is the whole signal of a slow, viscous flow (measured 2026-11-22: a pipe at a lattice
    speed of 2e-5 never settled, its flow rate wandering by 15 %)."""
    lines = [f"  var {rho} = 0.0;", f"  var {mom} = vec3f(0.0);"]
    for d in range(19):
        value = read.format(d=d)
        lines.append(f"  {rho} += {value};" + (f" {mom} += {_c(d)} * {value};" if d else ""))
    return lines


def _equilibrium(d: int, rho: str, u: str) -> str:
    """The shifted equilibrium ``feq - w`` of direction d, in the incompressible form (He and Luo
    1997): the density carries the pressure only, and ``u`` is the momentum. ``rho`` is the density's
    deviation from 1."""
    w = _f32(W[d])
    cu = f"dot({_c(d)}, {u})"
    return f"{w} * {rho} + {w} * (3.0 * {cu} + 4.5 * {cu} * {cu} - 1.5 * dot({u}, {u}))"


#: **A seam inside a body is not a wall** (ADR-0047 addendum 5, decision 31). A union of parts that share a face
#: exactly (a core box ending at x = 9 mm, a plenum starting there) has a field of exactly zero, or
#: of round-off, on the shared face, inside the fluid. A cell whose centre falls on that face was
#: classified solid, and a layer of them cut the fluid in pieces (measured 2026-11-23: a gyroid
#: block with its plenums in four pieces, no outflow, a pressure past 3.6 MPa). A cell whose field
#: is within ``SEAM_TOLERANCE`` cells of zero is looked at ``SEAM_STEP`` cells to either side,
#: along each axis and one oblique direction: inside on both sides of some direction, it is inside
#: (the larger of the two values). A true wall through the centre has the outside on one side and
#: stays as it was.
SEAM_TOLERANCE = 1e-3
SEAM_STEP = 1e-2
_SEAM_DIRECTIONS = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0), (1.0, 0.618034, 0.381966))


def _seam_resolved(zone: int) -> str:
    """``phi{zone}``: the zone's raw field ``phiRaw{zone}`` with its interior seams taken out."""
    lines = [
        f"fn phi{zone}(p: vec3f) -> f32 {{",
        f"  let v = phiRaw{zone}(p);",
        f"  if (v < 0.0 || v > {_f32(SEAM_TOLERANCE)} * P.dx) {{ return v; }}",
        f"  let e = {_f32(SEAM_STEP)} * P.dx;",
        "  var best = v;",
    ]
    for x, y, z in _SEAM_DIRECTIONS:
        step = f"vec3f({_f32(x)}, {_f32(y)}, {_f32(z)}) * e"
        lines.append(
            f"  {{ let s = max(phiRaw{zone}(p + {step}), phiRaw{zone}(p - {step}));"
            " if (s < 0.0) { best = min(best, s); } }"
        )
    lines += ["  return best;", "}"]
    return "\n".join(lines) + "\n"


def zone_fields(fields: list[tuple[str, bool]]) -> str:
    """The zones' fields as WGSL: ``phi0``, ``phi1``... (one per zone, each from ``compile_field``'s
    body with its interior seams taken out, ``SEAM_TOLERANCE``) and ``phiOf(code, p)``, the field
    of the zone a cell code names. The shader binds the params ``P`` (the cell size).

    ``fields`` holds each zone's compiled ``fn phi`` text and whether the zone is a solid.
    """
    parts = []
    cases = []
    for zone, (text, solid) in enumerate(fields):
        parts.append(text.replace("fn phi(", f"fn phiRaw{zone}(", 1))
        parts.append(_seam_resolved(zone))
        cases.append(f"  if (code == {zone_code(zone, solid)}u) {{ return phi{zone}(p); }}")
    parts.append(
        "fn phiOf(code: u32, p: vec3f) -> f32 {\n" + "\n".join(cases) + "\n  return 1.0;\n}\n"
    )
    return "\n".join(parts)


# ----------------------------------------------------------------------------------- the shaders


def classify_shader(fields: list[tuple[str, bool]], nx: int, ny: int, storage: str = "f32") -> str:
    """Cell codes and port adjacency from the zones' fields; cells that changed zone start at rest
    and at their zone's starting temperature."""
    lines = [
        "enable f16;" if storage == "f16" else "",
        PARAMS,
        f"@group(0) @binding(0) var<storage, read_write> f: array<{storage}>;",
        "@group(0) @binding(1) var<storage, read_write> flags: array<u32>;",
        "@group(0) @binding(2) var<uniform> P: Params;",
        "@group(0) @binding(3) var<storage, read> ports: array<f32>;",
        "@group(0) @binding(4) var<storage, read> masks: array<u32>;",
        "@group(0) @binding(5) var<storage, read_write> tcur: array<f32>;",
        "@group(0) @binding(6) var<storage, read_write> vel: array<vec4f>;",
        "@group(0) @binding(7) var<storage, read_write> mean: array<f32>;",
        _CELL,
        _PORTS,
        zone_fields(fields),
        "@compute @workgroup_size(64) fn main(@builtin(workgroup_id) wg: vec3u, @builtin(local_invocation_index) li: u32) {",
        "  let id = cellId(wg, li); if (id >= P.n) { return; }",
        "  let i = id % P.nx; let j = (id / P.nx) % P.ny; let k = id / (P.nx * P.ny);",
        "  let pos = posOf(i, j, k);",
        # The zone the cell is deepest inside, so bodies that overlap by round-off share no cell.
        "  var code = 1u; var ph = 0.0;",
    ]
    for zone, (_text, solid) in enumerate(fields):
        lines.append(
            f"  {{ let v = phi{zone}(pos); if (v < ph) {{ ph = v; code = {zone_code(zone, solid)}u; }} }}"
        )
    lines += [
        "  if (i == 0u || j == 0u || k == 0u || i == P.nx - 1u || j == P.ny - 1u || k == P.nz - 1u) { code = 1u; }",
        "  var adj = 0u;",
        "  if (isFluid(code)) {",
    ]
    for d in range(1, 19):
        lines += [
            f"    if (adj == 0u) {{ let ps = phiOf(code, pos - {_c(d)} * P.dx); if (ps >= 0.0) {{",
            "      let den = ph - ps; var q = 0.5; if (abs(den) > 1e-20) { q = clamp(ph / den, 0.0, 1.0); }",
            f"      let port = portAt(pos - {_c(d)} * (P.dx * q)); if (port >= 0) {{ adj = u32(port) + 1u; }}",
            "    } }",
        ]
    lines += [
        "  }",
        # The inlet profile's share at this cell (see ``INLET_TAPER``): the wall distance is read
        # ``INLET_PROBE`` cells inside the port, where the field no longer sees the port's own plane.
        "  var shape = 0u;",
        "  if (adj > 0u) {",
        f"    let inside = -phiOf(code, pos - portV(adj - 1u, 4u) * ({_f32(INLET_PROBE)} * P.dx));",
        f"    shape = u32(clamp(inside / ({_f32(INLET_TAPER)} * P.dx), 0.0, 1.0) * 65535.0);",
        "  }",
        "  let old = flags[id] & 0xffu;",
        "  flags[id] = code | (adj << 8u) | (shape << 16u);",
        "  if (isFluid(code) && (P.init_flag == 1u || old != code)) {",
        # The cell's own nineteen places (some are a neighbour's links: they start at rest too).
        *("    " + rest(storage, f"{d}u * P.n + id") for d in range(19)),
        "    vel[id] = vec4f(0.0); mean[id] = 0.0;",
        "  }",
        "  if (code != 1u && (P.init_flag == 1u || old != code)) { tcur[id] = zoneF(zoneOf(code), 7u); }",
        "}",
    ]
    return "\n".join(lines)


#: How far off a thermal wall's plane (in cells) a boundary face's centre may lie: the face is on
#: the lattice, the wall where the body ends, up to half a cell apart.
THERMAL_PLANE_TOLERANCE = 0.75


def thermal_shader(nx: int, ny: int) -> str:
    """One step of the temperature (ADR-0047 addendum 2): finite volumes on the lattice's cells,
    through the fluids and the solids alike.

    Conduction between two cells goes through the harmonic mean of their conductivities, which is
    what keeps the heat flux continuous where the conductivity jumps at a fluid-solid wall.
    Advection is between cells of one fluid only, second order with van Leer's limiter, written as
    ``u . grad T`` from the face fluxes (the fluxes' own divergence taken off), so a velocity
    field that is not discretely solenoidal makes no heat. A face on the outside of every body
    takes its zone's wall condition, or that of the thermal wall whose footprint holds it. The
    cells next to an inlet hold the inlet's temperature. Temperatures are stored less a reference.
    """
    lines = [
        PARAMS,
        "@group(0) @binding(0) var<storage, read> tsrc: array<f32>;",
        "@group(0) @binding(1) var<storage, read_write> tdst: array<f32>;",
        "@group(0) @binding(2) var<storage, read> flags: array<u32>;",
        "@group(0) @binding(3) var<uniform> P: Params;",
        "@group(0) @binding(4) var<storage, read> ports: array<f32>;",
        "@group(0) @binding(5) var<storage, read> masks: array<u32>;",
        "@group(0) @binding(6) var<storage, read> vel: array<vec4f>;",
        _CELL,
        _PORTS,
        # van Leer's limited slope from the upstream and the downstream difference.
        "fn lim(a: f32, b: f32) -> f32 { if (a * b <= 0.0) { return 0.0; } return sign(a) * min(min(2.0 * abs(a), 2.0 * abs(b)), 0.5 * abs(a + b)); }",
        "@compute @workgroup_size(64) fn main(@builtin(workgroup_id) wg: vec3u, @builtin(local_invocation_index) li: u32) {",
        "  let id = cellId(wg, li); if (id >= P.n) { return; }",
        "  let code = flags[id] & 0xffu;",
        "  if (code == 1u) { return; }",
        "  let zone = zoneOf(code);",
        "  let tc = tsrc[id];",
        "  let adj = (flags[id] >> 8u) & 0xffu;",
        "  if (adj > 0u && portF(adj - 1u, 3u) < 0.5) { tdst[id] = portF(adj - 1u, 24u); return; }",
        "  let kc = zoneF(zone, 2u);",
        "  let i = id % P.nx; let j = (id / P.nx) % P.ny; let k = id / (P.nx * P.ny);",
        "  let pos = posOf(i, j, k);",
        "  let uc = vel[id].xyz;",
        "  let moving = isFluid(code);",
        "  var acc = 0.0; var adv = 0.0;",
    ]
    for d in range(1, 7):
        off = _offset(d, nx, ny)
        lines += [
            f"  {{ let s = u32(i32(id) + ({off})); let cn = flags[s] & 0xffu;",
            "    if (cn == 1u) {",
            "      var mode = zoneF(zone, 4u); var value = zoneF(zone, 5u);",
            f"      let wall = anyPortAt(pos + {_c(d)} * (0.5 * P.dx), {_f32(THERMAL_PLANE_TOLERANCE)});",
            "      if (wall >= 0) { let kind = portF(u32(wall), 3u); mode = max(kind - 1.0, 0.0); value = portF(u32(wall), 24u); }",
            "      if (mode > 0.5 && mode < 1.5) { acc += 2.0 * kc * (value - tc); }",
            "      else if (mode > 1.5) { acc += value; }",
            "    } else {",
            "      let kn = zoneF(zoneOf(cn), 2u); let tn = tsrc[s];",
            "      acc += 2.0 * kc * kn / (kc + kn) * (tn - tc);",
            "      if (moving && cn == code) {",
            f"        let uf = 0.5 * dot({_c(d)}, uc + vel[s].xyz);",
            "        var tf = tc;",
            "        if (uf > 0.0) {",
            f"          let b = u32(i32(id) - ({off})); var tu = tc; if ((flags[b] & 0xffu) == code) {{ tu = tsrc[b]; }}",
            "          tf = tc + 0.5 * lim(tc - tu, tn - tc);",
            "        } else {",
            f"          let b = u32(i32(s) + ({off})); var tu = tn; if ((flags[b] & 0xffu) == code) {{ tu = tsrc[b]; }}",
            "          tf = tn + 0.5 * lim(tn - tu, tc - tn);",
            "        }",
            "        adv += uf * (tf - tc);",
            "      }",
            "    }",
            "  }",
        ]
    lines += ["  tdst[id] = tc + acc / zoneF(zone, 3u) - adv;", "}"]
    return "\n".join(lines)


def prolong_shader() -> str:
    """A lattice's velocity, density and temperature from another lattice of the same case (ADR-0047
    addendum 4, the warm start): trilinear between the other lattice's cell centres, among those
    of its eight cells round the point that belong to the same body, so nothing is taken across a
    wall. A cell with none of them keeps what the classification gave it (rest, the starting
    temperature). ``From`` has the other lattice's size and placement (mm) and what its lattice
    velocity and its density's deviation are worth in this lattice's units.
    """
    lines = [
        PARAMS,
        "struct From { nx: u32, ny: u32, nz: u32, pad: u32, ox: f32, oy: f32, oz: f32, dx: f32, velocity: f32, density: f32, pad1: f32, pad2: f32 };",
        "@group(0) @binding(0) var<storage, read> cvel: array<vec4f>;",
        "@group(0) @binding(1) var<storage, read> cflags: array<u32>;",
        "@group(0) @binding(2) var<storage, read> ctemp: array<f32>;",
        "@group(0) @binding(3) var<storage, read> flags: array<u32>;",
        "@group(0) @binding(4) var<storage, read_write> vel: array<vec4f>;",
        "@group(0) @binding(5) var<storage, read_write> temp: array<f32>;",
        "@group(0) @binding(6) var<uniform> P: Params;",
        "@group(0) @binding(7) var<uniform> C: From;",
        _CELL,
        "@compute @workgroup_size(64) fn main(@builtin(workgroup_id) wg: vec3u, @builtin(local_invocation_index) li: u32) {",
        "  let id = cellId(wg, li); if (id >= P.n) { return; }",
        "  let code = flags[id] & 0xffu;",
        "  if (code == 1u) { return; }",
        "  let i = id % P.nx; let j = (id / P.nx) % P.ny; let k = id / (P.nx * P.ny);",
        "  let at = (posOf(i, j, k) - vec3f(C.ox, C.oy, C.oz)) / C.dx - vec3f(0.5);",
        "  let base = floor(at); let t = at - base;",
        "  let bi = i32(base.x); let bj = i32(base.y); let bk = i32(base.z);",
        "  var sum = 0.0; var v = vec4f(0.0); var warm = 0.0;",
    ]
    for corner in range(8):
        a, b, c = corner & 1, (corner >> 1) & 1, (corner >> 2) & 1
        weight = " * ".join(
            f"t.{axis}" if bit else f"(1.0 - t.{axis})"
            for axis, bit in zip("xyz", (a, b, c), strict=True)
        )
        lines += [
            f"  {{ let ci = bi + {a}; let cj = bj + {b}; let ck = bk + {c};",
            "    if (ci >= 0 && cj >= 0 && ck >= 0 && ci < i32(C.nx) && cj < i32(C.ny) && ck < i32(C.nz)) {",
            "      let cid = u32(ci) + C.nx * (u32(cj) + C.ny * u32(ck));",
            f"      if ((cflags[cid] & 0xffu) == code) {{ let w = {weight}; sum += w; v += w * cvel[cid]; warm += w * ctemp[cid]; }}",
            "    } }",
        ]
    lines += [
        "  if (sum <= 1e-6) { return; }",
        "  temp[id] = warm / sum;",
        "  if (isFluid(code)) { vel[id] = vec4f(v.xyz * (C.velocity / sum), v.w * (C.density / sum)); }",
        "}",
    ]
    return "\n".join(lines)


def slice_shader() -> str:
    """The velocity and the density's deviation from 1 of every cell of an axis-aligned plane (the
    deviation is 1e30 for a cell that is not a fluid), then the plane again with each cell's
    temperature (less the reference) and its code."""
    lines = [
        PARAMS,
        "@group(0) @binding(0) var<storage, read> vel: array<vec4f>;",
        "@group(0) @binding(1) var<storage, read> flags: array<u32>;",
        "@group(0) @binding(2) var<uniform> P: Params;",
        "@group(0) @binding(3) var<storage, read_write> out: array<vec4f>;",
        "@group(0) @binding(4) var<storage, read> tsrc: array<f32>;",
        _CELL,
        "@compute @workgroup_size(8, 8) fn main(@builtin(global_invocation_id) g: vec3u) {",
        "  var n1 = P.nx; var n2 = P.ny;",
        "  if (P.axis == 0u) { n1 = P.ny; n2 = P.nz; } else if (P.axis == 1u) { n1 = P.nx; n2 = P.nz; }",
        "  if (g.x >= n1 || g.y >= n2) { return; }",
        "  var i = g.x; var j = g.y; var k = P.plane;",
        "  if (P.axis == 0u) { i = P.plane; j = g.x; k = g.y; } else if (P.axis == 1u) { i = g.x; j = P.plane; k = g.y; }",
        "  let id = i + P.nx * (j + P.ny * k);",
        "  let at = g.x + n1 * g.y;",
        "  let code = flags[id] & 0xffu;",
        "  out[n1 * n2 + at] = vec4f(tsrc[id], f32(code), 0.0, 0.0);",
        "  if (!isFluid(code)) { out[at] = vec4f(0.0, 0.0, 0.0, 1e30); return; }",
        "  out[at] = vel[id];",
        "}",
    ]
    return "\n".join(lines)


def gather_shader() -> str:
    """The velocity and the density's deviation from 1 of a list of cells (those next to a port),
    then each cell's temperature (less the reference)."""
    lines = [
        "@group(0) @binding(0) var<storage, read> vel: array<vec4f>;",
        "@group(0) @binding(1) var<storage, read> cells: array<u32>;",
        "@group(0) @binding(2) var<storage, read_write> out: array<vec4f>;",
        "@group(0) @binding(3) var<storage, read> tsrc: array<f32>;",
        "@compute @workgroup_size(64) fn main(@builtin(global_invocation_id) g: vec3u) {",
        "  let count = arrayLength(&cells);",
        "  if (g.x >= count) { return; }",
        "  let id = cells[g.x];",
        "  out[g.x] = vel[id];",
        "  out[count + g.x] = vec4f(tsrc[id], 0.0, 0.0, 0.0);",
        "}",
    ]
    return "\n".join(lines)
