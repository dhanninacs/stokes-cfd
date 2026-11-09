/**
 * Boundary coverage as the set-up flow shows it: which existing boundary a pick would overlap
 * (`boundary.overlap`), and which faces of a part no boundary holds (`boundary.uncovered`).
 *
 * The server computes both (ADR-0013: the front end does no range arithmetic over the model); these
 * pure functions only word the answers and turn ranges into the face ids `pickMany` takes, so the
 * wording is tested without a renderer.
 *
 * Overlap is a warning, not a refusal: decisions log G10 allows a face in two boundary tags while
 * preparing and the case compiler refuses it, so the notice says what will happen and lets the user
 * assign anyway.
 */

import type { BoundaryEntry, BoundaryOverlapAnswer, UncoveredAnswer, UncoveredPart } from "../api/types";

/** "a", "a and b", "a, b and c". */
function listed(names: readonly string[]): string {
  if (names.length <= 1) return names[0] ?? "";
  return `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
}

/** The names an overlap is reported under: the boundaries using each tag, else the tag itself. */
export function overlapNames(answer: BoundaryOverlapAnswer | null): string[] {
  const names: string[] = [];
  for (const overlap of answer?.overlaps ?? []) {
    const own = overlap.boundaries.length > 0 ? overlap.boundaries.map((b) => b.name) : [overlap.tag.name];
    for (const name of own) if (!names.includes(name)) names.push(name);
  }
  return names;
}

/**
 * The notice shown before an assign whose faces some boundary already holds, or null when none does.
 * Names the existing boundary so the user knows what would be overlapped (G10: allowed, then refused
 * by the case compiler until one of the two lets go of the faces).
 */
export function overlapNotice(answer: BoundaryOverlapAnswer | null): string | null {
  const names = overlapNames(answer);
  if (names.length === 0) return null;
  const faces = answer?.faces;
  const count = typeof faces === "number" ? `${faces.toLocaleString()} picked face${faces === 1 ? " is" : "s are"}` : "Picked faces are";
  const them = faces === 1 ? "it" : "them";
  return `${count} already in ${listed(names)}. Assigning puts ${them} in two boundaries, which the case refuses until one is removed.`;
}

/**
 * The same warning in a few words, for the places a sentence does not fit (the context toolbar beside
 * the pick, the head of the Assign menu): "1 face already in wall". The sentence goes in the tooltip.
 */
export function overlapShort(answer: BoundaryOverlapAnswer | null): string | null {
  const names = overlapNames(answer);
  if (names.length === 0) return null;
  const faces = answer?.faces;
  const count = typeof faces === "number" ? `${faces.toLocaleString()} face${faces === 1 ? "" : "s"}` : "Faces";
  return `${count} already in ${listed(names)}`;
}

/** The warning after an assign the server reports as overlapping (`setup.overlap` on the boundary). */
export function overlapAfterAssign(boundary: Pick<BoundaryEntry, "name" | "warnings"> | undefined | null): string | null {
  if (!boundary || !Array.isArray(boundary.warnings) || !boundary.warnings.includes("setup.overlap")) return null;
  return `${boundary.name} shares faces with another boundary; the case refuses it until one is removed.`;
}

/** Inclusive ranges as the face ids `pickMany` takes. */
export function rangesToIds(ranges: readonly (readonly [number, number])[]): number[] {
  const ids: number[] = [];
  for (const [first, last] of ranges) for (let id = first; id <= last; id++) ids.push(id);
  return ids;
}

/** The parts that have faces no boundary holds, in the server's order. */
export function uncoveredParts(answer: UncoveredAnswer | null): UncoveredPart[] {
  return (answer?.parts ?? []).filter((p) => (p.faces ?? 0) > 0);
}

/** One part's line: "4 of 6 faces". */
export function uncoveredCount(part: UncoveredPart): string {
  const faces = part.faces ?? 0;
  return part.total === null ? `${faces.toLocaleString()} face${faces === 1 ? "" : "s"}` : `${faces.toLocaleString()} of ${part.total.toLocaleString()} faces`;
}
