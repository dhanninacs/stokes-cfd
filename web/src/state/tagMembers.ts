/**
 * What a pick becomes as a tag's `members` (ADR-0021 §3.5, ADR-0022 §15.4).
 *
 * ## Why this is its own module
 *
 * These rules lived in `store.ts`, which reaches `window` and so cannot be imported by the node test
 * runner. They had **no test at all**, which is exactly how the defect below survived: `pickAsMembers`
 * returned `null` for every edge pick, and nothing failed.
 *
 * ## The rule that was wrong
 *
 * The old code refused edges, citing ADR-0022 §15.4's "a boundary tag with edges but no faces is
 * incomplete, not an STL patch". That sentence is about an **STL** boundary. The same section's next
 * sentence says how a cap loop is stored: *"store explicit cap loops as edge members of a boundary or
 * plain tag; the cap operation consumes the loop."* The server side has been complete all along —
 * `OpContext.edges` reads a member's `edges` key as ids or ranges, `cap._tagged_loops` ties those
 * edges to a closed opening by identity, and `cap` declares `tag_params=("loop",)`. Only the UI was
 * missing, so a loop could not be tagged and `cap loop=` was unreachable from the app.
 *
 * ## The rule that stands
 *
 * A **vertex** pick still makes nothing. §15.4 is explicit that a region tag holds whole bodies, never
 * vertices, and no consumer reads vertex members. Point mode exists for the Seed for fluid pick, which
 * takes a point, not a tag.
 *
 * Pure: no imports, nothing but the pick and the names it produces.
 */

/** The source id of the sample part (`SAMPLE_SOURCE` in `viewport/scene.ts`). */
export const SAMPLE_SOURCE = "@sample";

/** The three pick modes, as the store names them. */
export type PickMode = "body" | "face" | "edge" | "point";

/** A part's ids as a compact list: consecutive runs as `[first, last]`, single ids alone. */
export type IdRanges = Array<number | [number, number]>;

/** A tag member: faces or edges, on one part. */
export type PickMember =
  | { part: string; faces: IdRanges | "all" }
  | { part: string; edges: IdRanges };

/** What a tag made from the pick holds: its `members` parameter and the kind it takes. */
export interface PickMembers {
  members: PickMember[];
  kind: "boundary" | "region";
}

/** The parts the pick holds, by mode. */
export interface PickedIds {
  bodies: readonly string[];
  faces: Record<string, readonly number[]>;
  edges: Record<string, readonly number[]>;
  vertices: Record<string, readonly number[]>;
}

/** Sorted ids as the members' compact form: runs as `[first, last]`, single ids alone. */
export function idRanges(ids: readonly number[]): IdRanges {
  const out: IdRanges = [];
  for (let i = 0; i < ids.length; ) {
    let j = i;
    while (j + 1 < ids.length && ids[j + 1] === ids[j] + 1) j++;
    out.push(j > i ? [ids[i], ids[j]] : ids[i]);
    i = j + 1;
  }
  return out;
}

/**
 * The pick as the `members` of the tag commands: faces as ranges (a `boundary`), an edge loop as edge
 * ranges (a `boundary`), or whole bodies (a `region`).
 *
 * Returns `null` when the pick holds nothing that can be tagged, which is the toolbar's signal to
 * explain rather than open the dialog.
 */
export function pickAsMembers(picked: PickedIds, mode: PickMode): PickMembers | null {
  if (mode === "face") {
    const members = Object.entries(picked.faces)
      .filter(([part, ids]) => part !== SAMPLE_SOURCE && ids.length > 0)
      .map(([part, ids]) => ({ part, faces: idRanges(ids) }));
    return members.length > 0 ? { members, kind: "boundary" } : null;
  }
  if (mode === "point") {
    // §15.4: a region tag holds whole bodies, never vertices, and nothing consumes vertex members.
    return null;
  }
  if (mode === "edge") {
    // The cap loop's storage (§15.4). A `boundary`, because `cap` consumes it as a loop of edges.
    const members = Object.entries(picked.edges)
      .filter(([part, ids]) => part !== SAMPLE_SOURCE && ids.length > 0)
      .map(([part, ids]) => ({ part, edges: idRanges(ids) }));
    return members.length > 0 ? { members, kind: "boundary" } : null;
  }
  const members = picked.bodies
    .filter((part) => part !== SAMPLE_SOURCE)
    .map((part) => ({ part, faces: "all" as const }));
  return members.length > 0 ? { members, kind: "region" } : null;
}
