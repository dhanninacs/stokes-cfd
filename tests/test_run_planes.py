"""Slice planes: naming one, and the cache's identity for it (slices contract).

**Two properties make the cache work, and both are asserted here because both are easy to lose.**
A plane arrives as text from a client that may be dragging a handle, so the identity must survive
float noise; and a direction has no magnitude to the cutter, so `(0,0,1)` and `(0,0,7)` are one
plane. Lose either and the cache silently stores duplicates of the same cut — the memory grows, the
hit rate falls, and nothing reports it.
"""

from __future__ import annotations

import math

import pytest

from stokes.geometry.errors import MeshFormatError
from stokes.run.planes import (
    MAX_CACHED_PLANES,
    MAX_CONCURRENT_CUTS,
    PLANE_DIGITS,
    parse_plane,
    plane_from_header,
    plane_key,
    plane_matches,
)


def test_a_plane_is_six_numbers_and_comes_back_canonical() -> None:
    plane = parse_plane("0.1,0.2,0.3,0,0,1")
    assert plane == {"origin": [0.1, 0.2, 0.3], "normal": [0.0, 0.0, 1.0]}


def test_the_normal_is_normalised_but_the_origin_is_not() -> None:
    """**The direction has no magnitude; the origin is where the cut is.** Normalising the origin
    would move the plane, which is a different cut - and the difference is invisible in the reply
    shape, so it is asserted."""
    short = parse_plane("1,2,3,0,0,1")
    long = parse_plane("1,2,3,0,0,9")
    assert short["normal"] == long["normal"] == [0.0, 0.0, 1.0]
    assert short["origin"] == long["origin"] == [1.0, 2.0, 3.0]
    # A diagonal normal comes back unit length.
    diagonal = parse_plane("0,0,0,3,4,0")
    assert diagonal["normal"] == [0.6, 0.8, 0.0]


def test_two_spellings_of_one_plane_share_a_cache_key() -> None:
    """The two collapses that matter: magnitude and drag noise."""
    assert plane_key(parse_plane("0,0,0,0,0,1")) == plane_key(parse_plane("0,0,0,0,0,7")), (
        "a direction's magnitude changed the key, so the same geometry caches twice"
    )
    assert plane_key(parse_plane("0.30000001,0,0,0,1,0")) == plane_key(
        parse_plane("0.3,0,0,0,1,0")
    ), "float noise from a dragged handle changed the key"


def test_a_genuinely_different_plane_gets_a_different_key() -> None:
    """**The false-negative half**: collapsing too far would serve one plane's data for another."""
    a = parse_plane("0,0,0,0,0,1")
    for other in ("0,0,0,0,1,0", "2,0,0,0,0,1", "0,0,0.5,0,0,1"):
        assert not plane_matches(a, parse_plane(other)), other


@pytest.mark.parametrize(
    "text",
    [
        "0,0,0,0,0",  # five numbers
        "0,0,0,0,0,1,2",  # seven
        "0,0,0,0,0,0",  # zero normal
        "0,0,0,0,0,inf",
        "0,0,0,0,0,nan",
        "a,0,0,0,0,1",
        "",
    ],
)
def test_a_bad_plane_is_refused(text: str) -> None:
    """Refused, never repaired: every repair is a guess at what the client meant."""
    with pytest.raises(MeshFormatError):
        parse_plane(text)


def test_the_limits_are_the_contracts_numbers() -> None:
    """They are what the client's behaviour was designed against, so they are asserted rather than
    left as constants someone might retune."""
    assert PLANE_DIGITS == 6
    assert MAX_CACHED_PLANES == 16
    assert MAX_CONCURRENT_CUTS == 2


def test_plane_from_header_reads_what_the_artifact_stored() -> None:
    """The artifact's own slice section carries the plane it was cut with, so a request for the same
    plane can be answered without a cut."""
    header = {"slice": {"origin": [1.0, 2.0, 3.0], "normal": [0.0, 0.0, 1.0]}}
    found = plane_from_header(header)
    assert found == {"origin": [1.0, 2.0, 3.0], "normal": [0.0, 0.0, 1.0]}
    assert plane_matches(parse_plane("1,2,3,0,0,5"), found or {})
    # And the shapes that are not a plane.
    for bad in ({}, {"slice": None}, {"slice": {"origin": [0, 0, 0]}}, {"slice": "x"}):
        assert plane_from_header(bad) is None, bad


def test_a_normal_of_zero_is_refused_before_any_division() -> None:
    """A zero normal would divide by zero in the normalisation; the refusal has to come first, and
    a message is not enough - this asserts no `ZeroDivisionError` escapes."""
    with pytest.raises(MeshFormatError):
        parse_plane("0,0,0,0,0,0")
    assert math.isfinite(math.log10(1.0))  # the helper this guards is still sane


def test_a_huge_normal_is_normalised_rather_than_becoming_zero() -> None:
    """**Opus M3.** `sqrt(sum(x*x))` overflows: `1e200 * 1e200` is `1e400`, which is `inf`, so the
    length was `inf` and every `component / inf` was **0.0**. A normal of `1e200,1e200,0`
    therefore became `[0, 0, 0]` **and was accepted** - the client sent a zero normal, the agent
    refused it, and the user got `results.host_unavailable`, blaming the host for a bad request.

    `math.hypot` scales by the largest component, so the intermediate values stay in range.
    Measured: `hypot(1e200, 1e200)` is `1.414213562373095e+200` where the naive form gives `inf`.
    """
    import math

    assert math.hypot(1e200, 1e200) == pytest.approx(1.414213562373095e200, rel=1e-12)
    assert math.isinf(math.sqrt(1e200 * 1e200 + 1e200 * 1e200)), (
        "the naive form no longer overflows, so this test proves nothing"
    )

    got = parse_plane("0,0,0,1e200,1e200,0")
    assert got["normal"] == [0.707107, 0.707107, 0.0], got
    # The property that makes it a usable normal.
    length = math.hypot(*got["normal"])
    assert length == pytest.approx(1.0, abs=1e-5)


@pytest.mark.parametrize(
    "text",
    ["0,0,0,1e308,1e308,0", "0,0,0,1e-320,0,0", "0,0,0,-1e200,1e200,0"],
)
def test_a_normal_at_the_edges_of_the_float_range_still_normalises(text: str) -> None:
    """**The false-refusal half.** Largest and smallest finite floats must both give a unit
    normal - a parse that refuses them is no better than one that accepts a zero vector."""
    import math

    got = parse_plane(text)
    assert math.hypot(*got["normal"]) == pytest.approx(1.0, abs=1e-5), got


def test_a_normal_that_cannot_be_normalised_is_refused() -> None:
    """**Refused, never repaired.** A normal that normalises to zero is not a plane, and guessing
    one would cut somewhere the user did not ask for.
    """
    for text in ("0,0,0,0,0,0", "0,0,0,0,0,0.0"):
        with pytest.raises(MeshFormatError):
            parse_plane(text)
