"""Refinement levels: the integer a dictionary needs, and what it actually achieves.

**The brief's cases are the point of the module.** A level is a power of two, so a requested size
that is not one lands between two levels; the module must pick the closer one and say how far off
it is, because the alternative is a user whose "0.5 mm wall refinement" silently became 0.4 mm or
0.8 mm with nothing in the mesh result to say so.
"""

from __future__ import annotations

import pytest

from stokes.mesh.levels import DEVIATION_WARNING, MAX_LEVEL, level_for, level_pair


def test_the_briefs_exact_case_is_level_2_with_no_deviation() -> None:
    """base 8 mm, size 2 mm: 8/2 = 4 = 2**2, so level 2 and the size is hit exactly."""
    choice = level_for(0.008, 0.002)
    assert choice.level == 2
    assert choice.deviation == 0.0
    assert not choice.warns


def test_a_size_between_levels_picks_the_closer_one_and_reports_the_gap() -> None:
    """**The brief's second case.** 3 mm from an 8 mm base: log2(8/3) = 1.415, so level 1 gives
    4 mm (33 % coarser) and level 2 gives 2 mm (33 % finer). Rounding sends 1.415 to 1, and the
    deviation is large enough to warn - which is the honest outcome, not a silent choice."""
    choice = level_for(0.008, 0.003)
    assert choice.level == 1, "1.415 rounds to 1"
    assert choice.achieved == pytest.approx(0.004)
    assert choice.deviation == pytest.approx(1 / 3, abs=1e-9)
    assert choice.warns, "a third coarser than asked is worth saying out loud"


def test_a_size_at_or_above_the_base_is_level_zero() -> None:
    """A cell cannot be coarser than the background block: asking for 20 mm from an 8 mm base
    means "do not refine", and the brief's clamp is a floor at 0, not a negative level."""
    assert level_for(0.008, 0.020).level == 0
    assert level_for(0.008, 0.008).level == 0
    assert level_for(0.008, 0.008).deviation == 0.0


def test_the_level_is_clamped_and_SAYS_SO() -> None:
    """A micron on a metre part is level ~20; the cap keeps the dictionary bounded, and ``clamped``
    is what lets the caller warn instead of pretending the size was met."""
    choice = level_for(1.0, 1e-6)
    assert choice.level == MAX_LEVEL
    assert choice.clamped
    assert choice.warns
    # The achieved size is what the cap can reach, not what was asked.
    assert choice.achieved == pytest.approx(1.0 / 2**MAX_LEVEL)


def test_half_the_size_is_exactly_one_more_level() -> None:
    """The definition of a level, pinned: halving the target adds exactly one."""
    for base, size in ((0.008, 0.004), (0.008, 0.002), (0.1, 0.05), (1.0, 0.25)):
        coarse = level_for(base, size)
        finer = level_for(base, size / 2)
        assert finer.level == coarse.level + 1
        assert finer.deviation == 0.0


@pytest.mark.parametrize("bad", [0.0, -1.0, float("nan"), float("inf")])
def test_a_nonsense_base_is_refused_by_name(bad: float) -> None:
    """Arithmetic on a bad length would return a level and a NaN deviation; refusing names which
    argument was wrong, which is what a caller can act on."""
    with pytest.raises(ValueError, match="base size"):
        level_for(bad, 0.001)


@pytest.mark.parametrize("bad", [0.0, -1.0, float("nan"), float("inf")])
def test_a_nonsense_target_is_refused_by_name(bad: float) -> None:
    with pytest.raises(ValueError, match="target size"):
        level_for(0.008, bad)


def test_the_pair_is_the_surface_level_plus_one_by_default() -> None:
    """`level (min max)` is how OF14 expresses curvature refinement: a cell touching the surface
    gets `min`, one on a curve or a sharp junction gets `max`."""
    low, high = level_pair(0.008, 0.002)
    assert (low, high) == (2, 3)


def test_a_pair_at_the_cap_does_not_exceed_it() -> None:
    """`min` at the cap must not push `max` past it, or the dictionary asks for a level the
    background cannot produce."""
    low, high = level_pair(1.0, 1e-12)
    assert low == MAX_LEVEL
    assert high == MAX_LEVEL


def test_an_explicit_fine_size_is_honoured() -> None:
    low, high = level_pair(0.008, 0.004, fine=0.001)
    assert low == 1
    assert high == 3


def test_a_fine_size_at_or_below_the_surface_size_is_allowed() -> None:
    """`level (2 2)` is a legitimate "uniform at level 2" and snappy accepts it - the pair is not
    forced to be strictly increasing."""
    low, high = level_pair(0.008, 0.002, fine=0.004)
    assert low == 2
    assert high == 1


def test_the_warning_threshold_is_the_one_the_constant_names() -> None:
    """Pinned so the boundary is a decision rather than an accident: a deviation just under the
    threshold is quiet, just over is not."""
    base, size = 0.008, 0.008 / (2**1.1)  # level 1, ~7 % off
    quiet = level_for(base, size)
    assert quiet.deviation < DEVIATION_WARNING
    assert not quiet.warns

    loud = level_for(0.008, 0.003)  # level 1, 33 % off
    assert loud.deviation > DEVIATION_WARNING
    assert loud.warns
