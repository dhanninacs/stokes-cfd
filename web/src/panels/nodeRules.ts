/** Pure helpers over the node tree that the tree and properties panels share. */

import type { NodeSummary, NodeTypeSchema, ParamValue } from "../api/types";
import { isQuantity } from "../api/types";
import { ancestorChain, indexFor } from "../state/treePatch";
import type { PatchMeta } from "../viewport/geometry";
import { formatNumber, prettyDisplay, prettyUnit } from "../util/units";
import { meshTreeValue } from "./meshPanelModel";

export const PARAMETERS_PATH = "/physics/parameters";

/** Skeleton groups below the top level that the model looks up by path. */
const STRUCTURAL_PATHS: ReadonlySet<string> = new Set([PARAMETERS_PATH, "/physics/materials"]);

/**
 * The fixed groups every project is created with; the server refuses to rename or delete them.
 *
 * **A VIRTUAL row is never structural** (Codex's 042012, the virtual-menu item). Measured: every outline
 * section and page row showed a menu whose only content was *"Fixed group of the project"* - because an
 * outline section's path is its virtual id, which has no `/` in it, so the depth test said "top level".
 * **A page called "Run" is not a group of the project at all**, and saying so is worse than saying nothing.
 */
export function isStructural(node: NodeSummary): boolean {
  if (node.structural !== undefined) return node.structural;
  /*
    **The PATH decides, not the id.** Measured: keying this on `virtual:` broke `/physics/parameters`,
    because an outline row for a real folder carries a virtual ID and the REAL path. A row that shows a
    folder of the project IS that folder, whatever its row is called.
  */
  if (!node.path.startsWith("/")) return false;
  return node.path.split("/").length <= 2 || STRUCTURAL_PATHS.has(node.path);
}

/** True when the node type lists `group` (or the wildcard) among its accepted children. */
export function acceptsGroup(node: NodeSummary, types: Record<string, NodeTypeSchema>): boolean {
  const accepted = types[node.type]?.["x-children"] ?? [];
  return accepted.includes("*") || accepted.includes("group");
}

/** "Add parameter" applies to the parameters group and to the parameters inside it. */
export function canAddParameter(node: NodeSummary): boolean {
  return node.path === PARAMETERS_PATH || (node.type === "parameter" && node.path.startsWith(`${PARAMETERS_PATH}/`));
}

/** `group`, `group_2`, `group_3` … the first name not used by a sibling. */
export function uniqueName(base: string, siblings: readonly NodeSummary[]): string {
  const taken = new Set(siblings.map((s) => s.name));
  if (!taken.has(base)) return base;
  for (let n = 2; ; n++) {
    const candidate = `${base}_${n}`;
    if (!taken.has(candidate)) return candidate;
  }
}

export function rowDomId(id: string): string {
  return `tree-row-${id}`;
}

/**
 * The id of the span holding a row's VALUE text, for `aria-describedby` (Codex's 042012).
 *
 * **`title` alone is hover-only**: it does not reach a screen reader that never points at the row, and
 * the value is truncated on screen. This span is what the treeitem's `aria-describedby` names, so the
 * full text reaches assistive technology with the row itself - no hover, no new tooltip system.
 */
export function rowValueDomId(id: string): string {
  return `tree-row-value-${id}`;
}

/**
 * Height of a tree row in CSS px (DESIGN.md section 2; `.tree-row` in styles.css). The panel lays
 * its rows out by this number, so that only the ones in view have to exist: change both together.
 */
export const ROW_HEIGHT = 28;

/** The chain of ancestors from the first level down to the node's parent, or null when the node is absent. */
export function ancestorsOf(root: NodeSummary | null, id: string): NodeSummary[] | null {
  if (!root) return null;
  const index = indexFor(root);
  if (index !== null) return ancestorChain(index, id);
  const walk = (node: NodeSummary, trail: NodeSummary[]): NodeSummary[] | null => {
    for (const child of node.children ?? []) {
      if (child.id === id) return trail;
      const found = walk(child, [...trail, child]);
      if (found) return found;
    }
    return null;
  };
  return walk(root, []);
}

export function subtreeIds(node: NodeSummary): string[] {
  const ids: string[] = [node.id];
  for (const child of node.children ?? []) ids.push(...subtreeIds(child));
  return ids;
}

/** Which row to select once `rows[index]` (and its subtree) is gone: next sibling, else previous sibling, else parent. */
export function neighbourAfterRemoval(rows: readonly RowModel[], index: number): string | null {
  const row = rows[index];
  if (!row) return null;
  for (let i = index + 1; i < rows.length && rows[i].depth >= row.depth; i++) {
    if (rows[i].parentId === row.parentId) return rows[i].node.id;
  }
  for (let i = index - 1; i >= 0 && rows[i].depth >= row.depth; i--) {
    if (rows[i].parentId === row.parentId) return rows[i].node.id;
  }
  return row.parentId;
}

/** The muted text on the right of a tree row: display-unit value, then `· expression`; groups show a child count. */
export function treeValue(node: NodeSummary): string {
  // A row the stage model decorated (a placeholder, a virtual row, a boundary with its kind and value) says its own line (T-219 B).
  const subtitle = node.meta?.subtitle;
  if (typeof subtitle === "string" && subtitle !== "") return subtitle;
  // The mesh settings and the mesh result say the mesher and the cells or the run's state (T-211b).
  const mesh = meshTreeValue(node);
  if (mesh !== "") return mesh;
  const displays = node.displays ?? {};
  const key = "value" in displays ? "value" : Object.keys(displays)[0];
  if (key !== undefined) {
    const text = prettyDisplay(displays[key]);
    const raw = node.params[key];
    const expr = isQuantity(raw) ? raw.expr : undefined;
    return expr ? `${text} · ${expr}` : text;
  }
  const raw = node.params.value;
  if (isQuantity(raw)) {
    const text = `${formatNumber(raw.value)} ${prettyUnit(raw.unit)}`.trim();
    return raw.expr ? `${text} · ${raw.expr}` : text;
  }
  return node.children_count ? String(node.children_count) : "";
}

export interface RowModel {
  node: NodeSummary;
  depth: number;
  parentId: string | null;
  hasChildren: boolean;
  open: boolean;
}

// ------------------------------------------------------------------------------------ row style controls

/**
 * What a row's eye and swatch show (ADR-0021 decision 4.3). Parts and tags carry a colour and an
 * opacity; a group that holds parts carries the eye alone, because it has neither of its own.
 */
export interface RowControls {
  /** `#rrggbb` for the swatch, or null when nothing names a colour (the swatch shows the faint placeholder). */
  color: string | null;
  /** False while the node is hidden; for a group, while every part beneath it is hidden. */
  visible: boolean;
  /** The `opacity` parameter in 0..1, 1 when the node has none. */
  opacity: number;
  /** False for a group of parts: the eye only, without the swatch or the popover. */
  styled: boolean;
}

const HEX = /^#[0-9a-f]{6}$/i;

function hexParam(params: Record<string, ParamValue>, name: string): string | null {
  const value = params[name];
  return typeof value === "string" && HEX.test(value) ? value.toLowerCase() : null;
}

function opacityParam(params: Record<string, ParamValue>): number {
  const value = params.opacity;
  return typeof value === "number" && Number.isFinite(value) ? Math.min(1, Math.max(0, value)) : 1;
}

/** The `part` nodes of a subtree, in tree order. */
function partsUnder(node: NodeSummary): NodeSummary[] {
  const out: NodeSummary[] = [];
  const visit = (current: NodeSummary): void => {
    if (current.type === "part") out.push(current);
    for (const child of current.children ?? []) visit(child);
  };
  visit(node);
  return out;
}

/** The colours of one patch list, part id to `#rrggbb`: one pass instead of a scan per row. */
const colourIndex = new WeakMap<readonly PatchMeta[], Map<string, string>>();

function byte(channel: number): string {
  return Math.min(255, Math.max(0, Math.round(channel * 255))).toString(16).padStart(2, "0");
}

/** The colour the viewport draws a part of its file with, as `#rrggbb`, or null when no patch says one. */
function fileColour(geometry: readonly PatchMeta[], id: string): string | null {
  let index = colourIndex.get(geometry);
  if (index === undefined) {
    index = new Map<string, string>();
    for (const group of geometry) {
      if (group.part !== null && !index.has(group.part)) index.set(group.part, `#${byte(group.color[0])}${byte(group.color[1])}${byte(group.color[2])}`);
    }
    // The list is immutable, so its answers are too; the entry retires with the list itself.
    colourIndex.set(geometry, index);
  }
  return index.get(id) ?? null;
}

function deriveControls(
  node: NodeSummary,
  geometry: readonly PatchMeta[],
  tagOf: ((id: string) => NodeSummary | null) | undefined,
): RowControls | null {
  if (node.type === "part") {
    // A part without a `color` parameter shows the colour of the file it came from (ADR-0021 4.3).
    return {
      color: hexParam(node.params, "color") ?? fileColour(geometry, node.id),
      visible: node.params.visible !== false,
      opacity: opacityParam(node.params),
      styled: true,
    };
  }
  if (node.type === "tag") {
    return { color: hexParam(node.params, "color"), visible: node.params.visible !== false, opacity: opacityParam(node.params), styled: true };
  }
  // A region or a boundary proxies the tag it references (ADR-0022 §9.1): its swatch and eye are the
  // tag's, so the two views of one tag cannot disagree. The tag node is looked up by id in the same
  // tree, and a reference to a node that is gone gives the swatch a placeholder rather than a crash
  // (that state is what D2.10's `missing` reports as `failed`).
  if (node.type === "region" || node.type === "boundary") {
    const tagId = node.params.tag;
    const tag = typeof tagId === "string" && tagOf !== undefined ? tagOf(tagId) : null;
    if (tag == null) return { color: null, visible: true, opacity: 1, styled: false };
    return {
      color: hexParam(tag.params, "color"),
      visible: tag.params.visible !== false,
      opacity: opacityParam(tag.params),
      styled: true,
    };
  }
  const parts = partsUnder(node);
  if (parts.length === 0) return null; // a group without parts is a plain row
  return { color: null, visible: parts.some((part) => part.params.visible !== false), opacity: 1, styled: false };
}

/**
 * The controls of one row, or null when the row has none. The answer is kept per node object
 * (the tree shares the nodes a patch did not touch, see state/treePatch.ts) and retires with the
 * patch list, so a tree of thousands of parts derives them once and not per render.
 */
const controlCache = new WeakMap<
  NodeSummary,
  { geometry: readonly PatchMeta[]; tagOf: ((id: string) => NodeSummary | null) | undefined; controls: RowControls | null }
>();

/**
 * `tagOf` resolves a `tag` node id to its node, which a `region` or `boundary` needs for its proxy
 * swatch (§9.1). It is optional so the callers that only draw parts and tags are unchanged; when it
 * is absent a region or boundary simply shows no swatch rather than one it cannot vouch for.
 */
export function rowControls(
  node: NodeSummary,
  geometry: readonly PatchMeta[],
  tagOf?: (id: string) => NodeSummary | null,
): RowControls | null {
  const hit = controlCache.get(node);
  if (hit !== undefined && hit.geometry === geometry && hit.tagOf === tagOf) return hit.controls;
  const controls = deriveControls(node, geometry, tagOf);
  controlCache.set(node, { geometry, tagOf, controls });
  return controls;
}

/**
 * The rows of one node's subtree, as they were last built for it. A node that did not change is
 * the same object in the next version of the tree (the patches share what they did not touch, see
 * state/treePatch.ts), so an entry stays good until the node, its place, the open nodes or the
 * filter change; what a patch changed is rebuilt along the chain to the root and nothing else.
 * `expanded` is a new object whenever a node opens or closes, which retires every entry at once:
 * opening is the user's act, one at a time, and costs one pass over the rows that are open.
 */
interface Built {
  expanded: Record<string, boolean>;
  filter: string;
  depth: number;
  parentId: string | null;
  rows: readonly RowModel[];
}
const built = new WeakMap<NodeSummary, Built>();

function rowsOf(node: NodeSummary, depth: number, parentId: string | null, expanded: Record<string, boolean>, filter: string): readonly RowModel[] {
  const hit = built.get(node);
  if (hit !== undefined && hit.expanded === expanded && hit.filter === filter && hit.depth === depth && hit.parentId === parentId) return hit.rows;
  const kids = node.children ?? [];
  const named = !filter || node.name.toLowerCase().includes(filter);
  let rows: readonly RowModel[];
  if (kids.length === 0) {
    rows = named ? [{ node, depth, parentId, hasChildren: false, open: false }] : [];
  } else {
    const open = filter ? true : expanded[node.id] === true;
    const below: RowModel[] = [];
    if (open) {
      for (const kid of kids) {
        const rowsOfKid = rowsOf(kid, depth + 1, node.id, expanded, filter);
        for (let i = 0; i < rowsOfKid.length; i++) below.push(rowsOfKid[i]);
      }
    }
    // With a filter a node stays for its own name or for a descendant's, and everything on the way is open.
    rows = named || below.length > 0 ? [{ node, depth, parentId, hasChildren: true, open }, ...below] : [];
  }
  built.set(node, { expanded, filter, depth, parentId, rows });
  return rows;
}

/**
 * The rows the tree shows, in order. With a filter, a node is kept when its name matches or any
 * descendant's does, and everything on the way is open. Rows are memoised per node (see `Built`),
 * so building them again after a patch is the chain above the patched nodes plus one pass over
 * the rows, and the rows of everything else are the same objects as before.
 */
export function buildRows(root: NodeSummary | null, expanded: Record<string, boolean>, filter: string): RowModel[] {
  const out: RowModel[] = [];
  for (const top of root?.children ?? []) {
    const rows = rowsOf(top, 0, null, expanded, filter);
    for (let i = 0; i < rows.length; i++) out.push(rows[i]);
  }
  return out;
}

/**
 * For each row, its place among its siblings, for `aria-posinset` and `aria-setsize`. With the
 * rows windowed, a screen reader cannot count the siblings that are not in the DOM, so every row
 * says where it stands. Siblings are the rows with the same parent, which a row of another level
 * between two of them (the children of the first) does not split.
 */
export function siblingInfo(rows: readonly RowModel[]): { position: Int32Array; size: Int32Array } {
  const count = rows.length;
  const position = new Int32Array(count);
  const size = new Int32Array(count);
  const run = new Int32Array(count);
  const runSize: number[] = [];
  const parentAt: (string | null | undefined)[] = []; // the parent of the run that is open at each depth
  const runAt: number[] = [];
  for (let i = 0; i < count; i++) {
    const { depth, parentId } = rows[i];
    if (parentAt[depth] === undefined || parentAt[depth] !== parentId) {
      parentAt[depth] = parentId;
      runAt[depth] = runSize.length;
      runSize.push(0);
    }
    const id = runAt[depth];
    run[i] = id;
    position[i] = ++runSize[id];
  }
  for (let i = 0; i < count; i++) size[i] = runSize[run[i]];
  return { position, size };
}

/**
 * The spans of rows to draw, as `[from, to)`: the window `[start, end)` and any row that has to
 * stay in the DOM wherever it is (the selected row, which `aria-activedescendant` points at; the
 * one being renamed, which holds the focus and its text; the one whose menu is open, which the menu
 * hangs from), sorted and merged. Between two spans the panel leaves a gap of the rows' height.
 */
export function windowSegments(total: number, start: number, end: number, pins: readonly number[]): [number, number][] {
  const spans: [number, number][] = [[Math.max(0, start), Math.min(total, end)]];
  for (const pin of pins) if (pin >= 0 && pin < total && (pin < start || pin >= end)) spans.push([pin, pin + 1]);
  spans.sort((a, b) => a[0] - b[0]);
  const merged: [number, number][] = [];
  for (const span of spans) {
    const last = merged[merged.length - 1];
    if (last !== undefined && span[0] <= last[1]) last[1] = Math.max(last[1], span[1]);
    else merged.push([span[0], span[1]]);
  }
  return merged;
}

/** The index of the row of node `id`, trying `hint` first: the row of the selection rarely moves between renders. */
export function indexOfRow(rows: readonly RowModel[], id: string | null, hint: number): number {
  if (id === null) return -1;
  if (rows[hint]?.node.id === id) return hint;
  return rows.findIndex((row) => row.node.id === id);
}

/**
 * Whether a row has any menu entry worth opening (claude-win2's 0200 item 2).
 *
 * **The tree guide's finding 2**: right-click and the "…" button on a row with nothing to offer
 * looked up the raw tree, failed, and closed itself - so the Flow model, the "not set yet"
 * placeholders, run rows and result rows showed a menu button that did nothing.
 *
 * **What makes a row offer nothing**, which is the whole rule:
 *
 * * a **virtual** row that cannot be opened either - a "not set yet" placeholder with no panel and no
 *   create verb. A virtual row that DOES open something (a run, a field, a stage folder) has an
 *   action and keeps its menu;
 * * a **count** row, which is a label (`3 items`), not a thing.
 *
 * Everything else has at least one real entry: a node can be renamed unless it is fixed, deleted
 * unless it is fixed, and a fixed one still offers Open and its own panel. So "no actions" is a small,
 * nameable set rather than a general judgement - which is what keeps this from becoming a second
 * opinion about the menu's contents.
 */
/** **Not `RowKind`**: `stageTreeModel` already exports one for the placeholder rows it builds, and a
 * second type with the same name would shadow it at the import site. */
export type ActionKind = "node" | "virtual" | "count";

export function rowHasActions(kind: ActionKind, opens: boolean): boolean {
  if (kind === "node") return true;
  if (kind === "count") return false;
  // A virtual row is worth a menu exactly when it opens something; the guide's complaint was rows
  // that are neither a node nor an entry point.
  return opens;
}
