"""The implicit expression graph, its evaluator, frames and the slice 1 builders (ADR-0035
decisions 4 to 7): golden text, limits, the builder rules as property tests, transforms and sharp
booleans. No mesher and no session."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from stokes.implicit import features as F  # noqa: N812 - the builders read as F.box, F.sphere
from stokes.implicit import frame as frames
from stokes.implicit import graph
from stokes.implicit.errors import ImplicitInvalid, ImplicitTooLarge, ImplicitUnbounded
from stokes.implicit.evaluate import evaluate, gradient
from stokes.implicit.graph import Expr, X, Y, Z, lift, to_text

GOLDEN = Path(__file__).parent / "golden" / "implicit"

CASES: dict[str, F.Body] = {
    "box": F.box((10.0, 20.0, 30.0), (1.0, 2.0, 3.0)),
    "rounded_box": F.rounded_box((10.0, 20.0, 30.0), (0.0, 0.0, 0.0), 2.0),
    "cylinder_x": F.cylinder(4.0, 12.0, (0.0, 0.0, 0.0), "x"),
    "cone_y": F.cone(5.0, 10.0, 1.0, (0.0, 0.0, 0.0), "y"),
    "sphere": F.sphere(5.0, (1.0, -2.0, 3.0)),
    "torus_z": F.torus(10.0, 2.0, (0.0, 0.0, 0.0), "z"),
    "capsule_z": F.capsule(2.0, 8.0, (0.0, 0.0, 0.0), "z"),
    "half_space": F.half_space((0.0, 0.0, 5.0), "+z"),
}


# ------------------------------------------------------------------------------- the graph
@pytest.mark.parametrize("name", sorted(CASES))
def test_every_primitive_writes_its_golden_text(name: str) -> None:
    text = to_text(CASES[name].field())
    golden = GOLDEN / f"{name}.txt"
    assert golden.read_text(encoding="utf-8") == text


def test_shared_nodes_and_equal_constants_are_written_once() -> None:
    a = (X.square() + Y.square()).sqrt()
    root = graph.minimum(a - 1.0, a - 1.0)
    lines = to_text(root).splitlines()
    assert sum(1 for line in lines if line.endswith("var-x")) == 1
    assert sum(1 for line in lines if " const 1.0" in line) == 1
    assert lines[-1].split()[1] == "min"


def test_the_root_is_always_the_last_line() -> None:
    for body in CASES.values():
        lines = to_text(body.field()).splitlines()
        names = [line.split()[0] for line in lines]
        assert len(set(names)) == len(names)
        assert all(
            arg in names[:i]
            for i, line in enumerate(lines)
            for arg in line.split()[2:]
            if line.split()[1] != "const"
        )


def test_constants_must_be_finite_and_in_range() -> None:
    with pytest.raises(ImplicitInvalid):
        lift(math.inf)
    with pytest.raises(ImplicitInvalid):
        lift(math.nan)
    with pytest.raises(ImplicitInvalid):
        lift(2e9)
    with pytest.raises(ImplicitInvalid):
        Expr("exp", (X,))  # not in the closed set
    with pytest.raises(ImplicitInvalid):
        Expr("add", (X,))  # wrong arity


def test_a_graph_over_the_node_limit_is_refused() -> None:
    root: Expr = X
    for i in range(graph.MAX_NODES // 2 + 1):
        root = root + float(i)
    with pytest.raises(ImplicitTooLarge):
        graph.check(root)


def test_a_deep_graph_does_not_hit_the_recursion_limit() -> None:
    root: Expr = X
    for _ in range(20_000):
        root = root + 1.0
    assert graph.check(root) == 40_001  # x, then a constant and an add per step
    assert evaluate(root, [[0.0, 0.0, 0.0]])[0] == pytest.approx(20_000.0)


def test_the_evaluator_follows_the_mesher_semantics() -> None:
    point = np.array([[-7.5, 4.0, 0.25]])
    assert evaluate(X.mod(5.0), point)[0] == pytest.approx(2.5)  # Euclidean: in [0, 5)
    assert evaluate((X).sqrt(), point)[0] == 0.0  # sqrt of a negative is 0
    assert evaluate(graph.atan2(Y, X), point)[0] == pytest.approx(math.atan2(4.0, -7.5))
    assert evaluate(Z.sin() + Z.cos(), point)[0] == pytest.approx(math.sin(0.25) + math.cos(0.25))


# ------------------------------------------------------------------------------- builder rules
def _rays(count: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    v = rng.normal(size=(count, 3))
    return v / np.linalg.norm(v, axis=1, keepdims=True)


BOUNDED = {k: v for k, v in CASES.items() if k != "half_space"}


@pytest.mark.parametrize("name", sorted(BOUNDED))
def test_the_field_is_negative_inside_and_positive_outside(name: str) -> None:
    """Rule 1: along rays from a point inside, the field changes sign once, at the surface."""
    body = BOUNDED[name]
    root = body.field()
    low, high = body.bounded()
    centre = np.array([(a + b) / 2 for a, b in zip(low, high, strict=True)])
    if name == "torus_z":
        centre = centre + np.array([10.0, 0.0, 0.0])  # inside the tube, not in the hole
    reach = 2.0 * max(b - a for a, b in zip(low, high, strict=True))
    steps = np.linspace(0.0, reach, 400)
    for direction in _rays(40, 1):
        line = centre + steps[:, None] * direction
        values = evaluate(root, line)
        assert values[0] < 0.0
        assert values[-1] > 0.0
        if name != "torus_z":  # a ray through a torus may cross it twice
            assert int(np.count_nonzero(np.diff(np.sign(values)))) == 1


@pytest.mark.parametrize("name", ["rounded_box", "sphere", "torus_z", "capsule_z"])
def test_exact_bodies_have_a_unit_gradient_near_the_surface(name: str) -> None:
    body = CASES[name]
    assert body.metric == "exact"
    rng = np.random.default_rng(3)
    low, high = body.bounded()
    points = rng.uniform(low, high, size=(4000, 3))
    root = body.field()
    values = evaluate(root, points)
    near = points[np.abs(values) < 0.5]
    assert len(near) > 20
    norms = np.linalg.norm(gradient(root, near), axis=1)
    assert np.allclose(norms, 1.0, atol=1e-3)


@pytest.mark.parametrize("name", ["box", "cylinder_x", "cone_y"])
def test_bound_bodies_never_overstate_the_distance(name: str) -> None:
    body = CASES[name]
    assert body.metric == "bound"
    rng = np.random.default_rng(5)
    low, high = body.bounded()
    points = rng.uniform(np.array(low) - 5, np.array(high) + 5, size=(4000, 3))
    norms = np.linalg.norm(gradient(body.field(), points), axis=1)
    assert float(norms.max()) <= 1.0 + 1e-6


def test_a_sharp_corner_is_a_max_and_never_a_sqrt_of_zero() -> None:
    """Rule 2: below the sharp radius a rounded box is the sharp box, and no sqrt appears."""
    sharp = F.rounded_box((10.0, 10.0, 10.0), (0.0, 0.0, 0.0), F.SHARP_RADIUS / 2)
    assert "sqrt" not in to_text(sharp.field())
    assert sharp.metric == "bound"
    rounded = F.rounded_box((10.0, 10.0, 10.0), (0.0, 0.0, 0.0), 1.0)
    on_face = np.array([[5.0, 0.0, 0.0], [0.0, -5.0, 1.0], [2.0, 3.0, 5.0]])
    assert np.allclose(evaluate(rounded.field(), on_face), 0.0, atol=1e-12)
    assert np.isfinite(gradient(rounded.field(), on_face)).all()


@settings(max_examples=60, deadline=None)
@given(
    radius=st.floats(0.01, 1000.0),
    cx=st.floats(-1e4, 1e4),
    cy=st.floats(-1e4, 1e4),
    cz=st.floats(-1e4, 1e4),
)
def test_a_sphere_is_a_distance_wherever_it_is(
    radius: float, cx: float, cy: float, cz: float
) -> None:
    body = F.sphere(radius, (cx, cy, cz))
    probe = np.array([[cx + 2 * radius, cy, cz], [cx, cy, cz], [cx, cy + radius, cz]])
    values = evaluate(body.field(), probe)
    assert values == pytest.approx(
        [radius, -radius, 0.0], abs=1e-9 * max(1.0, abs(cx), abs(cy), abs(cz), radius)
    )


@settings(max_examples=60, deadline=None)
@given(
    size=st.tuples(st.floats(0.01, 100.0), st.floats(0.01, 100.0), st.floats(0.01, 100.0)),
    radius=st.floats(0.0, 10.0),
)
def test_a_rounded_box_keeps_its_faces_where_the_box_has_them(
    size: tuple[float, float, float], radius: float
) -> None:
    body = F.rounded_box(size, (0.0, 0.0, 0.0), radius)
    face_centres = np.array([[size[0] / 2, 0, 0], [0, -size[1] / 2, 0], [0, 0, size[2] / 2]])
    assert np.allclose(evaluate(body.field(), face_centres), 0.0, atol=1e-9)
    assert evaluate(body.field(), [[0.0, 0.0, 0.0]])[0] < 0.0


def test_dimensions_that_are_not_positive_are_refused() -> None:
    with pytest.raises(ImplicitInvalid):
        F.box((1.0, 0.0, 1.0), (0.0, 0.0, 0.0))
    with pytest.raises(ImplicitInvalid):
        F.sphere(-1.0, (0.0, 0.0, 0.0))
    with pytest.raises(ImplicitInvalid):
        F.torus(2.0, 3.0, (0.0, 0.0, 0.0), "z")  # tube wider than the ring
    with pytest.raises(ImplicitInvalid):
        F.cone(0.0, 5.0, 0.0, (0.0, 0.0, 0.0), "z")
    with pytest.raises(ImplicitInvalid):
        F.half_space((0.0, 0.0, 0.0), "z")


# ------------------------------------------------------------------------------- frames
def test_the_identity_frame_gives_the_bare_leaves() -> None:
    u, v, w = frames.IDENTITY.leaves()
    assert (u, v, w) == (X, Y, Z)


def test_transforms_are_composed_once_at_the_leaves() -> None:
    """Rule 3: ten nested transforms cost one affine expression per coordinate."""
    body = F.sphere(2.0, (0.0, 0.0, 0.0))
    for i in range(10):
        body = F.transformed(body, frames.translation((1.0, 0.0, 0.0)))
        body = F.transformed(body, frames.rotation((0.0, 0.0, 1.0), 0.1 * i))
    lines = to_text(body.field()).splitlines()
    assert len(lines) < 30  # a sphere is 11 lines; the frame adds at most 3 x 3 products and sums


def test_a_rotation_moves_the_body_and_its_box() -> None:
    bar = F.box((10.0, 2.0, 2.0), (5.0, 0.0, 0.0))
    turned = F.transformed(bar, frames.rotation((0.0, 0.0, 1.0), math.pi / 2))
    values = evaluate(turned.field(), [[0.0, 9.0, 0.0], [9.0, 0.0, 0.0]])
    assert values[0] < 0.0 < values[1]
    low, high = turned.bounded()
    assert low == pytest.approx((-1.0, 0.0, -1.0), abs=1e-9)
    assert high == pytest.approx((1.0, 10.0, 1.0), abs=1e-9)


def test_a_rotation_about_a_centre_keeps_the_centre() -> None:
    ball = F.sphere(1.0, (5.0, 0.0, 0.0))
    turned = F.transformed(ball, frames.rotation((0.0, 0.0, 1.0), 1.234, (5.0, 0.0, 0.0)))
    assert evaluate(turned.field(), [[5.0, 0.0, 0.0]])[0] == pytest.approx(-1.0)


def test_a_mirror_and_a_scale() -> None:
    ball = F.sphere(1.0, (3.0, 0.0, 0.0))
    mirrored = F.transformed(ball, frames.mirror((1.0, 0.0, 0.0)))
    assert evaluate(mirrored.field(), [[-3.0, 0.0, 0.0]])[0] == pytest.approx(-1.0)
    grown = F.transformed(ball, frames.uniform_scale(2.0))
    # Centre (6, 0, 0), radius 2, and still a distance: 1 from the surface at (9, 0, 0).
    assert evaluate(grown.field(), [[6.0, 0.0, 0.0], [9.0, 0.0, 0.0]]) == pytest.approx([-2.0, 1.0])
    assert grown.smallest == pytest.approx(4.0) and grown.metric == "exact"
    with pytest.raises(ImplicitInvalid):
        frames.uniform_scale(0.0)


def test_a_frame_and_its_inverse_cancel() -> None:
    f = frames.rotation((1.0, 2.0, 3.0), 0.7, (1.0, -1.0, 2.0)).then(
        frames.translation((4.0, 5.0, 6.0))
    )
    p = (0.3, -0.2, 0.9)
    assert f.inverse().map(f.map(p)) == pytest.approx(p)


# ------------------------------------------------------------------------------- booleans
def test_sharp_booleans_and_their_boxes() -> None:
    a = F.box((10.0, 10.0, 10.0), (0.0, 0.0, 0.0))
    b = F.sphere(3.0, (5.0, 0.0, 0.0))
    joined = F.union([a, b])
    common = F.intersection([a, b])
    cut = F.difference(a, [b])
    probe = np.array([[7.0, 0.0, 0.0], [4.5, 0.0, 0.0], [0.0, 0.0, 0.0]])
    assert (evaluate(joined.field(), probe) < 0).tolist() == [True, True, True]
    assert (evaluate(common.field(), probe) < 0).tolist() == [False, True, False]
    assert (evaluate(cut.field(), probe) < 0).tolist() == [False, False, True]
    assert joined.bounded()[1][0] == pytest.approx(8.0)
    assert common.bounded() == ((2.0, -3.0, -3.0), (5.0, 3.0, 3.0))
    assert cut.bounded() == a.bounded()
    assert joined.metric == common.metric == cut.metric == "bound"
    assert joined.smallest == pytest.approx(6.0)


def test_a_half_space_is_unbounded_until_it_is_intersected() -> None:
    cut_plane = F.half_space((0.0, 0.0, 1.0), "+z")
    with pytest.raises(ImplicitUnbounded):
        cut_plane.bounded()
    halved = F.intersection([F.sphere(5.0, (0.0, 0.0, 0.0)), cut_plane])
    assert halved.bounded() == ((-5.0, -5.0, -5.0), (5.0, 5.0, 1.0))
    with pytest.raises(ImplicitInvalid, match="do not overlap"):
        F.intersection([F.sphere(1.0, (0.0, 0.0, 0.0)), F.sphere(1.0, (10.0, 0.0, 0.0))])
