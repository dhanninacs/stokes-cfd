"""Whether a Fast run's frames are valid, and when a run has run away (ADR-0047 addendum 5).

A lattice that goes unstable without going NaN keeps reporting numbers: measured 2026-11-23 on a
gyroid block at a throat cell Reynolds number of about 400 (tau 0.5003), the pressure drop jumped
from 13 kPa to 132 kPa and the outflow ran 1.1 to 2.1 times the inflow, for minutes, with no
failure and nothing to say the numbers were wrong. Each frame is judged here once the inlets have
ramped and the flow had time to settle; a frame with any sign of a runaway is not valid
(``Frame.unstable``, with the sign in ``Frame.instability``), and a run whose recent frames are
mostly such is a runaway: the runner lowers the lattice speed and starts it again from rest, as
the guard does, and after ``RECOVERIES`` such restarts (or at the slowest lattice speed) it fails.

An imbalance alone is not a runaway (measured 2026-11-24): a restrictive laminar core fills with
pressure for seconds after its inlets ramp (an orifice duct at a hole Reynolds number of 0.4:
outflow rising smoothly from 0.4 to 0.7 of the inflow, the guard idle), and its outflow approaches
the inflow from one side. The runaways measured came with the guard holding cells (orifice ducts
at cell Reynolds numbers 8 and 21, the forced-laminar duct) or swung through balance (the gyroid
block's 1.07 to 2.13, an oscillation through 0.64 and 1.34). So an imbalance is a sign with the
guard on in the same frame, or when it does not die away (``settling``); in a creeping flow
(``CREEPING_REYNOLDS``) only when both hold: the same duct at 0.05 m/s filled with the guard on
while its lattice speed was halved, and ended steady and balanced.

Pure, so the rule is tested without a GPU.
"""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass, field

#: **The most the outflow may differ from the inflow** (a share of it) in a valid frame once the
#: flow has settled. A stable run's differ by a few per cent at most (the panel warns at 5 % on a
#: steady one); the runaway measured above was 7 % to 113 %, and a laminar lattice past its cell
#: Reynolds number 13 % to 46 %.
IMBALANCE_MAX = 0.25
#: **The largest density deviation at a port** (the pressure in lattice units) a valid frame has:
#: past it the lattice's weak compressibility is no model of a liquid. A restrictive core starts at
#: 0.4 and the guard's speed halving brings it under 0.1 (measured on the same block).
DENSITY_MAX = 0.5
#: **The share of cell-steps the guard may hold** in a valid frame. A restrictive core's guard holds
#: about 1e-4 before its speed is halved; a laminar lattice past its limit held every cell.
GUARD_SHARE_MAX = 0.01
#: A run has run away when at least ``RUNAWAY_SHARE`` of its last ``WINDOW`` judged frames were
#: not valid. A share, not a run of frames: an oscillating runaway passes through valid-looking
#: values (out/in 0.64, 1.34, 0.76 measured).
WINDOW = 20
RUNAWAY_SHARE = 0.5
#: Restarts at a lower lattice speed (or after a NaN) a run gets before it fails.
RECOVERIES = 2
#: **A cell Reynolds number below which an imbalance is never a runaway while the guard rests**: the
#: lattice is far inside its stable range (stable at 6.3 on a square duct; the measured runaways
#: were at 7.8 and up, their throats several times that), and a flow this viscous only fills.
CREEPING_REYNOLDS = 1.0


def settling(imbalance: float, before: Sequence[float]) -> bool:
    """Whether an imbalance is dying away: no larger than the largest of the older half of the
    ``before`` frames' (oldest first), and of the same sign as all of them past ``IMBALANCE_MAX``.
    A lattice filling with pressure (or draining after an edit) approaches balance from one side;
    an oscillation that keeps its amplitude, or grows, does not. Too few frames to tell: yes."""
    if len(before) < 2:
        return True
    older = before[: len(before) // 2]
    if any(b * imbalance < 0.0 and abs(b) > IMBALANCE_MAX for b in before):
        return False  # through balance and out again: an oscillation
    newer = [*before[len(before) // 2 :], imbalance]
    return max(abs(b) for b in newer) < max(abs(b) for b in older)


def instability(
    dp: float,
    flow_in: float,
    flow_out: float,
    swing: float,
    guard: int,
    cell_steps: float,
    nan: bool = False,
    before: Sequence[float] = (),
    reynolds: float = math.inf,
) -> str:
    """The first sign that a frame is not valid, or "": ``not_finite`` (the lattice went NaN or a
    figure is not a number), ``density`` (a port's density deviation ``swing`` past
    ``DENSITY_MAX``), ``guard`` (the guard held more than ``GUARD_SHARE_MAX`` of the frame's
    ``cell_steps``), ``imbalance`` (outflow and inflow apart by more than ``IMBALANCE_MAX`` while
    the guard holds cells, or not ``settling`` against the ``before`` frames' imbalances). In a
    creeping flow (a cell Reynolds number ``reynolds`` below ``CREEPING_REYNOLDS``) an imbalance
    is a sign only when the guard holds cells and it does not settle: the guard there is the
    restrictive core's, which the runner's speed halving answers."""
    if nan or not all(math.isfinite(v) for v in (dp, flow_in, flow_out, swing)):
        return "not_finite"
    if swing > DENSITY_MAX:
        return "density"
    if cell_steps > 0.0 and guard / cell_steps > GUARD_SHARE_MAX:
        return "guard"
    if flow_in > 0.0:
        imbalance = (flow_out - flow_in) / flow_in
        creeping = reynolds < CREEPING_REYNOLDS
        if (
            abs(imbalance) > IMBALANCE_MAX
            and not (creeping and guard == 0)
            and ((guard > 0 and not creeping) or not settling(imbalance, before))
        ):
            return "imbalance"
    return ""


@dataclass
class Health:
    """The judged frames of one lattice start: whether the run has run away."""

    recent: deque[str] = field(default_factory=lambda: deque(maxlen=WINDOW))
    imbalances: deque[float] = field(default_factory=lambda: deque(maxlen=WINDOW))
    """The judged frames' imbalances (outflow less inflow over inflow), oldest first."""
    recoveries: int = 0
    """Restarts already made for a runaway or a NaN."""

    def judge(self, sign: str, imbalance: float = 0.0) -> bool:
        """Record one judged frame's sign ("" for a valid frame) and imbalance; True when the run
        has run away: NaN at once, otherwise ``RUNAWAY_SHARE`` of a full window."""
        self.recent.append(sign)
        if math.isfinite(imbalance):
            self.imbalances.append(imbalance)
        if sign == "not_finite":
            return True
        if len(self.recent) < WINDOW:
            return False
        return sum(1 for s in self.recent if s) >= RUNAWAY_SHARE * WINDOW

    def restart(self) -> None:
        """The lattice starts again from rest: a new window, one recovery spent."""
        self.clear()
        self.recoveries += 1

    def clear(self) -> None:
        """A new window: the lattice started again from rest (the frames before say nothing)."""
        self.recent.clear()
        self.imbalances.clear()

    @property
    def spent(self) -> bool:
        """No recovery left: the next runaway fails the run."""
        return self.recoveries >= RECOVERIES

    def reason(self) -> str:
        """The commonest sign in the window, for the message."""
        signs = [s for s in self.recent if s]
        return max(set(signs), key=signs.count) if signs else ""
