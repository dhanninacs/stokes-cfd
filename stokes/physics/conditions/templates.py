"""The tags the geometry operations give, and the condition each becomes (ADR-0022 decision
10.1; ADR-0021 decisions 6.1 and 6.2).

An enclosure names the faces of the flow volume it made: ``inlet`` and ``outlet`` where the flow
enters and leaves, ``symmetry`` on a symmetry plane, ``ground`` on the floor when one was asked
for, ``far_field`` on the rest of the enclosure's own faces and ``walls`` on the faces the bodies
it cut contributed. A fluid extraction names its openings ``caps``. One table per template maps
such a name to the condition kind it becomes and the values that kind takes, so ``setup.defaults``
(``stokes/api/commands/setup.py``) reads both flows from one place instead of hard-coding them, and
``docs/engineering/setup-schema.md`` is the contract clients read.

Two rules the tables state and the command keeps: a tag qualifies by its name, exactly, and only
when it holds faces of the part the command was given; ``walls`` is last because ``setup.defaults``
extends it with every face of that part which no tag holds (``far_field`` is a wall that slips, so
an external flow keeps its far field instead of growing a no-slip boundary).

Import rule: the standard library only at module level, as the rest of ``stokes.physics.conditions``
requires (``stokes.api`` may load this package with its command modules and nothing here may pull
in numpy, OCP or a provider). Nothing is imported at all: the tables are literals.
"""

from __future__ import annotations

Template = dict[str, tuple[str, dict[str, str]]]
"""One template: a tag name to the condition kind it becomes and the values that kind takes."""

EXTERNAL: Template = {
    "inlet": ("velocity_inlet", {}),
    "outlet": ("pressure_outlet", {}),
    "symmetry": ("symmetry", {}),
    "far_field": ("wall", {"shear": "slip"}),
    "ground": ("wall", {}),
    "walls": ("wall", {}),
}
"""The external flow (after an enclosure, ADR-0021 6.2): the far field slips, everything else is
a no-slip wall."""

INTERNAL: Template = {
    "inlet": ("velocity_inlet", {}),
    "outlet": ("pressure_outlet", {}),
    "walls": ("wall", {}),
}
"""The internal flow (after a fluid extraction, ADR-0021 6.1): the openings the user names are
the inlet and the outlet, and every other face is a no-slip wall (the extraction's ``caps``
included, which ``setup.defaults`` counts in a warning)."""

TEMPLATES: dict[str, Template] = {"external": EXTERNAL, "internal": INTERNAL}
"""The two templates by the name the ``template`` parameter of ``setup.defaults`` takes."""
