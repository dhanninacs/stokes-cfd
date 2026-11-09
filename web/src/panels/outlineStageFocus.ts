import type { OutlineProjection } from "./outlineModel";

/** Focus visible outline sections while preserving the user's nested expansion choices. */
export function focusOutlineStage(
  projection: OutlineProjection,
  stage: string,
  expanded: Readonly<Record<string, boolean>>,
): { target: string | null; expanded: Record<string, boolean> } {
  const section = projection.sections.find((entry) => entry.stage === stage);
  const next = { ...expanded };
  // A hidden section must not collapse the sections the user can still navigate.
  if (!section) return { target: null, expanded: next };
  for (const entry of projection.sections) next[entry.id] = entry.id === section.id;
  return { target: section.id, expanded: next };
}
