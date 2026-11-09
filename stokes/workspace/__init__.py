"""The workspace (ADR-0015): everything the user arranges in the interface, as data.

A versioned JSON document describes the theme (a built-in one or a custom token
set), where panels, islands and top-bar items sit, how the tree is organised,
the shortcuts, the viewport defaults and the default engine. The user's document
(``~/.stokes/workspace.json``) and a project's overrides
(``<name>.stokes/workspace.json``) merge over the defaults into the effective
workspace that the front end renders and the ``workspace.*`` commands read and
write. Presets ship in ``presets/``. The contract is
``docs/engineering/workspace-schema.md``; the theme tokens are
``docs/engineering/theme-tokens.md``.

Modules: ``schema`` (vocabulary, rules, sanitising), ``defaults`` (built-in ids
and the default document), ``tokens`` (the token contract), ``keys`` (chords),
``paths`` and ``access`` (addressing values), ``files`` (bounded, atomic IO),
``merge`` (layers to the effective document), ``catalog`` (presets), ``store``
(the per-session service).

The workspace is not project model state: no undo stack, nothing in
``project.json``. Import rule: headless; the standard library plus
``stokes.errors`` and ``stokes.log`` only; nothing from ``stokes.model``,
``stokes.api`` or any client, so ``import stokes.api`` stays cheap.
"""

from stokes.workspace.schema import SCHEMA_VERSION
from stokes.workspace.store import WorkspaceStore

__all__ = ["SCHEMA_VERSION", "WorkspaceStore"]
