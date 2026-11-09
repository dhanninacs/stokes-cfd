/**
 * The wire walk: which edges belong to the same loop as a picked edge (ADR-0022 §15.4).
 *
 * ## What this is for
 *
 * §15.4: **double-click an edge to select its connected wire/loop**, double-click a point to select its
 * incident edges, triple-click to escalate to every edge and vertex of that body. The loop is what
 * `cap loop=` consumes, so this is the click that makes a cap loop reachable without naming a tag first.
 *
 * ## No codec change: the graph is already in the record
 *
 * A topology record has no `wires` list, and it does not need one. Each **vertex** carries its incident
 * edge ids (`TopoVertex.edges`, the A9 incidence), so the edge-to-vertices relation is the inverse of a
 * list already on the wire. That was the piece I missed when I first went looking for a wire: I checked
 * `TopoEdge` for one, found none, and did not think to invert the vertex list.
 *
 * ## The walk, and what it refuses to guess
 *
 * Start at the picked edge. Step through vertices that have **exactly two** incident edges, because that
 * is a chain continuing; **stop** at a vertex with more than two (a branch) or one (an open end). If the
 * walk returns to its start it is a **closed wire** and every edge on it is selected. Otherwise the
 * **chain** is selected as far as it got, and the branch vertices are **reported** — "3 edges, stopped at
 * 2 branches" — rather than a path being chosen through them. §15.4 is explicit that a branch is reported
 * and not guessed, and a guess here would silently cap the wrong opening.
 *
 * ## Cost
 *
 * Linear in the wire length, with a vertex-to-edges map built once. The heat sink has 220 circular edges
 * and the review named it as the performance case, so nothing here scans all edges per step: each step
 * looks only at the vertices of the edge it is standing on.
 *
 * Pure: no three.js, no store, no browser. The record's own types are structural, so a test can build one.
 */

/** One vertex as the record carries it: its id and the edges that meet there. */
export interface WalkVertex {
  id: number;
  /** The incident edge ids. The A9 incidence, and the inverse this module walks. */
  edges: readonly number[];
}

/** One edge as the walk needs it: its id and the vertices it joins. */
export interface WalkEdge {
  id: number;
  /** The vertices this edge runs between, in the polyline's own order. */
  points: number;
}

/** A wire, or the chain a walk got to before it had to stop. */
export interface Wire {
  /** Every edge id on the walk, in the order walked, starting from the picked edge. */
  edges: number[];
  /** True when the walk returned to the edge it started from. */
  closed: boolean;
  /**
   * The vertices the walk refused to pass through: each has more than two incident edges.
   *
   * Reported, never traversed. A closed walk cannot have any (returning to the start means every
   * vertex on it had exactly two), so this is non-empty only for an open chain.
   */
  branches: number[];
  /** The vertex the walk stopped at, or null when it closed. */
  endedAt: number | null;
}

/** The incidence the walk needs, built once from a record's vertices. */
export interface Incidence {
  /** Vertex id → the edge ids meeting there. */
  at: Map<number, number[]>;
  /** Edge id → the vertex ids at its ends. Not on the wire; derived from `at`, so the two agree. */
  ends: Map<number, number[]>;
}

/**
 * Build the incidence from a record's vertices.
 *
 * The record gives vertex → edges directly. Edge → vertices is the **inverse**, built here rather than
 * sent, because sending both would be sending the same information twice and the two could disagree.
 */
export function incidenceOf(vertices: readonly WalkVertex[]): Incidence {
  const at = new Map<number, number[]>();
  const ends = new Map<number, number[]>();
  for (const vertex of vertices) {
    const held = at.get(vertex.id);
    if (held === undefined) at.set(vertex.id, [...vertex.edges]);
    else held.push(...vertex.edges);
    for (const edge of vertex.edges) {
      const list = ends.get(edge);
      if (list === undefined) ends.set(edge, [vertex.id]);
      else list.push(vertex.id);
    }
  }
  return { at, ends };
}

/**
 * The wire or chain through one edge.
 *
 * `from` and `to` are the vertex the walk starts at and the one it heads towards: a caller that knows
 * which endpoint the user is nearer can start in that direction, so the selected chain is the one the
 * user pointed at rather than an arbitrary half of a longer run. Omit `to` to walk towards whichever end
 * `from` is not.
 */
export function wireThrough(edgeId: number, incidence: Incidence, from?: number, to?: number): Wire {
  const walk: Wire = { edges: [edgeId], closed: false, branches: [], endedAt: null };
  const ends = incidence.ends.get(edgeId) ?? [];
  if (ends.length < 2) {
    // A closed curve stored as one edge, or a degenerate one. Either way there is nothing to walk and
    // one edge is the whole answer — a circle in some CAD files is exactly this case.
    return walk;
  }
  const start = from !== undefined && ends.includes(from) ? from : ends[0];
  let here = to !== undefined && ends.includes(to) ? to : nextEnd(ends, start);
  let current = edgeId;
  const seen = new Set<number>([edgeId]);

  // **The START is a vertex too, and it can be a branch.** The loop below stops at the far end of each
  // edge it steps onto, so it never examines the vertex the walk set off from — and on a bar between two
  // branches BOTH ends are branches, so the walk reported one and silently dropped the other. Found by
  // the T-junction test: `expected [2] to equal [1, 2]`.
  const atStart = incidence.at.get(start) ?? [];
  if (atStart.length > 2 && !walk.branches.includes(start)) walk.branches.push(start);
  else if (atStart.length === 1) {
    // The picked edge's own near end is free, so the walk cannot continue past it: the chain ends here
    // and the far direction is the only one. Reported rather than walked.
    walk.endedAt = start;
  }

  // Bounded by the number of edges in the incident map, so a malformed record cannot spin forever — a
  // wire that revisited an edge would otherwise loop until something else stopped it.
  const limit = incidence.ends.size + 1;
  for (let step = 0; step < limit; step++) {
    const neighbours = incidence.at.get(here) ?? [];
    if (neighbours.length !== 2) {
      // One edge is an open end, more than two is a branch. Either way the walk stops HERE and says so:
      // §15.4 reports a branch rather than choosing a path through it.
      // Pushed once: the start vertex can also be reached again as a walk end, and a branch named
      // twice reads as two branches.
      if (neighbours.length > 2 && !walk.branches.includes(here)) walk.branches.push(here);
      walk.endedAt = here;
      return walk;
    }
    const nextEdge = neighbours[0] === current ? neighbours[1] : neighbours[0];
    if (nextEdge === edgeId) {
      walk.closed = true;
      return walk;
    }
    if (seen.has(nextEdge)) {
      // Back on an edge already walked without closing: the only way here is a record whose incidence
      // disagrees with itself. Stop and report rather than loop.
      walk.endedAt = here;
      walk.branches.push(here);
      return walk;
    }
    seen.add(nextEdge);
    walk.edges.push(nextEdge);
    const otherEnds = incidence.ends.get(nextEdge) ?? [];
    const forward = nextEnd(otherEnds, here);
    if (forward === here) {
      // An edge whose two ends are the same vertex: a self-loop. Nothing to step onto.
      walk.endedAt = here;
      return walk;
    }
    current = nextEdge;
    here = forward;
  }
  walk.endedAt = here;
  return walk;
}

/** The end of a list that is not `at`, or `at` itself when the two are equal (a self-loop). */
function nextEnd(ends: readonly number[], at: number): number {
  if (ends.length === 0) return at;
  return ends[0] === at ? (ends[1] ?? at) : ends[0];
}

/**
 * Every edge and vertex of one part: what a triple-click escalates to (§15.4).
 *
 * The escalation is the whole body's topology, not a walk, so it needs no incidence — and it is the one
 * selection that can be large, which is why the caller names the part and the record bounds it.
 */
export function wholePart(
  edges: readonly { id: number }[],
  vertices: readonly { id: number }[],
): { edges: number[]; vertices: number[] } {
  return { edges: edges.map((e) => e.id), vertices: vertices.map((v) => v.id) };
}

/**
 * The edges incident on one vertex: what a double-click on a POINT selects (§15.4).
 *
 * One lookup, because the record holds it directly. Kept as a function so the click path reads the same
 * as the edge path rather than reaching into the map itself.
 */
export function edgesAtVertex(vertexId: number, incidence: Incidence): number[] {
  return [...(incidence.at.get(vertexId) ?? [])].sort((a, b) => a - b);
}
