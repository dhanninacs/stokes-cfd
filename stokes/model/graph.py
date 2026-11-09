"""Derivation edges and staleness between nodes (docs/02 §4).

A node that was computed from others records their ids in
``meta["derives_from"]``. When any of those change, the dependents become
``stale`` until recomputed. Expression dependencies between parameters are a
separate, finer-grained mechanism inside :class:`~stokes.model.project.Project`.
"""

from __future__ import annotations

from stokes.model.project import Project

DERIVES_FROM = "derives_from"


def derive(project: Project, node_ref: str, *source_refs: str) -> None:
    """Record that ``node_ref`` was computed from ``source_refs``."""
    node = project.get(node_ref)
    ids = [project.get(ref).id for ref in source_refs]
    node.meta[DERIVES_FROM] = sorted(set(ids))


def dependents(project: Project, node_ref: str) -> list[str]:
    """Ids of nodes that transitively derive from ``node_ref``."""
    start = project.get(node_ref).id
    found: list[str] = []
    frontier = [start]
    while frontier:
        current = frontier.pop()
        for node in project.nodes():
            sources = node.meta.get(DERIVES_FROM, [])
            if current in sources and node.id not in found:
                found.append(node.id)
                frontier.append(node.id)
    return found


def mark_stale(project: Project, node_ref: str) -> list[str]:
    """Mark every dependent of ``node_ref`` stale; returns their ids."""
    ids = dependents(project, node_ref)
    for node_id in ids:
        project.get(node_id).status = "stale"
    return ids
