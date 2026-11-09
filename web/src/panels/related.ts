/**
 * The Related ▾ menu (ADR-0022 §6.4, T-051): the relations the server can find, and what a click on
 * one does to the pick.
 *
 * §6.4 describes the interaction as: **hovering an entry previews the matches as the hover overlay,
 * a click adds them to the pick, and Shift-click replaces.** **The click and Shift-click halves are
 * built; the hover preview is NOT** — it needs the selection overlay to accept a provisional set,
 * which it does not yet (board row T-051, and group B of the review corrected exactly this
 * overstatement in an earlier version of this comment). The commit is an ordinary pick change, which
 * is why the rule lives here as a pure function rather than inside the menu component.
 *
 * The relations are the server's (`tag.related`), with their one-line description and the unit their
 * `tolerance` is in. A relation this build does not know is not listed: the server refuses an unknown
 * relation, so offering one would be a button that always errors.
 */

import type { TagRegionMember } from "../api/types";

/** One relation the menu offers. */
export interface RelatedRelation {
  id: string;
  label: string;
  /** What the relation means, for the entry's tooltip. */
  detail: string;
  /** What `tolerance` is in, so a caller can label its value: "relative", "radians" or "degrees". */
  toleranceUnit: string;
}

/**
 * The relations §6.4 names, in the order the menu lists them: the geometric ones first (they are what
 * an engineer reaches for), then the two that reuse an existing command.
 */
export const RELATED_RELATIONS: readonly RelatedRelation[] = [
  { id: "same_area", label: "Same area", detail: "Faces within 2% of the picked area", toleranceUnit: "relative" },
  // Checked against `RELATIONS` in `stokes/api/commands/selection.py` and the implementations in
  // `stokes/geometry/related.py`, NOT against §6.4's prose, which disagrees with both:
  //   * `coplanar`'s tolerance is a FRACTION OF THE PART'S BOUNDING-BOX DIAGONAL, not an angle —
  //     "parallel at the fixed COPLANAR_ANGLE, and the centroid within offset_tol of the diagonal"
  //     (`related.coplanar`). Its angle is fixed at 0.035 rad and is not what `tolerance` sets.
  //   * `parallel` and `coaxial` take RADIANS (both compared against a cosine).
  //   * `tangent` takes DEGREES: it calls `grow_parts`, whose angle is validated `0 < a <= 180`.
  // Labelling these wrong is not cosmetic — a user typing "2" for coplanar in degrees would be
  // asking for a threshold 2000x too large.
  { id: "coplanar", label: "Coplanar", detail: "Faces in the picked faces' plane", toleranceUnit: "diagonal fraction" },
  { id: "parallel", label: "Parallel", detail: "Faces parallel to the picked ones", toleranceUnit: "radians" },
  { id: "coaxial", label: "Coaxial", detail: "Cylindrical faces on the picked axis", toleranceUnit: "radians" },
  { id: "tangent", label: "Tangent", detail: "Faces reached across smooth edges", toleranceUnit: "degrees" },
  { id: "same_color", label: "Same colour", detail: "Faces sharing the picked faces' colour", toleranceUnit: "relative" },
];

/** The relation with this id, or undefined for one this build does not offer. */
export function relationById(id: string): RelatedRelation | undefined {
  return RELATED_RELATIONS.find((relation) => relation.id === id);
}

/**
 * What a click on a relation does to the pick: `add` on a plain click, `replace` on a Shift-click
 * (§6.4). A click with no modifier adds, because a user narrowing a selection reaches for Related
 * after picking a seed, and replacing by default would throw that seed away.
 */
export function relatedHow(shift: boolean): "add" | "replace" {
  return shift ? "replace" : "add";
}

/**
 * The members a relation's answer contributes to the pick, as the bulk-pick items `pickMany` takes.
 *
 * The server answers with the *full* member list for each part it matched (canonical ranges). Those
 * are expanded to the face ids the pick holds, because `pickMany` is indexed by face id — and the
 * expansion is bounded by the answer, which the server has already trimmed to the matches.
 */
export function relatedItems(members: readonly TagRegionMember[]): { part: string; faces: number[] }[] {
  const items: { part: string; faces: number[] }[] = [];
  for (const member of members) {
    if (member.faces === "all") continue; // a whole part is not a face relations answer
    const faces: number[] = [];
    for (const [first, last] of member.faces) {
      for (let id = first; id <= last; id++) faces.push(id);
    }
    if (faces.length > 0) items.push({ part: member.part, faces });
  }
  return items;
}

/** A short description of an answer, for the toast a click shows: "3 faces on 1 part". */
export function relatedSummary(items: readonly { faces: number[] }[]): string {
  const faces = items.reduce((total, item) => total + item.faces.length, 0);
  if (faces === 0) return "no related faces";
  return `${faces} face${faces === 1 ? "" : "s"} on ${items.length} part${items.length === 1 ? "" : "s"}`;
}
