/**
 * The live pick as pure functions (ADR-0021 3.1): what a click, a Shift-click or a Ctrl-click
 * leaves of the picked bodies and faces, and which member of the result is the active one
 * (`selectedPatch`). Moved out of `store.ts` so the rules can be tested without the store; the store
 * keeps the state and the side effects. This module imports nothing.
 *
 * The selection additions of ADR-0022 6 are here too, as rules: `convertPick` (a switch of mode
 * keeps the pick), `allFacesOf` and `pickAfterFaces` (the triple-click takes every face of a body),
 * `siblingsOf` and `pickAfterBodies` (the double-click takes the parts of one import group),
 * `applyMany` (the bulk pick of the paint stroke, and of the box and lasso T-032 adds), and
 * `pushHistory` with `pickInMode` (the last ten picks `selection.previous` brings back).
 */

/** What a click, Shift-click or Ctrl-click does to the current pick. */
export type PickHow = "replace" | "add" | "toggle";
/**
 * The pick mode (ADR-0021 3.1; edge and point added by ADR-0022 15.4). A pick holds one kind at a
 * time, and the mode is what the next click means and what the toolbar acts on.
 */
export type PickMode = "body" | "face" | "edge" | "point";
/** The modes that name B-rep topology rather than tessellation (they need a part's topology record). */
export const TOPOLOGY_MODES: readonly PickMode[] = ["edge", "point"];
/**
 * The live pick: part node ids of picked bodies, and face/edge/vertex ids per part node id, each
 * sorted (ADR-0021 3.1; edges and vertices by ADR-0022 15.4). A pick of one kind leaves the other
 * three maps empty, exactly as a body pick has always held no faces.
 */
export interface Picked {
  bodies: string[];
  faces: Record<string, number[]>;
  edges: Record<string, number[]>;
  vertices: Record<string, number[]>;
}
/** The empty pick, which the store shares between every empty state; never mutate it. */
export const NO_PICK: Picked = { bodies: [], faces: {}, edges: {}, vertices: {} };

/** One id set of the pick, by the mode it belongs to (the mode names its own map). */
export function idsInMode(picked: Picked, mode: PickMode): Record<string, number[]> {
  if (mode === "edge") return picked.edges;
  if (mode === "point") return picked.vertices;
  if (mode === "face") return picked.faces;
  return {};
}

/** The bodies after `how`: replace, append the id if absent, or toggle it. */
export function toggleBody(current: readonly string[], id: string, how: PickHow): string[] {
  if (how === "replace") return [id];
  const has = current.includes(id);
  if (how === "add") return has ? [...current] : [...current, id];
  return has ? current.filter((x) => x !== id) : [...current, id];
}

/** The face ids after `how`, kept sorted. */
export function toggleFace(current: readonly number[], face: number, how: PickHow): number[] {
  if (how === "replace") return [face];
  const has = current.includes(face);
  if (how === "add") return has ? [...current] : [...current, face].sort((a, b) => a - b);
  return (has ? current.filter((f) => f !== face) : [...current, face]).sort((a, b) => a - b);
}

/**
 * The bodies after a whole set is picked with `how` (the double-click in body mode, ADR-0022 6.2):
 * replace takes the set, add adds the ones missing, and toggle is the set's own checkbox — every id
 * of the set already held leaves just the others, else the whole set joins. A set toggle is all or
 * nothing because the click that escalates has already applied `how` to the one id under the
 * pointer; toggling element by element would undo it.
 */
export function toggleBodies(current: readonly string[], parts: readonly string[], how: PickHow): string[] {
  if (how === "replace") return [...parts];
  const held = new Set(current);
  if (how === "add" || !parts.every((id) => held.has(id))) for (const id of parts) held.add(id);
  else for (const id of parts) held.delete(id);
  return [...held];
}

/** The face ids after a whole set of one part is picked with `how`; the same all-or-nothing toggle. */
export function toggleFaces(current: readonly number[], faces: readonly number[], how: PickHow): number[] {
  if (how === "replace") return [...faces].sort((a, b) => a - b);
  const held = new Set(current);
  if (how === "add" || !faces.every((face) => held.has(face))) for (const face of faces) held.add(face);
  else for (const face of faces) held.delete(face);
  return [...held].sort((a, b) => a - b);
}

/**
 * The edge or vertex ids after a whole SET of one part is picked with `how`.
 *
 * The same **all-or-nothing** toggle the faces use, and for the same reason: the click that escalates
 * has already applied `how` to the one id under the pointer, so toggling the set element by element
 * would undo it. A wire is a set like any other — double-clicking an edge selects the whole loop, and
 * Ctrl-double-clicking it again removes the whole loop.
 */
export function toggleTopology(
  current: readonly number[],
  ids: readonly number[],
  how: PickHow,
): number[] {
  if (how === "replace") return [...ids].sort((a, b) => a - b);
  const held = new Set(current);
  if (how === "add" || !ids.every((id) => held.has(id))) for (const id of ids) held.add(id);
  else for (const id of ids) held.delete(id);
  return [...held].sort((a, b) => a - b);
}

/** The pick and the active member after a whole set of bodies is picked (the double-click). */
export function pickAfterBodies(picked: Picked, parts: readonly string[], how: PickHow): { picked: Picked; active: string | null } {
  const bodies = toggleBodies(picked.bodies, parts, how);
  const clicked = parts[0] ?? null;
  const active = clicked !== null && bodies.includes(clicked) ? clicked : (bodies[bodies.length - 1] ?? null);
  return { picked: { bodies, faces: {}, edges: {}, vertices: {} }, active };
}

/** The pick and the active member after a click on body `part`. */
export function pickAfterBody(picked: Picked, part: string, how: PickHow): { picked: Picked; active: string | null } {
  const bodies = toggleBody(picked.bodies, part, how);
  // The active member is the body just picked, or after a toggle-off the last one left.
  const active = bodies.includes(part) ? part : (bodies[bodies.length - 1] ?? null);
  return { picked: { bodies, faces: {}, edges: {}, vertices: {} }, active };
}

/** The pick and the active member after a click on face `face` of `part`. */
export function pickAfterFace(picked: Picked, part: string, face: number, how: PickHow): { picked: Picked; active: string | null } {
  return pickAfterFaces(picked, part, [face], how);
}

/** The pick and the active member after a whole set of faces of `part` is picked (the triple-click). */
export function pickAfterFaces(picked: Picked, part: string, faces: readonly number[], how: PickHow): { picked: Picked; active: string | null } {
  const ids = toggleFaces(picked.faces[part] ?? [], faces, how);
  // A plain click replaces the whole pick, the faces of other parts too, as it does for bodies.
  const all: Record<string, number[]> = how === "replace" ? {} : { ...picked.faces };
  if (ids.length) all[part] = ids;
  else delete all[part];
  // The active member owns the face just picked, or after a toggle-off the last part with faces left.
  const owners = Object.keys(all);
  const active = all[part] ? part : (owners[owners.length - 1] ?? null);
  return { picked: { bodies: [], faces: all, edges: {}, vertices: {} }, active };
}

/**
 * The pick after a click on edge `edge` or vertex `id` of `part` (ADR-0022 §15.4, T-058).
 *
 * The rules mirror the face ones exactly — replace clears the other parts, add and toggle work on
 * one part's set — so a click in Edge or Point mode behaves the way the same click does in Face mode
 * and the two cannot drift apart. Edges and vertices are their own pick kind (a pick holds one kind
 * at a time), so a click here clears bodies and faces.
 */
export function pickAfterTopology(
  picked: Picked,
  part: string,
  kind: "edge" | "vertex",
  id: number,
  how: PickHow,
): { picked: Picked; active: string | null } {
  if (id < 0) return { picked, active: null };
  const current = (kind === "edge" ? picked.edges : picked.vertices)[part] ?? [];
  const ids = toggleFace(current, id, how); // same integer-set rules as faces
  const all: Record<string, number[]> = how === "replace" ? {} : { ...(kind === "edge" ? picked.edges : picked.vertices) };
  if (ids.length) all[part] = ids;
  else delete all[part];
  const owners = Object.keys(all);
  const active = all[part] ? part : (owners[owners.length - 1] ?? null);
  return {
    picked: {
      bodies: [],
      faces: {},
      edges: kind === "edge" ? all : {},
      vertices: kind === "vertex" ? all : {},
    },
    active,
  };
}

/**
 * The patch the active member is drawn by: the clicked patch when the body just picked owns several
 * (the sample part), else the single patch of the active source, as a tree selection of the part
 * node would give. `patchOf` is the store's `patchForSource`, passed in so that this module needs
 * no geometry type.
 */
export function activePatch(active: string | null, part: string, clicked: string | undefined, patchOf: (source: string) => string | null): string | null {
  if (active === null) return null;
  return active === part && clicked ? clicked : patchOf(active);
}

// ---------------------------------------------------------------------------------------------- the bulk pick
/** How a bulk pick is applied: paint adds or removes as it goes, a box or lasso replaces (T-032). */
export type PickManyHow = "add" | "remove" | "replace";
/** One item of a bulk pick: a body (no `faces`), or the faces `faces` of the part `part`. */
export interface PickManyItem {
  part: string;
  faces?: number[];
}

/**
 * The pick after a whole set of items is applied with `how` (ADR-0022 6.5): what the paint stroke
 * calls as it goes, and, through `pickMany`, what T-032's box and lasso apply through. An item
 * without `faces` is a body and one with them is those faces of its part, so one call may name
 * bodies and faces; the pick holds one kind at a time, and the last item's kind is the one it
 * becomes — the gesture's last word. `replace` builds the pick from the items alone, `add` and
 * `remove` leave the rest of it as it is. One call is one pick whatever it names: the store makes
 * one update and `pickHistory` one entry for it. The active member is the last item's part while
 * it is still held, else the last one left, the rule a click follows.
 */
export function applyMany(picked: Picked, items: readonly PickManyItem[], how: PickManyHow): { picked: Picked; active: string | null } {
  const replace = how === "replace";
  const bodies = new Set<string>(replace ? [] : picked.bodies);
  const faces = new Map<string, Set<number>>();
  if (!replace) for (const [part, ids] of Object.entries(picked.faces)) faces.set(part, new Set(ids));
  let last: string | null = null;
  let byFace = false;
  for (const item of items) {
    last = item.part;
    if (item.faces === undefined) {
      if (how === "remove") bodies.delete(item.part);
      else bodies.add(item.part);
      byFace = false;
      continue;
    }
    const ids = faces.get(item.part) ?? new Set<number>();
    for (const face of item.faces) {
      if (how === "remove") ids.delete(face);
      else ids.add(face);
    }
    if (ids.size > 0) faces.set(item.part, ids);
    else faces.delete(item.part);
    byFace = true;
  }
  // A body pick holds no faces and a face pick no bodies, as every click leaves it: the last item
  // decides which one this is, and what the gesture did not name that way goes.
  if (byFace) bodies.clear();
  else faces.clear();
  const result: Picked = { bodies: [...bodies], faces: {}, edges: {}, vertices: {} };
  for (const [part, ids] of faces) result.faces[part] = [...ids].sort((a, b) => a - b);
  const owners = Object.keys(result.faces);
  const active =
    last === null ? null : result.bodies.includes(last) || owners.includes(last) ? last : byFace ? (owners[owners.length - 1] ?? null) : (result.bodies[result.bodies.length - 1] ?? null);
  return { picked: result, active };
}

// ---------------------------------------------------------------------------------------------- the mode switch
/** The face ids of a part with `count` faces: 0 up to count-1, in order. */
export function allFacesOf(count: number): number[] {
  const faces: number[] = [];
  for (let i = 0; i < count; i++) faces.push(i);
  return faces;
}

/**
 * The pick in the mode `to` (ADR-0022 6.3). Face → body: the parts that own picked faces, in the
 * order they were picked, after any bodies already held. Body → face: the faces already held, plus
 * every face (0 up to count-1) of each picked body; a body whose face count is unknown (0: the
 * transport carries no face ids for it, as for the sample part) has no face to name and is dropped.
 * `faceCountOf` is the store's lookup into the drawn `geometry`.
 */
/** The topology facts a finer-mode conversion needs, if the record is loaded. */
export interface ConversionTopology {
  /** An edge's incident vertex ids, or null when the part's record is not loaded. */
  verticesOfEdge(part: string, edge: number): number[] | null;
  /** A vertex's incident edge ids, or null when the part's record is not loaded (§15.4: point → edge). */
  edgesOfVertex(part: string, vertex: number): number[] | null;
  /** Every edge id in a part's record, or null when it is not loaded (§15.4: body → all its edges). */
  allEdges(part: string): number[] | null;
  /** The faces a picked edge or vertex touches, or null when the record cannot say. */
  facesOfEdges(part: string, edges: readonly number[]): number[] | null;
  facesOfVertices(part: string, vertices: readonly number[]): number[];
}

export function convertPick(
  picked: Picked,
  to: PickMode,
  faceCountOf: (part: string) => number,
  topology?: ConversionTopology,
): Picked {
  if (to === "body") {
    const bodies = [...picked.bodies];
    // Any mode -> owning body: every part named by any id set joins, in a stable order.
    for (const map of [picked.faces, picked.edges, picked.vertices]) {
      for (const part of Object.keys(map)) {
        if ((map[part] ?? []).length > 0 && !bodies.includes(part)) bodies.push(part);
      }
    }
    return { bodies, faces: {}, edges: {}, vertices: {} };
  }
  if (to === "face") {
    // Face keeps faces; a body expands to all its faces; an edge or a point expands to the faces it
    // touches **when the record is loaded** (A6, ADR-0022 §15.4: "edge → adjacent faces").
    // §15.4 also says to "omit topology not yet loaded with a visible pending state rather than
    // clearing the pick silently" — so a conversion with no record keeps NOTHING for that part and
    // says so, rather than dropping the pick and looking like a plain clear.
    const faces: Record<string, number[]> = {};
    for (const [part, ids] of Object.entries(picked.faces)) if (ids.length > 0) faces[part] = [...ids];
    for (const part of picked.bodies) {
      const count = faceCountOf(part);
      if (count > 0) faces[part] = allFacesOf(count);
    }
    if (topology !== undefined) {
      for (const [part, ids] of Object.entries(picked.edges)) {
        if (ids.length === 0) continue;
        const adjacent = topology.facesOfEdges(part, ids);
        if (adjacent !== null && adjacent.length > 0) faces[part] = mergeIds(faces[part] ?? [], adjacent);
      }
      for (const [part, ids] of Object.entries(picked.vertices)) {
        if (ids.length === 0) continue;
        const adjacent = topology.facesOfVertices(part, ids);
        if (adjacent.length > 0) faces[part] = mergeIds(faces[part] ?? [], adjacent);
      }
    }
    return { bodies: [], faces, edges: {}, vertices: {} };
  }
  if (to === "point") {
    // Point from a body or a face expands to those vertices; from an edge it is its endpoints
    // (§15.4: "edge → endpoints (Point)"). Without a record the pick keeps nothing for that part.
    const vertices: Record<string, number[]> = {};
    for (const [part, ids] of Object.entries(picked.vertices)) if (ids.length > 0) vertices[part] = [...ids];
    if (topology !== undefined) {
      for (const [part, ids] of Object.entries(picked.edges)) {
        if (ids.length === 0) continue;
        for (const edge of ids) {
          const endpoints = topology.verticesOfEdge(part, edge);
          if (endpoints !== null) vertices[part] = mergeIds(vertices[part] ?? [], endpoints);
        }
      }
    }
    return { bodies: [], faces: {}, edges: {}, vertices };
  }
  // to === "edge" | "point". §15.4's conversions into a topology mode:
  //   * point -> incident edges (to "edge");
  //   * body -> all its edges/vertices, and face -> its bounding edges/vertices (both, from the
  //     record: a face's bounding edges are not derivable without edge->face incidence, so a face
  //     pick contributes nothing for that part and the caller shows the pending state).
  // A part whose record is not loaded contributes nothing — spec: "omit topology not yet loaded with
  // a visible pending state rather than clearing the pick silently".
  const carried: Record<string, number[]> = {};
  // Captured before the branch below narrows `to`, so the return can still tell the two apart.
  const wantsEdges = to === "edge";
  const same = wantsEdges ? picked.edges : picked.vertices;
  for (const [part, ids] of Object.entries(same)) if (ids.length > 0) carried[part] = [...ids];

  if (to === "edge" && topology !== undefined) {
    // A point pick expands to the edges meeting it — the record lists them per vertex.
    for (const [part, ids] of Object.entries(picked.vertices)) {
      for (const vertex of ids) {
        const incident = topology.edgesOfVertex(part, vertex);
        if (incident !== null && incident.length > 0) carried[part] = mergeIds(carried[part] ?? [], incident);
      }
    }
    // A body expands to all the edges of its record.
    for (const part of picked.bodies) {
      const all = topology.allEdges(part);
      if (all !== null && all.length > 0) carried[part] = mergeIds(carried[part] ?? [], all);
    }
  }
  return {
    bodies: [],
    faces: {},
    edges: wantsEdges ? carried : {},
    vertices: wantsEdges ? {} : carried,
  };
}

/**
 * The parts beside `part` in tree order, `part` first: what the double-click in body mode picks
 * (`siblingsOf` of the ids the caller found under the part's parent group). An id the list does not
 * hold is still first — the clicked part is what the gesture names.
 */
export function siblingsOf(part: string, order: readonly string[]): string[] {
  return [part, ...order.filter((id) => id !== part)];
}

// ---------------------------------------------------------------------------------------------- the pick history
/** How many picks the store keeps for `selection.previous` (ADR-0022 6.7). */
export const PICK_HISTORY_LIMIT = 10;

/** True when two picks hold the very same bodies and faces (a repeat is not kept). */
export function samePicked(a: Picked, b: Picked): boolean {
  if (a === b) return true;
  if (a.bodies.length !== b.bodies.length || !a.bodies.every((id, i) => id === b.bodies[i])) return false;
  const keys = Object.keys(a.faces);
  if (keys.length !== Object.keys(b.faces).length) return false;
  for (const key of keys) {
    const left = a.faces[key] ?? [];
    const right = b.faces[key];
    if (right === undefined || left.length !== right.length || !left.every((face, i) => face === right[i])) return false;
  }
  return true;
}

/**
 * The history after `picked` — the pick a change is leaving — joins it: the last ten, oldest
 * first, and a repeat of the entry at the top is skipped, so going back and forth does not fill
 * the list with one pick. The caller passes the same array back unchanged when nothing moved.
 */
export function pushHistory(history: readonly Picked[], picked: Picked): Picked[] {
  const last = history[history.length - 1];
  if (last !== undefined && samePicked(last, picked)) return [...history];
  return [...history, picked].slice(-PICK_HISTORY_LIMIT);
}

/** Which mode a stored pick is from: face picks hold faces and no body (an empty pick suits either). */
/**
 * What a mode change could not convert because the record was not loaded (§15.4, A6).
 *
 * The spec is explicit: "omit topology not yet loaded with a **visible pending state** rather than
 * clearing the pick silently". `convertPick` omits such a part, which on its own is
 * indistinguishable from the user having picked nothing — so this says which parts are waiting, and
 * the strip shows it. Returns null when nothing is pending, so the caller can render nothing.
 *
 * `loaded` is the set of parts whose topology record the controller holds.
 */
export function pendingConversionText(
  picked: Picked,
  to: PickMode,
  loaded: ReadonlySet<string>,
): string | null {
  if (to !== "edge" && to !== "point") return null;
  // A part is waiting when the pick names it but its record is not in yet. Only the parts that
  // would CONTRIBUTE are counted: a part already holding the right kind needs no record.
  const same = to === "edge" ? picked.edges : picked.vertices;
  const waiting = new Set<string>();
  for (const part of [...Object.keys(picked.faces), ...picked.bodies]) {
    if ((same[part] ?? []).length > 0) continue; // already carries the target kind
    if (!loaded.has(part)) waiting.add(part);
  }
  if (waiting.size === 0) return null;
  const noun = to === "edge" ? "edges" : "vertices";
  return `Waiting for ${waiting.size} part${waiting.size === 1 ? "" : "s"} to load before showing ${noun}`;
}

export function pickModeOf(picked: Picked): PickMode {
  // The order matters (A6): a pick in edge mode holds `edges` and no bodies, and calling it "body"
  // is what made a stored edge pick get converted — and dropped — when it was restored.
  if (picked.bodies.length > 0) return "body";
  if (Object.values(picked.edges).some((ids) => ids.length > 0)) return "edge";
  if (Object.values(picked.vertices).some((ids) => ids.length > 0)) return "point";
  if (Object.values(picked.faces).some((ids) => ids.length > 0)) return "face";
  return "body";
}

/** Sorted, deduplicated union of two id lists (the pick's id sets are kept sorted). */
export function mergeIds(left: readonly number[], right: readonly number[]): number[] {
  return [...new Set([...left, ...right])].sort((a, b) => a - b);
}

/** A stored pick applied in the mode now current: converted only when it is the other mode's (ADR-0022 6.7). */
export function pickInMode(
  picked: Picked,
  mode: PickMode,
  faceCountOf: (part: string) => number,
  topology?: ConversionTopology,
): Picked {
  return pickModeOf(picked) === mode ? picked : convertPick(picked, mode, faceCountOf, topology);
}