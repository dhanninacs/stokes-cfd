"""The surface-size resolution (brief M-503): one function, every caller.

``surface_size`` was a **dead setting** — the snappy snapshot never read it, so a user could set
a global surface size and nothing changed, which is the failure class the guided-meshing plan
exists to kill (the withdrawn 2026-10-18 ``layers`` died the same way). This module is the one
place the precedence lives, and both callers — the snapshot builder, which writes the
dictionary, and the Sizing page and body page, which show what will really be used — go through
it, so **the page cannot disagree with the dictionary**.

## The precedence, in one sentence

For one tag's surface: **boundary size > domain body size > the mesh settings' ``surface_size``
(when > 0) > the rule** — a wall gets half the base size, any other face the base size. The
rule is the behaviour every project had before any of these controls existed, so an untouched
project resolves exactly as it did, byte for byte (the golden test is that).

## Pure, and what that means

No session, no store, no tree: the caller reads the stored parameters and passes plain numbers,
with ``None`` for "not set" — the empty state of the two no-default parameters
(``boundary.mesh_size``, ``part.mesh_size``; the same shape the layer counts use).
"""

from __future__ import annotations

from stokes.mesh.levels import LevelChoice, level_for


def surface_target(
    *,
    boundary_size: float | None,
    body_size: float | None,
    surface_size: float,
    base_size: float,
    patch_type: str,
) -> float:
    """The size one surface aims for, by the precedence above.

    ``patch_type`` is ``"wall"`` or a non-wall type such as ``"patch"``/``"symmetryPlane"`` —
    the snapshot's own spellings. The rule that
    answers when nothing is set is the one the snapshot carried before this module existed: a
    wall at half the base size (walls benefit from a finer surface than a far-field patch),
    anything else at the base size.
    """
    for candidate in (boundary_size, body_size):
        if candidate is not None and candidate > 0:
            return float(candidate)
    if surface_size > 0:
        return float(surface_size)
    return base_size / 2.0 if patch_type == "wall" else base_size


def achieved_for(base_size: float, target: float) -> LevelChoice:
    """The level the target size really achieves, and the deviation of that from the ask.

    The placeholder and the Sizing page's achieved column read this — the same ``level_for``
    the snapshot's ``level_pair`` uses, so the shown level is the dictionary's level.
    """
    return level_for(base_size, target)
