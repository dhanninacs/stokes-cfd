/**
 * "Set up flow" for a freshly imported body (T-219 C): the offer to make a fluid region of a part, give it a
 * material and turn every face that has no boundary yet into a wall (`setup.defaults`), so the person's own steps
 * are only the ones that are theirs: pick the inlet face and assign Inlet, pick the outlet face and assign Outlet.
 *
 * What `setup.defaults` does with a part and no picked faces: the tags the part already has, named `inlet`,
 * `outlet`, `far_field`, `symmetry`, `ground` or `walls`, become their conditions; a region is made for the part
 * when none holds it (or the region a boundary assign made, which holds no body yet), with the project's only
 * fluid material or a new water (internal) or air (external); and every face no tag holds becomes a no-slip
 * `walls` wall.
 *
 * **The order matters, and the card enforces it.** The walls cover the faces no tag holds at the moment of the
 * call. Run first, they cover all six faces of a box, and assigning an inlet to one of them afterwards leaves it in
 * two tags (`setup.overlap`). Run after the inlet and the outlet are assigned, they cover the other four and
 * `setup.check` says "Set-up complete". So an internal flow waits for both openings; an external flow (an
 * enclosure names its inlet, outlet and far field when it is made) does not.
 *
 * Pure and tested; `ConditionsPanel.tsx` draws the card and calls the command.
 */
import type { ConditionKind, NodeSummary, SetupCheck } from "../api/types";

export type FlowTemplate = "internal" | "external";

export interface FlowPart {
  id: string;
  name: string;
}

/** The `part` nodes of the tree, in tree order. */
export function partsOf(root: NodeSummary | null): FlowPart[] {
  const out: FlowPart[] = [];
  const visit = (node: NodeSummary): void => {
    if (node.type === "part") out.push({ id: node.id, name: node.name });
    for (const child of node.children ?? []) visit(child);
  };
  if (root !== null) visit(root);
  return out;
}

/** The names of the tags under `/geometry/tags`. */
function tagNames(root: NodeSummary | null): string[] {
  const geometry = root?.children?.find((c) => c.path === "/geometry");
  const tags = geometry?.children?.find((c) => c.path === "/geometry/tags");
  return (tags?.children ?? []).map((t) => t.name);
}

/** A part whose tags include an enclosure's `far_field` is an external flow; everything else starts as internal. */
export function defaultTemplate(root: NodeSummary | null): FlowTemplate {
  return tagNames(root).includes("far_field") ? "external" : "internal";
}

/** How many boundaries of each opening family the tree holds: an inlet and an outlet are what an internal flow needs first. */
export interface Openings {
  inlet: number;
  outlet: number;
}

export function openingsOf(root: NodeSummary | null, kinds: readonly ConditionKind[]): Openings {
  const out: Openings = { inlet: 0, outlet: 0 };
  const visit = (node: NodeSummary): void => {
    if (node.type === "boundary") {
      const kind = kinds.find((k) => k.id === node.params.kind);
      if (kind?.family === "inlet") out.inlet += 1;
      else if (kind?.family === "outlet") out.outlet += 1;
    }
    for (const child of node.children ?? []) visit(child);
  };
  if (root !== null) visit(root);
  return out;
}

/** The findings that say a region has no body or no material yet: what the offer is for. */
const REGION_CODES: readonly string[] = ["setup.no_region", "setup.no_material", "setup.empty_tag"];

export interface FlowOffer {
  parts: FlowPart[];
  template: FlowTemplate;
  openings: Openings;
}

/**
 * The offer, or null when it has no place: there is no part, or the set-up already has its region and its
 * material (the Conditions list is then the way on). A region that exists only because a boundary was assigned
 * to it (no body, no material yet) still gets the offer: that is the moment it is for. `setup` is null before
 * the first `setup.check` answer, which counts as no region.
 */
export function flowOffer(root: NodeSummary | null, setup: SetupCheck | null, kinds: readonly ConditionKind[] = []): FlowOffer | null {
  const parts = partsOf(root);
  if (parts.length === 0) return null;
  if (setup !== null && setup.counts.regions > 0 && !setup.findings.some((f) => REGION_CODES.includes(f.code))) return null;
  return { parts, template: defaultTemplate(root), openings: openingsOf(root, kinds) };
}

/** Whether the button can be pressed for this kind of flow, and why not. */
export function canCreate(offer: FlowOffer, template: FlowTemplate): { enabled: boolean; reason: string } {
  if (template === "internal" && (offer.openings.inlet === 0 || offer.openings.outlet === 0)) {
    const missing = offer.openings.inlet === 0 && offer.openings.outlet === 0 ? "the inlet and the outlet" : offer.openings.inlet === 0 ? "the inlet" : "the outlet";
    return { enabled: false, reason: `Assign ${missing} first: pick the face, then Assign. Every face you have not assigned becomes a wall.` };
  }
  return { enabled: true, reason: "" };
}

/** The parameters of `setup.defaults` for one part: nothing picked, so the part's own tags and the walls are what it uses. */
export function defaultsParams(part: string, template: FlowTemplate): { target: string; template: FlowTemplate } {
  return { target: part, template };
}

/**
 * What `setup.defaults` answered, in a sentence for the toast: "Fluid region with water, 4 faces made walls".
 * `boundaries` lists only the boundaries the call made (the walls), so the faces are what is worth saying.
 */
export function describeDefaults(answer: { boundaries?: unknown; material?: unknown; region?: unknown; walls_faces?: unknown }): string {
  const count = Array.isArray(answer.boundaries) ? answer.boundaries.length : 0;
  const faces = typeof answer.walls_faces === "number" ? answer.walls_faces : null;
  const material = answer.material !== null && typeof answer.material === "object" && typeof (answer.material as { name?: unknown }).name === "string" ? ` with ${(answer.material as { name: string }).name}` : typeof answer.material === "string" && answer.material !== "" ? ` with ${answer.material}` : "";
  if (faces !== null) return `Fluid region${material}, ${faces} face${faces === 1 ? "" : "s"} made walls`;
  return `Fluid region${material}, ${count} boundar${count === 1 ? "y" : "ies"}`;
}

/**
 * The command a finding's **Fix** button runs, or null when the finding has none.
 *
 * The server attaches one of two kinds of `fix`. `{command, params}` is a command a press can send as it is
 * (`region.set`, `tag.rematch`). `{action}` (`condition.edit`, `condition.add`, `material.assign`) names a
 * step the user takes on a page: `condition.edit` is the row's own click, which selects the node, and the
 * others have no command behind them, so they get **no button** rather than one that says "not wired yet".
 */
export function findingFix(finding: { fix?: { [key: string]: unknown } }): { command: string; params: Record<string, unknown> } | null {
  const fix = finding.fix;
  if (fix === undefined || typeof fix.command !== "string" || fix.command === "") return null;
  const params = fix.params !== null && typeof fix.params === "object" && !Array.isArray(fix.params) ? (fix.params as Record<string, unknown>) : {};
  return { command: fix.command, params };
}

/** The first clause of a sentence, for the few words on the screen: "Assign the inlet first: pick the face." gives "Assign the inlet first". */
export function firstClause(sentence: string): string {
  const cut = sentence.search(/[:.]/);
  return (cut < 0 ? sentence : sentence.slice(0, cut)).trim();
}
