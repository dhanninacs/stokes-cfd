"""stokes-agent: runs where the engines run (ADR-0005).

Owns the engine host's disk: probing installs, building in-house solvers,
staging cases, supervising processes with heartbeats, parsing logs and
extracting results with VTK. The desktop talks to it over the length-prefixed
msgpack protocol in :mod:`stokes_agent.protocol`.

Import rule: pure Python plus numpy, msgpack and (later) VTK. No Qt, no OCP,
no OpenFOAM libraries.
"""

__version__ = "0.0.1.dev0"
