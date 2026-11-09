"""Host names shared by the mesh and solver settings (ADR-0024).

A host is `local`, an SSH record's name, or `wsl:<distro>`. **The WSL form is why the settings
cannot use the SSH rule**: a distribution name is mixed case (`Ubuntu-24.04`) and may contain a
space, while an SSH record is deliberately lowercase and has no colon. Both `/mesh/settings` and
`/solver/settings` use this one pattern, so a host a user can pick in Compute is a host they can
type into either form.

`WSL_PREFIX` stays in `stokes.run.hosts`: this module is imported by the model schema, and that
package must not load a runner (or the agent) at module level. Nothing is imported here at all,
which is what keeps `import stokes.api` inside its budget.
"""

from __future__ import annotations

#: An SSH host name, `wsl:` and a distribution name, or empty. **Empty means the Compute default**
#: (ADR-0024): a project that does not name a host uses the machine chosen in Settings, so a user
#: who never opens the Execution section still meshes where the Compute section says. The schema
#: wraps this in `^...$`, so the group is optional rather than the last character.
HOST_NAME_PATTERN = r"(?:[a-z0-9][a-z0-9_.-]{0,62}|wsl:[A-Za-z0-9][A-Za-z0-9 ._+-]{0,127})?"

__all__ = ["HOST_NAME_PATTERN"]
