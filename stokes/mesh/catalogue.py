"""The mesher catalogue: what Stokes can drive, and what it cannot yet (ADR-0024, T-143).

One entry per mesher, with ``built`` saying whether the driver exists. **A mesher that is not built
is listed rather than hidden**, and it carries the reason: a user who cannot find cfMesh in the list
will assume Stokes forgot it, while a user who reads "cfMesh waits for its OpenFOAM 14 build gate"
knows the answer and knows where it stands.

Standard library only, so importing this costs nothing at the ``import stokes.api`` budget.
"""

from __future__ import annotations

from dataclasses import dataclass

from stokes.mesh.errors import MeshInvalid


@dataclass(frozen=True)
class MesherSpec:
    """One mesher.

    ``domains`` is what it can mesh: ``("part", "box")`` for blockMesh, which builds a block
    either from
    a part's bounding box or from a box the user gives. A mesher with no entries cannot be
    chosen at all
    yet — it exists in the list to say so.
    """

    id: str
    label: str
    built: bool
    domains: tuple[str, ...]
    why: str
    #: The cell families the validated recipe produces (ADR-0032), from the closed set
    #: `structured-hexahedral`, `hex-dominant`, `tetrahedral`, `polyhedral`, `poly-hexcore` - the
    #: Fluent Meshing vocabulary a user looks for. Empty for a mesher that is not built.
    output_structures: tuple[str, ...] = ()
    #: How `cores` is spent: `mpi` (ranks), `threads` (OpenMP) or `serial` (ignored).
    parallel: str = "serial"

    def to_json(self) -> dict[str, object]:
        """As a client reads it: id, label, built, domains, why, output_structures, parallel."""
        return {
            "id": self.id,
            "label": self.label,
            "built": self.built,
            "domains": list(self.domains),
            "why": self.why,
            "output_structures": list(self.output_structures),
            "parallel": self.parallel,
        }


#: Every mesher Stokes knows about, in the order the UI shows them.
MESHERS: tuple[MesherSpec, ...] = (
    # **`why` is not only for the unbuilt ones.** blockMesh is the one mesher that works, and
    # its limitation is the thing a user most needs told: it meshes the part's BOUNDING BOX in
    # one block, so a round body comes out a box and anything inside it cannot become a patch.
    # Measured on the demo pipe: 14,112 cells, every quality check perfect, `walls` absent and
    # 4,704 faces `unassigned` - and the panel said nothing about why. An empty `why` here is
    # why the owner asked "why does it turn into a duct?".
    MesherSpec(
        "blockmesh",
        "blockMesh",
        True,
        ("part", "box"),
        "meshes the bounding box in one block: a round part comes out a box, and faces inside it "
        "cannot become patches. Use snappyHexMesh for the shape itself.",
        ("structured-hexahedral",),
        "serial",
    ),
    # **Built, and the DEFAULT** (the owner, via claude-win2's 1045 message: "make snappyHexMesh
    # work and make it default so that any CAD that user loads is meshable, blockMesh is user
    # selectable"). `domains` is `("part", "enclosure")` - a body's own volume for internal flow, or
    # a wind-tunnel box around it for external - where blockMesh offers `("part", "box")`, its
    # bounding-box shortcut.
    MesherSpec(
        "snappy",
        "snappyHexMesh",
        True,
        ("part", "enclosure"),
        "follows the shape: the boundary patches are the part's own surfaces, so a round part "
        "stays round.",
        ("hex-dominant",),
        "mpi",
    ),
    # **The cfMesh family and the poly-hexcore dual** (ADR-0032). Each meshes the inside of the
    # part's closed surface (`part` domain only, for now) and keeps the tag names as patches.
    # Measured on the Flanged pipe at the default sizes: cartesian 30 k cells in 8 s, tet 706 k
    # in 32 s, poly 191 k in 43 s, poly-hexcore 45 k in 9 s; every one passed checkMesh once the
    # patch types were written.
    MesherSpec(
        "cfmesh",
        "Hex-dominant (cfMesh cartesianMesh)",
        True,
        ("part",),
        "a Cartesian hex core trimmed to the shape, with polyhedra at the wall: few cells, "
        "excellent quality. Meshes a part's own volume.",
        ("hex-dominant",),
        "threads",
    ),
    MesherSpec(
        "cfmesh_tet",
        "Tetrahedral (cfMesh tetMesh)",
        True,
        ("part",),
        "tetrahedra everywhere: follows any shape, but needs about ten times the cells of a "
        "hex-dominant mesh for the same size. Meshes a part's own volume.",
        ("tetrahedral",),
        "threads",
    ),
    MesherSpec(
        "cfmesh_poly",
        "Polyhedral (cfMesh pMesh)",
        True,
        ("part",),
        "polyhedral cells: fewer cells than tetrahedra for the same size, and well-behaved "
        "gradients. Meshes a part's own volume.",
        ("polyhedral",),
        "threads",
    ),
    MesherSpec(
        "polyhex",
        "Poly-hexcore (cartesianMesh + polyDualMesh)",
        True,
        ("part",),
        "hexahedra in the core and polyhedra in the transitions and at the wall: the dual of a "
        "Cartesian mesh, layers included. Meshes a part's own volume.",
        ("poly-hexcore",),
        "threads",
    ),
    MesherSpec(
        "gmsh",
        "Gmsh",
        False,
        (),
        "Gmsh ships with FoamWin but has no driver yet; use the cfMesh tetrahedral type for "
        "tetrahedra",
    ),
)


def mesher(mesher_id: str) -> MesherSpec:
    """The spec for ``mesher_id``, or `MeshInvalid` naming the ones that exist."""
    for spec in MESHERS:
        if spec.id == mesher_id:
            return spec
    known = ", ".join(spec.id for spec in MESHERS)
    raise MeshInvalid(f"no mesher {mesher_id!r}; known: {known}")


def meshers_json() -> list[dict[str, object]]:
    """The catalogue as a client reads it."""
    return [spec.to_json() for spec in MESHERS]
