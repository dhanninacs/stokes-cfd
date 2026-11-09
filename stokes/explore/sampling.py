"""Design-of-experiments samplers (ADR-0016): full factorial, Latin hypercube, Sobol, Halton,
central composite, Box-Behnken, optimal space filling and Monte Carlo.

Every sampler takes variables (a sequence of ``Variable`` or a ``DesignSpace``), is deterministic
for a given ``seed`` and returns a ``DesignTable`` whose metadata records the sampler, the seed and
its options. The space-filling samplers draw points of the unit cube and map them through each
variable's quantile function, so a uniform range, a truncated normal, an explicit value list and
a categorical set all work with the same code. The classical response-surface designs (central
composite, Box-Behnken) are defined for continuous variables only, in coded units around the
middle of each variable's bounds.

``unit_*`` functions return raw unit-cube points for callers that want them (candidate pools in
the surrogate-assisted loop, initial designs).

Import rule: numpy at module level; scipy lazily through ``stokes.explore._imports``.
"""

from __future__ import annotations

import math
import warnings
from collections.abc import Callable, Mapping, Sequence
from itertools import combinations, product
from typing import Literal

import numpy as np

from stokes.explore._imports import scipy_module
from stokes.explore.arrays import Floats, Json
from stokes.explore.errors import DomainError, SamplerError
from stokes.explore.space import DesignSpace, Variable
from stokes.explore.table import DesignTable

MAX_FACTORIAL_POINTS = 1_000_000
CcdKind = Literal["faced", "circumscribed", "inscribed"]
SpaceFillingCriterion = Literal["maximin", "discrepancy"]

_SAMPLER_ALIASES = {
    "full_factorial": "full_factorial",
    "factorial": "full_factorial",
    "lhs": "latin_hypercube",
    "latin_hypercube": "latin_hypercube",
    "sobol": "sobol",
    "halton": "halton",
    "ccd": "central_composite",
    "central_composite": "central_composite",
    "box_behnken": "box_behnken",
    "bbd": "box_behnken",
    "osf": "optimal_space_filling",
    "optimal_space_filling": "optimal_space_filling",
    "monte_carlo": "monte_carlo",
    "mc": "monte_carlo",
}


def _count(n: int, what: str = "n", *, minimum: int = 1) -> int:
    try:
        number = int(n)
        valid = number == n and number >= minimum
    except (TypeError, ValueError):
        valid = False
    if not valid:
        raise SamplerError(f"{what} must be an integer of at least {minimum}, got {n!r}")
    return number


def _table(
    space: DesignSpace, points: Floats, sampler: str, seed: int | None, **options: Json
) -> DesignTable:
    metadata: dict[str, Json] = {"sampler": sampler, "seed": seed, "n": int(points.shape[0])}
    metadata.update(options)
    return DesignTable(space.variables, points, metadata)


def _require_continuous(space: DesignSpace, sampler: str) -> None:
    discrete = [v.name for v in space.variables if v.domain.is_discrete]
    if discrete:
        raise SamplerError(
            f"{sampler} needs continuous variables; discrete: {discrete}. "
            "Use a space-filling sampler or a full factorial for those.",
            details={"variables": discrete},
        )


# ---------------------------------------------------------------- unit-cube points


def unit_latin_hypercube(
    d: int, n: int, seed: int = 0, *, centered: bool = False, optimization: str | None = None
) -> Floats:
    """``n`` points of a Latin hypercube in ``[0, 1)^d``: exactly one point per stratum of width
    ``1/n`` in every dimension. ``optimization`` is scipy's ``"random-cd"`` or ``"lloyd"``."""
    if optimization not in (None, "random-cd", "lloyd"):
        raise SamplerError(f"unknown LHS optimization {optimization!r}")
    qmc = scipy_module("scipy.stats.qmc")
    engine = qmc.LatinHypercube(
        d=_count(d, "d"), scramble=not centered, optimization=optimization, rng=seed
    )
    return np.asarray(engine.random(_count(n)), dtype=np.float64)


def unit_sobol(d: int, n: int, seed: int = 0, *, scramble: bool = True) -> Floats:
    """The first ``n`` points of a (scrambled) Sobol' sequence. Its balance properties hold for
    powers of two; any other ``n`` is a valid prefix of the sequence."""
    n = _count(n)
    qmc = scipy_module("scipy.stats.qmc")
    try:
        engine = qmc.Sobol(d=_count(d, "d"), scramble=scramble, rng=seed)
    except ValueError as exc:
        raise SamplerError(str(exc)) from exc
    if n & (n - 1) == 0:
        return np.asarray(engine.random_base2(n.bit_length() - 1), dtype=np.float64)
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="The balance properties of Sobol'")
        return np.asarray(engine.random(n), dtype=np.float64)


def unit_halton(d: int, n: int, seed: int = 0, *, scramble: bool = True) -> Floats:
    """The first ``n`` points of a (scrambled) Halton sequence."""
    qmc = scipy_module("scipy.stats.qmc")
    try:
        engine = qmc.Halton(d=_count(d, "d"), scramble=scramble, rng=seed)
    except ValueError as exc:
        raise SamplerError(str(exc)) from exc
    return np.asarray(engine.random(_count(n)), dtype=np.float64)


def unit_random(d: int, n: int, seed: int = 0) -> Floats:
    """``n`` independent uniform points of ``[0, 1)^d``."""
    return np.random.default_rng(seed).random((_count(n), _count(d, "d")))


def _maximin_search(
    unit: Floats, rng: np.random.Generator, iterations: int, exponent: float
) -> Floats:
    """Improve a Latin hypercube by exchanging two entries of one column (which keeps it Latin)
    whenever that lowers the Morris-Mitchell criterion, a smooth stand-in for maximin: the
    sum over pairs of ``(d_ref / d_ij) ** p``. Distances are updated in O(n) per proposal."""
    n, d = unit.shape
    x = unit.copy()
    sq = ((x[:, None, :] - x[None, :, :]) ** 2).sum(axis=2)
    off = ~np.eye(n, dtype=bool)
    ref = float(sq[off].min())
    half = exponent / 2.0
    terms = np.zeros((n, n))
    terms[off] = (ref / sq[off]) ** half
    for _ in range(iterations):
        col = int(rng.integers(d))
        a = int(rng.integers(n))
        b = int(rng.integers(n - 1))
        b += b >= a
        xa, xb = x[a, col], x[b, col]
        delta = (xb - x[:, col]) ** 2 - (xa - x[:, col]) ** 2
        keep = np.ones(n, dtype=bool)
        keep[[a, b]] = False
        new_a, new_b = sq[a, keep] + delta[keep], sq[b, keep] - delta[keep]
        if new_a.min() <= 0.0 or new_b.min() <= 0.0:
            continue
        with np.errstate(over="ignore"):
            terms_a, terms_b = (ref / new_a) ** half, (ref / new_b) ** half
            change = terms_a.sum() + terms_b.sum() - terms[a, keep].sum() - terms[b, keep].sum()
        if not change < 0.0:
            continue
        x[a, col], x[b, col] = xb, xa
        sq[a, keep] = sq[keep, a] = new_a
        sq[b, keep] = sq[keep, b] = new_b
        terms[a, keep] = terms[keep, a] = terms_a
        terms[b, keep] = terms[keep, b] = terms_b
    return x


def min_distance(unit: Floats) -> float:
    """Smallest pairwise Euclidean distance of the rows (a space-filling quality measure)."""
    n = unit.shape[0]
    if n < 2:
        return math.inf
    sq = ((unit[:, None, :] - unit[None, :, :]) ** 2).sum(axis=2)
    return float(np.sqrt(sq[~np.eye(n, dtype=bool)].min()))


# ---------------------------------------------------------------- the samplers


def full_factorial(
    variables: Sequence[Variable] | DesignSpace,
    levels: int | Mapping[str, int] | None = None,
    *,
    max_points: int = MAX_FACTORIAL_POINTS,
) -> DesignTable:
    """Every combination of the variables' levels.

    Value lists, categories and ranges with a count or a step bring their own levels;
    ``levels`` gives the count for the others (an ``int`` for all of them, or a mapping from
    variable name to count, which also overrides a range's own grid). The first variable varies
    slowest, so points that share the leading variables are consecutive: put the variables that
    are expensive to change, such as geometry, first.
    """
    space = DesignSpace.of(variables)
    if isinstance(levels, Mapping):
        unknown = sorted(set(levels) - set(space.names))
        if unknown:
            raise SamplerError(f"levels given for unknown variables {unknown}")
    grids: list[Floats] = []
    for variable in space.variables:
        explicit = levels.get(variable.name) if isinstance(levels, Mapping) else None
        default = levels if isinstance(levels, int) else None
        try:
            if explicit is not None:
                grid = variable.domain.levels(_count(explicit, f"levels[{variable.name!r}]"))
            else:
                try:
                    grid = variable.domain.levels(None)
                except DomainError:
                    if default is None:
                        raise SamplerError(
                            f"variable {variable.name!r} needs a level count: pass levels=..."
                        ) from None
                    grid = variable.domain.levels(_count(default, "levels"))
        except DomainError as exc:
            raise SamplerError(f"variable {variable.name!r}: {exc.message}") from exc
        grids.append(grid)
    total = math.prod(len(g) for g in grids)
    if total > max_points:
        raise SamplerError(
            f"the full factorial has {total} points, above the limit of {max_points}",
            details={"points": total, "limit": max_points},
        )
    mesh = np.meshgrid(*grids, indexing="ij")
    points = np.stack([m.ravel() for m in mesh], axis=1)
    return _table(space, points, "full_factorial", None, levels=[len(g) for g in grids])


def latin_hypercube(
    variables: Sequence[Variable] | DesignSpace,
    n: int,
    seed: int = 0,
    *,
    centered: bool = False,
    optimization: str | None = None,
) -> DesignTable:
    """Latin hypercube: one point in every one of ``n`` equal-probability strata of each variable.

    ``centered`` puts every point at the middle of its stratum; ``optimization`` asks scipy to
    lower the centered discrepancy (``"random-cd"``) or to run Lloyd's algorithm (``"lloyd"``).
    """
    space = DesignSpace.of(variables)
    unit = unit_latin_hypercube(space.size, n, seed, centered=centered, optimization=optimization)
    return _table(
        space,
        space.from_unit(unit),
        "latin_hypercube",
        seed,
        centered=centered,
        optimization=optimization,
    )


def sobol(
    variables: Sequence[Variable] | DesignSpace,
    n: int,
    seed: int = 0,
    *,
    scramble: bool = True,
) -> DesignTable:
    """Sobol' low-discrepancy sequence, scrambled with ``seed`` (powers of two balance best)."""
    space = DesignSpace.of(variables)
    unit = unit_sobol(space.size, n, seed, scramble=scramble)
    return _table(space, space.from_unit(unit), "sobol", seed, scramble=scramble)


def halton(
    variables: Sequence[Variable] | DesignSpace,
    n: int,
    seed: int = 0,
    *,
    scramble: bool = True,
) -> DesignTable:
    """Halton low-discrepancy sequence, scrambled with ``seed``."""
    space = DesignSpace.of(variables)
    unit = unit_halton(space.size, n, seed, scramble=scramble)
    return _table(space, space.from_unit(unit), "halton", seed, scramble=scramble)


def monte_carlo(variables: Sequence[Variable] | DesignSpace, n: int, seed: int = 0) -> DesignTable:
    """Independent samples from each variable's distribution (uniform, truncated normal, ...)."""
    space = DesignSpace.of(variables)
    return _table(space, space.from_unit(unit_random(space.size, n, seed)), "monte_carlo", seed)


def optimal_space_filling(
    variables: Sequence[Variable] | DesignSpace,
    n: int,
    seed: int = 0,
    *,
    criterion: SpaceFillingCriterion = "maximin",
    iterations: int | None = None,
    exponent: float = 50.0,
) -> DesignTable:
    """A Latin hypercube optimised for space filling.

    ``"maximin"`` (default) maximises the smallest distance between two points by exchanging
    coordinates inside a seeded random Latin hypercube; ``iterations`` bounds the search (default
    ``100 * n``, between 2000 and 20000) and ``exponent`` is the Morris-Mitchell exponent.
    ``"discrepancy"`` lets scipy minimise the centered discrepancy instead.
    """
    space = DesignSpace.of(variables)
    n = _count(n)
    d = space.size
    if criterion == "discrepancy":
        unit = unit_latin_hypercube(d, n, seed, optimization="random-cd")
        options: dict[str, Json] = {"criterion": criterion}
    elif criterion == "maximin":
        rng = np.random.default_rng(seed)
        ranks = np.stack([rng.permutation(n) for _ in range(d)], axis=1)
        start = (ranks + rng.random((n, d))) / n
        steps = int(iterations) if iterations is not None else min(20000, max(2000, 100 * n))
        unit = start if n < 3 else _maximin_search(start, rng, steps, float(exponent))
        options = {
            "criterion": criterion,
            "iterations": steps,
            "initial_min_distance": min_distance(start),
        }
    else:
        raise SamplerError(f"unknown space-filling criterion {criterion!r}")
    options["min_distance"] = min_distance(unit)
    return _table(space, space.from_unit(unit), "optimal_space_filling", seed, **options)


def _coded_design(space: DesignSpace, coded: Floats, sampler: str, **options: Json) -> DesignTable:
    """Map coded coordinates (0 is the middle of the bounds, +/-1 the bounds) to values."""
    box = space.bounds
    middle = box.mean(axis=1)
    half = (box[:, 1] - box[:, 0]) / 2.0
    return _table(space, middle + coded * half, sampler, None, **options)


def _cube(k: int, *, half_fraction: bool) -> Floats:
    corners = np.array(list(product((-1.0, 1.0), repeat=k)))
    if half_fraction:
        corners = corners[np.prod(corners, axis=1) > 0.0]
    return corners


def central_composite(
    variables: Sequence[Variable] | DesignSpace,
    kind: CcdKind = "faced",
    *,
    center_points: int = 1,
    alpha: float | None = None,
    half_fraction: bool | None = None,
) -> DesignTable:
    """Central composite design: the corners of a cube, ``2k`` axial (star) points and
    ``center_points`` copies of the centre, ``2**k + 2k + center_points`` points in all.

    In coded units, with the variable bounds at +/-1:

    * ``"faced"``: axial points on the faces of the cube (alpha = 1); the whole design stays
      inside the bounds.
    * ``"circumscribed"``: cube corners at +/-1, axial points at +/-alpha, so the star points lie
      *outside* the bounds. The default alpha makes the design rotatable, ``alpha = F**0.25``
      with ``F`` the number of cube points.
    * ``"inscribed"``: the circumscribed design scaled by ``1/alpha`` so that the axial points sit
      on the bounds and the cube shrinks inside them.

    From five variables on the cube is by default a half fraction (defining relation
    ``I = x1 x2 ... xk``, resolution V or higher), the usual practice; ``half_fraction`` forces
    either choice. One centre point is enough for deterministic simulations.
    """
    space = DesignSpace.of(variables)
    _require_continuous(space, "central composite design")
    k = space.size
    if k < 2:
        raise SamplerError("a central composite design needs at least two variables")
    if kind not in ("faced", "circumscribed", "inscribed"):
        raise SamplerError(f"unknown central composite kind {kind!r}")
    centre_count = _count(center_points, "center_points", minimum=0)
    if kind == "faced" and alpha is not None:
        raise SamplerError("alpha applies to circumscribed and inscribed designs only")
    use_half = k >= 5 if half_fraction is None else bool(half_fraction)
    cube = _cube(k, half_fraction=use_half)
    if kind == "faced":
        alpha_used = 1.0
    else:
        alpha_used = float(alpha) if alpha is not None else float(len(cube)) ** 0.25
        if not alpha_used > 0.0:
            raise SamplerError(f"alpha must be positive, got {alpha!r}")
    cube_scale = 1.0 / alpha_used if kind == "inscribed" else 1.0
    axial_scale = 1.0 if kind == "inscribed" else alpha_used
    axial = axial_scale * np.vstack([np.eye(k), -np.eye(k)])
    coded = np.vstack([cube * cube_scale, axial, np.zeros((centre_count, k))])
    return _coded_design(
        space,
        coded,
        "central_composite",
        kind=kind,
        center_points=centre_count,
        alpha=alpha_used,
        fraction="half" if use_half else "full",
        n_factorial=len(cube),
        n_axial=2 * k,
    )


def box_behnken(
    variables: Sequence[Variable] | DesignSpace, *, center_points: int = 1
) -> DesignTable:
    """Box-Behnken design: for every pair of variables the four corners of their square with all
    other variables at the middle, plus ``center_points`` centre points; ``4 * k (k - 1) / 2 +
    center_points`` points, all inside the bounds. It needs at least three variables. For six or
    more variables this is the all-pairs construction, larger than the incomplete-block designs
    in the original paper."""
    space = DesignSpace.of(variables)
    _require_continuous(space, "Box-Behnken design")
    k = space.size
    if k < 3:
        raise SamplerError("a Box-Behnken design needs at least three variables")
    centre_count = _count(center_points, "center_points", minimum=0)
    rows: list[Floats] = []
    for i, j in combinations(range(k), 2):
        for si, sj in product((-1.0, 1.0), repeat=2):
            row = np.zeros(k)
            row[i], row[j] = si, sj
            rows.append(row)
    rows.extend(np.zeros(k) for _ in range(centre_count))
    return _coded_design(
        space,
        np.array(rows),
        "box_behnken",
        center_points=centre_count,
        construction="all_pairs",
    )


_SAMPLERS: dict[str, Callable[..., DesignTable]] = {
    "full_factorial": full_factorial,
    "latin_hypercube": latin_hypercube,
    "sobol": sobol,
    "halton": halton,
    "central_composite": central_composite,
    "box_behnken": box_behnken,
    "optimal_space_filling": optimal_space_filling,
    "monte_carlo": monte_carlo,
}
_FIXED_SIZE = {"full_factorial", "central_composite", "box_behnken"}


def sampler_names() -> tuple[str, ...]:
    """Canonical sampler names accepted by ``generate``."""
    return tuple(_SAMPLERS)


def generate(
    sampler: str,
    variables: Sequence[Variable] | DesignSpace,
    n: int | None = None,
    seed: int = 0,
    **options: object,
) -> DesignTable:
    """Run a sampler by name: the single entry point for ``study.generate``.

    ``n`` is the number of points for the samplers that take one; the classical designs and the
    full factorial have a size of their own and refuse it. ``seed`` is ignored by the
    deterministic designs. Other keyword arguments go to the sampler.
    """
    name = _SAMPLER_ALIASES.get(sampler.lower().replace("-", "_").replace(" ", "_"))
    if name is None:
        raise SamplerError(
            f"unknown sampler {sampler!r}; known: {sampler_names()}",
            details={"known": list(sampler_names())},
        )
    function = _SAMPLERS[name]
    if name in _FIXED_SIZE:
        if n is not None:
            raise SamplerError(f"{name} has a fixed number of points; do not pass n")
        return function(variables, **options)
    if n is None:
        raise SamplerError(f"{name} needs the number of points n")
    return function(variables, n, seed, **options)
