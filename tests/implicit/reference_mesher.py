"""A reference implicit mesher for the tests: the command line and output of
``stokes-implicit-mesh`` (ADR-0035 decision 14), done slowly in numpy by marching tetrahedra.

It reads the model text, evaluates it on the cube's grid with :mod:`stokes.implicit.evaluate`,
splits every cell into six tetrahedra that share the main diagonal (so neighbouring cells agree on
every face and the surface is closed), places one vertex per crossed grid edge (so vertices are
shared and the mesh is welded by construction), orients every triangle outwards and writes the
``STKIMSH1`` file with labels 0. It prints progress lines and the statistics line like the real
program. It is a test fixture, not a fallback: it is slow and it rounds every sharp edge to a cell.

Run as ``python reference_mesher.py --model FILE --centre X Y Z --half H --depth D --out FILE``.
It meshes at most 2^6 cells a side whatever the depth asked (``STOKES_REFERENCE_MESHER_DEPTH``
raises the cap).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from itertools import permutations
from pathlib import Path

import numpy as np

from stokes.implicit.evaluate import evaluate
from stokes.implicit.graph import BINARY, LEAVES, UNARY, Expr
from stokes.implicit.meshfile import write

_FROM_TEXT = {op: op for op in (*UNARY, *BINARY, *LEAVES)}


def parse(text: str) -> Expr:
    nodes: dict[str, Expr] = {}
    last: Expr | None = None
    for raw in text.splitlines():
        parts = raw.split()
        if not parts:
            continue
        name, op, args = parts[0], _FROM_TEXT[parts[1]], parts[2:]
        if op == "const":
            node = Expr("const", value=float(args[0]))
        else:
            node = Expr(op, tuple(nodes[a] for a in args))
        nodes[name] = node
        last = node
    if last is None:
        raise SystemExit("empty model")
    return last


def march(root: Expr, centre: np.ndarray, half: float, depth: int) -> tuple[np.ndarray, np.ndarray]:
    n = 1 << depth
    axis = np.linspace(-half, half, n + 1)
    gx, gy, gz = np.meshgrid(axis + centre[0], axis + centre[1], axis + centre[2], indexing="ij")
    points = np.stack([gx.ravel(), gy.ravel(), gz.ravel()], axis=1)
    values = evaluate(root, points).reshape(n + 1, n + 1, n + 1)
    values = np.where(values == 0.0, 1e-12, values)
    stride = np.array([(n + 1) * (n + 1), n + 1, 1])
    cells = np.stack(np.meshgrid(*(np.arange(n),) * 3, indexing="ij"), axis=-1).reshape(-1, 3)
    corner = {
        bits: cells + np.array([(bits >> 2) & 1, (bits >> 1) & 1, bits & 1]) for bits in range(8)
    }
    unit = {0: 4, 1: 2, 2: 1}
    edge_vertex: dict[tuple[int, int], int] = {}
    positions: list[np.ndarray] = []
    triangles: list[np.ndarray] = []

    def vertex_ids(a: np.ndarray, b: np.ndarray) -> np.ndarray:
        ga, gb = a @ stride, b @ stride
        lo, hi = np.minimum(ga, gb), np.maximum(ga, gb)
        keys = list(zip(lo.tolist(), hi.tolist(), strict=True))
        out = np.empty(len(keys), dtype=np.int64)
        new_rows: list[int] = []
        for i, key in enumerate(keys):
            found = edge_vertex.get(key)
            if found is None:
                found = len(edge_vertex)
                edge_vertex[key] = found
                new_rows.append(i)
            out[i] = found
        if new_rows:
            rows = np.array(new_rows)
            fa = values[tuple(a[rows].T)]
            fb = values[tuple(b[rows].T)]
            t = (fa / (fa - fb))[:, None]
            pa = points[ga[rows]]
            pb = points[gb[rows]]
            positions.append(pa + t * (pb - pa))
        return out

    for order in permutations(range(3)):
        bits = [0]
        for ax in order:
            bits.append(bits[-1] | unit[ax])
        tet = [corner[b] for b in bits]
        f = np.stack([values[tuple(c.T)] for c in tet], axis=1)
        inside = f < 0
        mask = (inside * np.array([1, 2, 4, 8])).sum(axis=1)
        for case in range(1, 15):
            rows = np.nonzero(mask == case)[0]
            if rows.size == 0:
                continue
            ins = [i for i in range(4) if case >> i & 1]
            outs = [i for i in range(4) if not case >> i & 1]
            corners = [c[rows] for c in tet]
            if len(ins) in (1, 3):
                lone, others = (ins[0], outs) if len(ins) == 1 else (outs[0], ins)
                ids = [vertex_ids(corners[lone], corners[o]) for o in others]
                tris = [np.stack(ids, axis=1)]
                ref_in = (
                    points[corners[lone] @ stride]
                    if len(ins) == 1
                    else np.mean([points[corners[i] @ stride] for i in others], axis=0)
                )
                ref_out = (
                    np.mean([points[corners[o] @ stride] for o in others], axis=0)
                    if len(ins) == 1
                    else points[corners[lone] @ stride]
                )
                refs = [(ref_in, ref_out)]
            else:
                i, j = ins
                k, m = outs
                e = [
                    vertex_ids(corners[i], corners[k]),
                    vertex_ids(corners[i], corners[m]),
                    vertex_ids(corners[j], corners[m]),
                    vertex_ids(corners[j], corners[k]),
                ]
                tris = [np.stack([e[0], e[1], e[2]], axis=1), np.stack([e[0], e[2], e[3]], axis=1)]
                ref_in = (points[corners[i] @ stride] + points[corners[j] @ stride]) / 2
                ref_out = (points[corners[k] @ stride] + points[corners[m] @ stride]) / 2
                refs = [(ref_in, ref_out), (ref_in, ref_out)]
            for tri, (rin, rout) in zip(tris, refs, strict=True):
                triangles.append(np.concatenate([tri, rin, rout], axis=1))
    pos = np.concatenate(positions) if positions else np.zeros((0, 3))
    if not triangles:
        return pos, np.zeros((0, 3), dtype=np.int64)
    raw = np.concatenate(triangles)
    tri = raw[:, :3].astype(np.int64)
    rin, rout = raw[:, 3:6], raw[:, 6:9]
    a, b, c = pos[tri[:, 0]], pos[tri[:, 1]], pos[tri[:, 2]]
    normal = np.cross(b - a, c - a)
    flip = np.einsum("ij,ij->i", normal, rout - rin) < 0
    tri[flip] = tri[flip][:, [0, 2, 1]]
    return pos, tri


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--centre", nargs=3, type=float, required=True)
    parser.add_argument("--half", type=float, required=True)
    parser.add_argument("--depth", type=int, required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--threads", type=int, default=1)
    parser.add_argument("--simplify", type=float, default=0.0)
    parser.add_argument("--progress", action="store_true")
    args = parser.parse_args(argv)
    start = time.monotonic()
    if args.depth > 15:
        print(json.dumps({"error": "depth", "message": "depth above 15"}))
        return 2
    # It is slow and its memory grows with the cube: it meshes at most 2^6 cells a side, coarser
    # than asked, which every test that uses it allows for.
    depth = min(args.depth, int(os.environ.get("STOKES_REFERENCE_MESHER_DEPTH", "6")))
    root = parse(Path(args.model).read_text(encoding="utf-8"))
    if args.progress:
        print(json.dumps({"stage": "evaluate", "done": 0, "total": 1}), flush=True)
    pos, tri = march(root, np.array(args.centre), args.half, depth)
    write(Path(args.out), pos, tri)
    if args.progress:
        print(json.dumps({"stage": "evaluate", "done": 1, "total": 1}), flush=True)
    stats = {
        "vertices": len(pos),
        "triangles": len(tri),
        "welded": 0,
        "open_edges": 0,
        "nonmanifold_edges": 0,
        "folds": 0,
        "simplified": False,
        "seconds": round(time.monotonic() - start, 3),
    }
    print(json.dumps({"stats": stats}), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
