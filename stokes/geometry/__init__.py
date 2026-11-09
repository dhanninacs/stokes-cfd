"""CAD geometry: STEP import, tessellation, display meshes and their cache (ADR-0003, ADR-0014);
tags, mesh checks and STL export (ADR-0020 slice 1).

``files`` and ``errors`` (standard library) check sources and name failures for the API layer;
``mesh`` and ``transport`` (numpy) hold display meshes with face ids and their binary form;
``step`` wraps OCP; ``worker`` runs it in a separate process (``worker_main`` is the code that
runs inside it); ``store`` keeps each session's part meshes, one per solid (decision G1), runs
imports and writes ``cache``; ``commands`` holds the undoable import. ``sample`` is the
synthetic demo part.

Selections: ``model`` (standard library) declares the ``tag`` node type;
``topology`` welds a part's mesh into shared edges and face adjacency; ``selection`` holds the
members, their arithmetic and feature-angle growth; ``fingerprints`` describes faces and
re-matches them after a re-import; ``selection_cache`` keeps both per session and writes the
fingerprint archive; ``checks`` checks tessellations; ``export`` writes STL. The contract is
``docs/engineering/geometry-schema.md``.

Operations (ADR-0021 decision 8): ``model`` declares their node types too; ``shapes`` is the
shape cache (every part's B-rep on disk) and the provider worker jobs load parts through;
``operations`` holds one module per operation type (``primitive``, ``boolean`` ...) and their
contract, run in the worker by ``worker_main``; ``opplan`` builds a run's job from the tree,
``opstore`` is the session's operations service (runs, outputs, staleness, re-runs), ``carry``
moves tags through a run's history, and ``opcommands`` holds the undoable create and delete.

Import rule: the only package besides the Qt host and server-side post-processing that may
import OCP, and only inside functions, so that importing any module here never loads OCCT.
Nothing here imports the server or a client; the store and the operations service name
``stokes.api.Session`` for type checking only.
"""
