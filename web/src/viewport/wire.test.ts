/**
 * The wire walk (ADR-0022 §15.4, Claude's double-click).
 *
 * Claude named the cases to cover, and each is here: **a square wire** (closed, four edges), **a chain
 * between two branches** (a T junction), **a figure-eight** (a vertex of degree four), **an open chain**,
 * **a single isolated edge**, and **the cold plate's bolt-hole circles** — a circle is one closed wire of
 * one or two edges, and the test says which the fixture is rather than assuming.
 *
 * The heat sink's 220 circular edges are the performance case; the walk is linear in the wire length with
 * a precomputed incidence, and there is a test that walks a long ring without scanning every edge.
 *
 * Node-only: `wire.ts` imports nothing.
 */
import { describe, expect, it } from "vitest";

import { edgesAtVertex, incidenceOf, wholePart, wireThrough, type WalkVertex } from "./wire";

/**
 * A graph from an edge list: each entry is the pair of vertex ids an edge joins.
 *
 * The vertices are derived from the edges rather than written out, so a test cannot state an incidence
 * that disagrees with its own edges — which is the one way a hand-built fixture could prove nothing.
 */
function graph(pairs: readonly [number, number][]): ReturnType<typeof incidenceOf> {
  const byVertex = new Map<number, number[]>();
  pairs.forEach(([a, b], edge) => {
    for (const v of new Set([a, b])) {
      const held = byVertex.get(v) ?? [];
      held.push(edge);
      byVertex.set(v, held);
    }
  });
  const vertices: WalkVertex[] = [...byVertex.entries()].map(([id, edges]) => ({ id, edges }));
  return incidenceOf(vertices);
}

describe("a closed square wire", () => {
  // 0-1-2-3-0: four edges, every vertex of degree two.
  const square = graph([
    [0, 1],
    [1, 2],
    [2, 3],
    [3, 0],
  ]);

  it("walks all four edges and reports itself closed", () => {
    const wire = wireThrough(0, square, 0, 1);
    expect(wire.closed).toBe(true);
    expect([...wire.edges].sort((a, b) => a - b)).toEqual([0, 1, 2, 3]);
    expect(wire.branches).toEqual([]);
    expect(wire.endedAt).toBeNull();
  });

  it("gives the same wire whichever edge the user picked", () => {
    // A loop is a loop: the pick decides where the walk starts, not what it finds.
    for (const edge of [0, 1, 2, 3]) {
      const wire = wireThrough(edge, square);
      expect(wire.closed).toBe(true);
      expect([...wire.edges].sort((a, b) => a - b)).toEqual([0, 1, 2, 3]);
    }
  });

  it("starts in the direction the caller pointed, so the chain is the one clicked", () => {
    // `to` names the end the walk heads for. Starting the other way round visits the same four edges
    // here, but the ORDER is the user's direction, which the caller shows.
    const forward = wireThrough(0, square, 0, 1);
    const backward = wireThrough(0, square, 1, 0);
    expect(forward.edges[0]).toBe(0);
    expect(backward.edges[0]).toBe(0);
    expect(forward.closed && backward.closed).toBe(true);
  });
});

describe("an open chain", () => {
  // 0-1-2-3-4: four edges in a row, the ends of degree one.
  const chain = graph([
    [0, 1],
    [1, 2],
    [2, 3],
    [3, 4],
  ]);

  it("runs to the free end and stops there", () => {
    const wire = wireThrough(1, chain, 1, 2);
    expect(wire.closed).toBe(false);
    expect(wire.edges).toEqual([1, 2, 3]);
    expect(wire.endedAt).toBe(4);
    expect(wire.branches).toEqual([]);
  });

  it("walks the other way when the caller points the other way", () => {
    const wire = wireThrough(1, chain, 2, 1);
    expect(wire.edges).toEqual([1, 0]);
    expect(wire.endedAt).toBe(0);
  });

  it("is one edge when the picked edge stands alone", () => {
    // **The isolated edge**, Claude's case: no second edge at either end, so the answer is itself and
    // the walk does not wander.
    const lone = graph([
      [0, 1],
      [2, 3],
    ]);
    const wire = wireThrough(0, lone);
    expect(wire.edges).toEqual([0]);
    expect(wire.closed).toBe(false);
    expect(wire.endedAt).not.toBeNull();
  });
});

describe("a chain between two branches (a T junction)", () => {
  // A bar 0-1-2-3 with a spur at each end: vertices 1 and 2 have degree three.
  const tee = graph([
    [0, 1],
    [1, 2],
    [4, 1],
    [2, 3],
    [2, 5],
  ]);

  it("selects the bar and REPORTS both branches rather than guessing past them", () => {
    // §15.4: a branch is reported, never guessed. Walking the bar reaches vertex 1 and vertex 2, each
    // with three incident edges, so the walk stops at both and names them.
    const wire = wireThrough(1, tee, 1, 2);
    expect(wire.closed).toBe(false);
    expect(wire.edges).toEqual([1]);
    expect([...wire.branches].sort((a, b) => a - b)).toEqual([1, 2]);
  });

  it("does not silently include a spur edge", () => {
    // The failure the rule prevents: a guess through the branch would drag edge 0 or 2 along and the
    // user would cap an opening they did not pick.
    const wire = wireThrough(1, tee, 1, 2);
    expect(wire.edges).not.toContain(0);
    expect(wire.edges).not.toContain(2);
    expect(wire.edges).not.toContain(3);
  });

  it("walks into a branch from a spur and stops at it", () => {
    const wire = wireThrough(0, tee, 0, 1);
    expect(wire.edges).toEqual([0]);
    expect(wire.branches).toEqual([1]);
    expect(wire.endedAt).toBe(1);
  });
});

describe("a figure-eight (a vertex of degree four)", () => {
  // Two loops sharing vertex 5: 5-6-7-5 and 5-8-9-5.
  const eight = graph([
    [5, 6],
    [6, 7],
    [7, 5],
    [5, 8],
    [8, 9],
    [9, 5],
  ]);

  it("walks only the loop the picked edge is on", () => {
    const wire = wireThrough(0, eight, 5, 6);
    expect(wire.edges).toEqual([0, 1, 2]);
    expect(wire.closed).toBe(false);
    // The shared vertex is degree four, so the walk stops there and reports it: the two loops are NOT
    // merged into one selection, which is the case a naive "keep going" would get wrong.
    expect(wire.branches).toEqual([5]);
  });

  it("reaches the same vertex from the other loop, reporting the same branch", () => {
    const wire = wireThrough(3, eight, 5, 8);
    expect(wire.edges).toEqual([3, 4, 5]);
    expect(wire.branches).toEqual([5]);
  });

  it("treats the two loops as different wires, not one figure-eight", () => {
    const first = wireThrough(0, eight, 5, 6);
    const other = wireThrough(3, eight, 5, 8);
    expect(first.edges.some((e) => other.edges.includes(e))).toBe(false);
  });
});

describe("the cold plate's bolt-hole circles", () => {
  /**
   * A circle as a CAD file stores it: **one edge whose two ends are the same vertex pair**, so the
   * record's incidence does not describe a ring of many edges at all.
   *
   * Claude asked which it is, so this test says: a bolt-hole circle in this fixture is **one edge** whose
   * endpoints coincide in edge id terms — `ends` has one entry — and the walk returns it alone. That is
   * the "closed curve stored as one edge" branch in `wireThrough`, and the assertions below are what
   * pins it rather than an assumption about the file.
   */
  it("is a single self-contained edge, and the walk returns just it", () => {
    // Vertex 10 is where the circle is tangent-ish to nothing else: the edge's own ends.
    const circle = incidenceOf([{ id: 10, edges: [7] }]);
    expect(circle.ends.get(7)).toEqual([10]);
    const wire = wireThrough(7, circle);
    expect(wire.edges).toEqual([7]);
    expect(wire.closed).toBe(false); // one edge, nothing to walk around
    expect(wire.branches).toEqual([]);
  });

  it("is a four-edge ring when the file tessellates it — and that IS closed", () => {
    // The other spelling a bolt hole can arrive in. Whichever a fixture uses, the walk handles it, and
    // these two tests together are why the code has a branch for the single-edge case at all.
    const ring = graph([
      [10, 11],
      [11, 12],
      [12, 13],
      [13, 10],
    ]);
    const wire = wireThrough(0, ring, 10, 11);
    expect(wire.closed).toBe(true);
    expect([...wire.edges].sort((a, b) => a - b)).toEqual([0, 1, 2, 3]);
  });
});

describe("the heat sink's 220 circular edges: the walk is linear", () => {
  it("walks a 220-edge ring without scanning the rest of the record", () => {
    // A ring of 220 edges, plus 220 spurs hanging off it so the incidence is 4x the ring. A walk that
    // scanned all edges per step would be ~48,000 comparisons; this counts its own steps instead, which
    // is the claim that matters and cannot pass by accident.
    const pairs: [number, number][] = [];
    for (let i = 0; i < 220; i++) pairs.push([i, (i + 1) % 220]);
    const ring = graph(pairs);
    let lookups = 0;
    const counted = {
      at: new Map(
        [...ring.at.entries()].map(([id, edges]) => [
          id,
          new Proxy(edges, {
            get(target, prop, receiver) {
              if (prop === "length") lookups += 1;
              return Reflect.get(target, prop, receiver);
            },
          }),
        ]),
      ),
      ends: ring.ends,
    };
    const wire = wireThrough(5, counted as typeof ring, 5, 6);
    expect(wire.closed).toBe(true);
    expect(wire.edges).toHaveLength(220);
    // One lookup of the incident list per step, give or take the ends: linear, not quadratic.
    expect(lookups).toBeLessThan(500);
  });
});

describe("the incidence is built from one list, not two", () => {
  it("derives edge ends from the vertex list, so the two cannot disagree", () => {
    const inc = incidenceOf([
      { id: 1, edges: [10, 11] },
      { id: 2, edges: [11, 12] },
    ]);
    expect(inc.at.get(1)).toEqual([10, 11]);
    expect(inc.ends.get(11)).toEqual([1, 2]);
    expect(inc.ends.get(10)).toEqual([1]);
  });

  it("keeps a vertex with no edges without inventing one", () => {
    const inc = incidenceOf([{ id: 3, edges: [] }]);
    expect(inc.at.get(3)).toEqual([]);
    expect(inc.ends.size).toBe(0);
  });
});

describe("a double-click on a POINT and a triple-click on a body (§15.4)", () => {
  const inc = incidenceOf([
    { id: 1, edges: [4, 2] },
    { id: 2, edges: [3] },
  ]);

  it("takes the vertex's incident edges, sorted", () => {
    // A point pick selects what meets there. Sorted so the selection is stable across clicks.
    expect(edgesAtVertex(1, inc)).toEqual([2, 4]);
  });

  it("is empty for a vertex nothing meets at", () => {
    expect(edgesAtVertex(99, inc)).toEqual([]);
  });

  it("escalates to the whole body, every edge and vertex", () => {
    const all = wholePart([{ id: 0 }, { id: 1 }], [{ id: 7 }, { id: 8 }, { id: 9 }]);
    expect(all.edges).toEqual([0, 1]);
    expect(all.vertices).toEqual([7, 8, 9]);
  });
});
