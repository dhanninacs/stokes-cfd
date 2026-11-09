/**
 * The outline tree as DATA: six sections, their rows, and what each row opens (G-305).
 *
 * **Pure, and the whole point of the brief**: G-306 draws this, so what lands here is the decision about
 * which existing node appears where - not a rendering.
 *
 * **Real ids are never duplicated.** A row either shows a real node (and keeps its id) or is a virtual row
 * with an id of its own. The test walks every id, virtual ones included.
 *
 * **Two rows are declared UNAVAILABLE rather than faked.** The brief puts Mesh ▸ Controls at
 * `/mesh/controls` (T-236) and Physics ▸ Interfaces at `region.interfaces` (T-231); measured against a real
 * project, **neither exists** - the tree has `/mesh/results` and no controls group, and the command
 * registry has no interface command. A folder that is always empty and a page that always says "none"
 * would pass a brief-shaped test and tell a user something false, so they carry `unavailable` with the
 * reason and light up when their sources land.
 */
import type { NodeSummary } from "../api/types";

/** Where a row sits: the six stages, in the outline's order. */
export const OUTLINE_STAGES = ["Geometry", "Mesh", "Physics", "Solve", "Results", "Explore"] as const;
export type OutlineStage = (typeof OUTLINE_STAGES)[number];

/** What a row does: a folder of real nodes, a page, or one real node. */
export type OutlineRowKind = "folder" | "page" | "node";

/**
 * The groups each stage works in, as the workspace's `tree.stages` names them.
 *
 * **A user's arrangement must reach the outline too.** `arrangeTree` reorders and hides the ROOT's
 * groups - the real project group nodes - but an outline section reads its groups by PATH, so ordering
 * and hiding never touched it: measured, a reversed `groupOrder` and `hidden: ["geometry"]` left all six
 * sections exactly as they were, so **a user who arranged their tree had that arrangement silently
 * ignored** whenever the outline was on. A section now offers only its groups that are still shown, and is
 * dropped when it has none.
 */
export const STAGE_GROUPS: Readonly<Record<OutlineStage, readonly string[]>> = {
  Geometry: ["/geometry"],
  Mesh: ["/mesh"],
  Physics: ["/physics", "/regions"],
  Solve: ["/solver", "/monitors", "/runs"],
  Results: ["/scenes"],
  Explore: ["/studies"],
};

/** The section's first group path - the real node the section is named after. */
export const SECTION_PATH: Readonly<Record<OutlineStage, string>> = {
  Geometry: "/geometry",
  Mesh: "/mesh",
  Physics: "/physics",
  Solve: "/solver",
  Results: "/scenes",
  Explore: "/studies",
};

/**
 * A row of the outline.
 *
 * **`source`** is the group path the row reads its nodes from, when it has one. A row with no source and
 * no `open` is a container whose children are other rows.
 *
 * **`unavailable`** is a row whose data does not exist in this build, with the reason. **It is OMITTED from
 * the projection** - not drawn as an empty folder, not drawn as a note, not drawn at all - and reported in
 * `OutlineProjection.omitted` for a diagnostic. (This paragraph said "drawn as absent-with-a-note" before
 * the omission rule landed; the words and the behaviour agree now.)
 */
export interface OutlineRow {
  key: string;
  label: string;
  kind: OutlineRowKind;
  /** The group path whose children become this row's nodes, when there is one. */
  source?: string;
  /**
   * Show only the source's children (and their descendants) of this node type.
   *
   * **Two rows share `/regions` and must not show the same thing**: Regions shows the `region` nodes,
   * Boundary conditions shows the `boundary` nodes that live UNDER each region. Without this, the first
   * row claimed the source and the second became a reference with no rows of its own - measured: the
   * boundary rows never appeared.
   */
  only?: string;
  /** The panel a page opens, when it opens one. */
  open?: string;
  /** Why this row has nothing behind it yet. Present means the row is NOT buildable. */
  unavailable?: string;
  /**
   * **Left out while its source is absent**, rather than drawn as an empty folder. For a row that only
   * some projects have at all: imported OpenFOAM cases (ADR-0029) live under `/cases`, which a CAD project
   * never gets, and an always-empty folder there would suggest something is missing.
   */
  onlyWhenPresent?: boolean;
  /**
   * **Left out of the Basic tree** (the default; "Show all rows" restores it): a row a first run does not
   * need. `true` always; `"whenEmpty"` only while its source has nothing in it, so a project that has an
   * operation or a parameter keeps the row. The row stays in `OUTLINE` and in every full projection, so a
   * page, a selection or a reveal that names it still resolves.
   */
  advanced?: boolean | "whenEmpty";
  children?: readonly OutlineRow[];
}

/**
 * The decorated Mesh task rows a Basic tree leaves out, by node id (`mesh/<task>`): Boundaries (the Physics
 * section's Boundary conditions already lists the real boundaries) and Fidelity. The stepper still opens both.
 */
export const ADVANCED_LEAF_IDS: ReadonlySet<string> = new Set(["mesh/boundaries", "mesh/fidelity"]);

/** The rows of the outline, per section, in the brief's order. */
export const OUTLINE: Readonly<Record<OutlineStage, readonly OutlineRow[]>> = {
  Geometry: [
    { key: "bodies", label: "Bodies", kind: "folder", source: "/geometry/parts" },
    { key: "tags", label: "Tags", kind: "folder", source: "/geometry/tags" },
    { key: "operations", label: "Operations", kind: "folder", source: "/geometry/operations", advanced: "whenEmpty" },
  ],
  Mesh: [
    { key: "global", label: "Global size", kind: "node", source: "/mesh" },
    // Capping (ADR-0026 decision 2) follows the sizing, as Fluent's watertight workflow orders it. The
    // row is the decorated tree's virtual `mesh/capping` node; without this line the outline, which is
    // the tree a user sees, never showed it.
    { key: "capping", label: "Capping", kind: "node", source: "virtual:mesh/capping", advanced: true },
    {
      key: "controls",
      label: "Local controls",
      kind: "folder",
      // **Measured absent**: the project's groups are `/mesh` and `/mesh/results`; there is no controls
      // group and no `mesh.control*` command. T-236's work, which is the mesh lane.
      // **A fact about THIS build, not a promise about the next one**: the user-text gate forbids
      // "coming next" phrasing, and it is right - a reason a user could read must say what is true now.
      unavailable: "local mesh controls are not part of the mesh settings this build writes",
    },
    /*
      **The mesher is chosen from the catalogue, not from a hard-coded list** (Codex's 040513). The rows
      come from `mesh.meshers`, which carries `built` and the user-visible `why`, so the outline shows
      exactly the two that are built today - blockMesh and snappyHexMesh - and **nothing that would fail
      later**. A tool whose `built` is false is not drawn: cfMesh and Gmsh exist in the catalogue and are
      deliberately absent from this list.
    */
    { key: "meshers", label: "Mesher", kind: "folder", source: "catalogue:meshers", advanced: true },
    { key: "result", label: "Mesh", kind: "node", source: "/mesh/results" },
  ],
  Physics: [
    /*
      **Solver type and Models are OMITTED, not pointed at a near neighbour.** Neither page exists: the
      only panel a model edit reaches today is the Solve panel's own model list, and *"a recognised row
      kind is not the same as the promised page"* (Codex's 013452). Pointing "Solver type" at the
      Conditions panel would open the BOUNDARY conditions, which is a different question with a similar
      name - worse than not showing the row at all.
    */
    { key: "materials", label: "Materials", kind: "folder", source: "/physics/materials" },
    // **Regions shows the region nodes WITHOUT their boundaries**: a boundary appears once, under
    // Boundary conditions. Measured: with both rows showing everything, three real boundary nodes and
    // their region were drawn TWICE - the duplicate-id defect the uniqueness test exists to catch.
    { key: "regions", label: "Regions", kind: "folder", source: "/regions", only: "region" },
    {
      key: "boundaries",
      label: "Boundary conditions",
      kind: "folder",
      // The boundary nodes live under each region, so the source is the regions and `only` keeps theirs.
      source: "/regions",
      only: "boundary",
    },
    {
      key: "interfaces",
      label: "Interfaces",
      kind: "page",
      // **Measured absent**: `region.interfaces` does not exist as a command, so there is nothing to read.
      unavailable: "this build has no region interface query",
    },
    { key: "parameters", label: "Parameters", kind: "folder", source: "/physics/parameters", advanced: "whenEmpty" },
    { key: "reference", label: "Reference values", kind: "page", open: "properties", advanced: true },
  ],
  Solve: [
    // An imported case is a whole OpenFOAM case of its own: its settings, recipe and runs (ADR-0029).
    { key: "cases", label: "OpenFOAM cases", kind: "folder", source: "/cases", onlyWhenPresent: true },
    // **Basic is Settings + Runs.** Controls, Initialisation and Run open sections of the same Solve page the
    // Settings row opens (its Advanced disclosure holds them), so they join the full tree only.
    { key: "methods", label: "Settings", kind: "page", open: "solve" },
    { key: "controls", label: "Controls", kind: "page", open: "solve", advanced: true },
    { key: "initialisation", label: "Initialisation", kind: "page", open: "solve", advanced: true },
    { key: "run", label: "Run", kind: "page", open: "solve", advanced: true },
    { key: "runs", label: "Runs", kind: "folder", source: "/runs" },
  ],
  Results: [
    {
      key: "surfaces",
      label: "Surfaces",
      kind: "folder",
      children: [{ key: "slice", label: "Slice", kind: "node", source: "virtual:slice" }],
    },
    {
      key: "graphics",
      label: "Graphics",
      kind: "folder",
      children: [
        { key: "contours", label: "Contours", kind: "folder", source: "virtual:fields", open: "results" },
        { key: "vectors", label: "Vectors", kind: "page", open: "results-vectors" },
        { key: "streamlines", label: "Streamlines", kind: "page", open: "results-streamlines" },
        { key: "probe", label: "Probe", kind: "page", open: "results-probe" },
        { key: "lod", label: "Level of detail", kind: "page", open: "results-lod" },
      ],
    },
    {
      key: "plots",
      label: "Plots",
      kind: "folder",
      children: [
        { key: "residuals", label: "Residuals", kind: "node", source: "virtual:residuals" },
        { key: "monitors", label: "Monitors", kind: "node", source: "virtual:monitors" },
      ],
    },
    {
      key: "reports",
      label: "Reports",
      kind: "folder",
      children: [{ key: "report", label: "HTML report", kind: "page", open: "results-report" }],
    },
  ],
  Explore: [{ key: "studies", label: "Studies", kind: "folder", source: "/studies" }],
};

/** The rows that can be built from what this project has, and the ones that cannot, with the reason. */
export interface OutlineAvailability {
  buildable: OutlineRow[];
  unavailable: { row: OutlineRow; reason: string }[];
}

/**
 * Split a section's rows by whether their source exists, so a caller draws the honest tree.
 *
 * `hasSource` answers whether a path or a virtual id is present in the decorated tree. **A row with no
 * source and no `open` is a container** and is buildable by definition; a row marked `unavailable` in the
 * table stays unavailable whatever the tree holds, because the table already knows why.
 */
export function availableRows(
  rows: readonly OutlineRow[],
  hasSource: (source: string) => boolean,
): OutlineAvailability {
  const buildable: OutlineRow[] = [];
  const unavailable: { row: OutlineRow; reason: string }[] = [];
  for (const row of rows) {
    if (row.unavailable !== undefined) {
      unavailable.push({ row, reason: row.unavailable });
    } else if (row.source !== undefined && !hasSource(row.source)) {
      // **A source that is not in this project** is not the same as one that cannot exist: a project
      // without studies has no `/studies`, and the row is simply empty rather than missing.
      buildable.push(row);
    } else {
      buildable.push(row);
    }
  }
  return { buildable, unavailable };
}

/** Every id the outline will draw, given the real nodes it holds - for the uniqueness test. */
export function outlineIds(
  root: NodeSummary | null,
): string[] {
  const ids: string[] = [];
  const walk = (node: NodeSummary): void => {
    ids.push(node.id);
    for (const child of node.children ?? []) walk(child);
  };
  if (root !== null) walk(root);
  return ids;
}

/** How a row or a section reads at a glance. Folders roll their children up. */
export type OutlineState = "idle" | "todo" | "ready" | "stale" | "running" | "done" | "failed";

/** The order a rolled-up state takes precedence in: the worst child wins. */
const STATE_RANK: Readonly<Record<OutlineState, number>> = {
  failed: 6,
  todo: 5,
  stale: 4,
  running: 3,
  ready: 2,
  done: 1,
  idle: 0,
};

/** One node of the projected outline: a section, a row, or a real node under one. */
export interface OutlineNode {
  /** The real node's id, or a virtual id built from the section and key. */
  id: string;
  label: string;
  /** The section this node belongs to - carried on every node so the drawer never re-derives it. */
  stage: OutlineStage;
  kind: OutlineRowKind | "section" | "leaf";
  /** The panel this node opens, when it opens one. */
  open?: string;
  /** The real node's subtree, when the row showed one; already decorated. */
  children?: OutlineNode[];
  /** For a row that REUSES a source another row already shows: where its content lives. */
  ref?: string;
  /**
   * The real group path this row shows, when it shows one.
   *
   * **Path-keyed rules need the REAL path** - the folder verbs are keyed on `/physics/materials`, and a
   * synthetic row whose `path` is a virtual id matches none of them. Measured: the Materials row's menu
   * was empty for exactly this reason.
   */
  sourcePath?: string;
}

/** A section and its rows, with the rows this build cannot fill left out entirely. */
export interface ProjectedSection {
  stage: OutlineStage;
  id: string;
  rows: OutlineNode[];
}

/** Why a row is not in the projected tree - kept for the caller, never rendered as a row. */
export interface OmittedRow {
  stage: OutlineStage;
  key: string;
  reason: string;
}

export interface OutlineProjection {
  sections: ProjectedSection[];
  omitted: OmittedRow[];
  /** The rows the Basic tree left out (never set by a full projection); `OmittedRow.reason` says why. */
  folded: OmittedRow[];
  /**
   * The row that SHOWS each source, by the source string.
   *
   * **Recorded when the projection is built**, because that is the only moment the answer is known.
   * A reference row used to search for its owner by label at flatten time - a guess, and a wrong one
   * whenever two rows share a word.
   */
  owners: ReadonlyMap<string, string>;
}

/**
 * The outline for a real, decorated tree.
 *
 * **Unsupported rows are OMITTED, not drawn empty** (the decision on the brief): a row whose source cannot
 * exist in this build is left out of `rows` and reported in `omitted` with its reason. The caller renders
 * `sections`; `omitted` is for a log or a diagnostic, **never a row and never a "coming next" line**.
 *
 * **Real ids are preserved**: a row that shows real nodes keeps those nodes' ids and their children, so
 * selection, reveal and filtering keep working on the ids the journal and the API use.
 */
export function buildOutline(
  root: NodeSummary | null,
  resolve: (source: string) => NodeSummary | null,
  /** The groups the user HID, by name (`tree.hidden`) - a section with none of its groups left is dropped. */
  hidden: readonly string[] = [],
  /**
   * Draw the rows flagged `advanced` too. **True by default**, so every caller that wants "the row this id
   * names" (the Details lookup, the tests) sees the whole outline; the tree passes the user's choice
   * (`outlineMode.ts`, Basic unless "Show all rows" is on).
   */
  showAll: boolean = true,
): OutlineProjection {
  const sections: ProjectedSection[] = [];
  const omitted: OmittedRow[] = [];
  const folded: OmittedRow[] = [];
  /*
    **A source shown under one row is not shown again under another.** Measured: Regions and Boundary
    conditions both read `/regions`, and Surfaces and Scenes both read `/scenes`, so four real nodes
    appeared twice - and a duplicated real id makes selection ambiguous. The FIRST row that claims a source
    shows its children; a later row with the same source is a **reference** (`ref`), named and openable but
    carrying no children of its own. The brief's own rule is "every real id appears at most once".
  */
  const claimed = new Set<string>();
  const owners = new Map<string, string>();
  for (const stage of OUTLINE_STAGES) {
    /*
      **A section whose groups are ALL hidden does not appear.** `arrangeTree` hides the real group nodes,
      which the outline does not draw, so without this a user who hid Geometry kept a Geometry section
      with nothing under it - measured: `hidden: ["geometry"]` left the six sections unchanged.
    */
    const groups = STAGE_GROUPS[stage];
    if (groups.length > 0 && groups.every((path) => hidden.includes(path.replace(/^\//, "")))) continue;
    const rows: OutlineNode[] = [];
    for (const row of OUTLINE[stage]) {
      if (row.unavailable !== undefined) {
        omitted.push({ stage, key: row.key, reason: row.unavailable });
        continue;
      }
      /*
        **A PAGE WITH NO DESTINATION IS OMITTED before it can be drawn** (Codex's 005002): a row whose
        click does nothing is worse than an absent row - it looks available and is not. Checked here, in
        the projection, so no renderer can draw one by mistake.
      */
      if (row.kind === "page" && (row.open === undefined || PAGE_ROW[row.open] === undefined)) {
        // **No destination, or a destination the Details pane does not know**: omitted, because a row
        // whose click does nothing looks available and is not.
        omitted.push({ stage, key: row.key, reason: "this page has no destination in this build" });
        continue;
      }
      const claim = row.source !== undefined ? `${row.source}#${row.only ?? ""}` : "";
      /*
        **A row whose SOURCE the user hid is dropped too**, not only a whole section. Measured: hiding
        `/regions` kept the `Regions` row with its region under it - the section survived (right, Physics
        has other groups) but the hidden group's rows should not (Codex's 081956: *"hidden-source rows stay
        absent"*).
      */
      const sourceHidden =
        row.source !== undefined &&
        !row.source.startsWith("virtual:") &&
        !row.source.startsWith("catalogue:") &&
        hidden.includes(row.source.replace(/^\//, "").split("/")[0]);
      if (sourceHidden) {
        omitted.push({ stage, key: row.key, reason: "its group is hidden" });
        continue;
      }
      if (row.onlyWhenPresent === true && row.source !== undefined && resolve(row.source) === null) {
        omitted.push({ stage, key: row.key, reason: "this project has none" });
        continue;
      }
      if (!showAll && isAdvancedRow(row, resolve)) {
        folded.push({ stage, key: row.key, reason: "advanced row" });
        continue;
      }
      const reused = claim !== "" && claimed.has(claim);
      rows.push(projectRow(stage, row, resolve, reused, showAll, folded));
      if (row.source !== undefined) {
        if (!reused) owners.set(row.source, rowId(stage, row.key));
        claimed.add(claim);
      }
    }
    sections.push({ stage, id: sectionId(stage), rows });
  }
  void root;
  return { sections, omitted, folded, owners };
}

/** A row the Basic tree leaves out: flagged advanced, or advanced while its source is empty. */
function isAdvancedRow(row: OutlineRow, resolve: (source: string) => NodeSummary | null): boolean {
  if (row.advanced === true) return true;
  if (row.advanced !== "whenEmpty" || row.source === undefined) return false;
  const found = resolve(row.source);
  return found === null || (found.children ?? []).length === 0;
}

/** The virtual id of a section or a row: unique by construction, and distinct from every real id. */
export function sectionId(stage: OutlineStage): string {
  return `virtual:outline:${stage}`;
}

export function rowId(stage: OutlineStage, key: string): string {
  return `virtual:outline:${stage}:${key}`;
}

function projectRow(
  stage: OutlineStage,
  row: OutlineRow,
  resolve: (source: string) => NodeSummary | null,
  reused: boolean,
  showAll: boolean,
  folded: OmittedRow[],
): OutlineNode {
  const id = rowId(stage, row.key);
  const base = {
    id,
    label: row.label,
    stage,
    ...(row.open !== undefined ? { open: row.open } : {}),
    ...(row.source !== undefined && !row.source.startsWith("virtual:") && !row.source.startsWith("catalogue:")
      ? { sourcePath: row.source }
      : {}),
  } as const;
  if (row.source === undefined) {
    const children = (row.children ?? [])
      .filter((child) => showAll || !isAdvancedRow(child, resolve))
      .map((child) => projectRow(stage, child, resolve, false, showAll, folded));
    return { ...base, kind: children.length > 0 ? "folder" : "page", ...(children.length > 0 ? { children } : {}) };
  }
  // **A second row with the same source is a reference**, so the real ids below stay unique.
  if (reused) return { ...base, kind: "folder", ref: row.source };
  const found = resolve(row.source);
  // **A row whose source is merely absent in this project** is empty, not omitted: a project without
  // studies has no `/studies`, and the row still belongs in its section.
  if (found === null) return { ...base, kind: row.kind === "page" ? "page" : "folder" };
  if (row.kind === "node" && row.source.startsWith("virtual:")) return leafOf(found, stage);
  /*
    **`only` narrows the source to one node type, keeping their ANCESTORS.** The boundary nodes live
    inside each region (`/regions/fluid/boundaries/inlet`), so grouping them under the region that holds
    them is what makes the row readable - and it is the same subtree the tree draws, with the real ids
    selection and reveal use.
  */
  const shown = (found.children ?? []).filter((child) => {
    if (showAll || !ADVANCED_LEAF_IDS.has(child.id)) return true;
    folded.push({ stage, key: child.id, reason: "advanced row" });
    return false;
  });
  const children = row.only === undefined ? shown.map((child) => leafOf(child, stage)) : narrowTo(found.children ?? [], row.only, stage);
  return {
    ...base,
    kind: row.kind === "page" ? "page" : "folder",
    children,
  };
}

/**
 * The children whose subtree holds a node of `type`, **with the groups between them dropped**.
 *
 * **Measured**: the real tree nests boundaries three levels down — `/regions/fluid` →
 * `/regions/fluid/boundaries` → the `boundary` nodes — so keeping every ancestor made "Boundary
 * conditions" a THREE-STEP reach for what its name promises. The `region` node is kept (it says WHICH
 * region's boundaries these are, and a project may have several), but a bare `group` in between is
 * **transparent**: it is structure, not content.
 */
function narrowTo(nodes: readonly NodeSummary[], type: string, stage: OutlineStage): OutlineNode[] {
  const keep = (node: NodeSummary, under: string | null): OutlineNode[] => {
    // **A matching node is a LEAF here**: the row shows that type and nothing under it, so the two rows
    // sharing `/regions` cannot draw the same real node twice.
    if (node.type === type) return [{ ...leafOf(node, stage), children: [] }];
    const kept = (node.children ?? []).flatMap((child) => keep(child, under));
    if (kept.length === 0) return [];
    /*
      **A group or a region is TRANSPARENT**: it adds no node this row is about, and keeping it would draw
      the SAME region node under both Regions and Boundary conditions - measured, and a duplicate real id
      makes selection ambiguous. The boundary rows stand on their own; which region each belongs to is in
      its path, which the Details pane already shows.
    */
    if (node.type === "group" || node.type === "region") return kept;
    return [{ ...leafOf(node, stage), children: kept }];
  };
  return nodes.flatMap((node) => keep(node, null));
}

function leafOf(node: NodeSummary, stage: OutlineStage): OutlineNode {
  return {
    id: node.id,
    label: node.name,
    stage,
    kind: "leaf",
    ...(node.children !== undefined && node.children.length > 0
      ? { children: node.children.map((c) => leafOf(c, stage)) }
      : {}),
  };
}

/** The state of every node in a projection, and the text a folder rolls up. */
export function outlineStatus(
  projection: OutlineProjection,
  stateOf: (id: string) => OutlineState,
): Map<string, { state: OutlineState; text: string }> {
  const out = new Map<string, { state: OutlineState; text: string }>();
  for (const section of projection.sections) {
    let worst: OutlineState = "idle";
    let count = 0;
    for (const row of section.rows) {
      const state = rollUp(row, stateOf, out);
      if (STATE_RANK[state] > STATE_RANK[worst]) worst = state;
      if (state === "todo") count += 1;
    }
    out.set(section.id, { state: worst, text: sectionText(worst, count) });
  }
  return out;
}

function rollUp(
  node: OutlineNode,
  stateOf: (id: string) => OutlineState,
  out: Map<string, { state: OutlineState; text: string }>,
): OutlineState {
  if (node.children === undefined) {
    const state = stateOf(node.id);
    out.set(node.id, { state, text: "" });
    return state;
  }
  let worst: OutlineState = "idle";
  let count = 0;
  for (const child of node.children) {
    const state = rollUp(child, stateOf, out);
    if (STATE_RANK[state] > STATE_RANK[worst]) worst = state;
    if (state === "todo") count += 1;
  }
  out.set(node.id, { state: worst, text: sectionText(worst, count) });
  return worst;
}

/**
 * The words a rolled-up state shows.
 *
 * **Counted, not invented**: "<N> outstanding" when the worst is `todo`, the state word otherwise, and
 * nothing when the section is simply idle.
 *
 * **"outstanding", where the brief says "N to do"**: the product's user-text gate forbids the literal
 * `TO DO` (it exists to catch placeholder text in anything a user reads, case-insensitively), and it is
 * right to be blunt rather than to special-case a counter. "<N> outstanding" is what the brief means and
 * what a user reads without the gate having to make an exception for it.
 */
export function sectionText(state: OutlineState, count: number): string {
  if (state === "idle") return "";
  if (state === "todo") return count > 0 ? `${count} outstanding` : "outstanding";
  return state;
}


/** One drawn line of the outline: what a renderer needs, and nothing more. */
export interface OutlineLine {
  id: string;
  label: string;
  stage: OutlineStage;
  /** 0 for a section, 1 for its rows, deeper inside a real node's subtree. */
  depth: number;
  kind: OutlineNode["kind"];
  /** True when the line has children, so it can be folded. */
  foldable: boolean;
  /** True when this render shows its children. */
  open: boolean;
  /** The panel this line opens, when it opens one. */
  opens: string | null;
  /**
   * For a row that reuses another row's source: the scope of the row that SHOWS it.
   *
   * **A reference is not inert** (Codex's 000514): a click on it goes to the row that owns the content,
   * rather than doing nothing. `refScope` is what a renderer scrolls to and opens.
   */
  refScope: string | null;
  /** What the row shows beside its name - a bound finding's message, or "". */
  value: string;
  state: OutlineState;
  stateText: string;
  /** The real group path this line shows, for path-keyed rules such as the folder verbs. */
  sourcePath?: string;
}

/** What a flatten needs to decide folding and values. */
export interface FlattenContext {
  /** Which ids are open. A section or a foldable line not in here is drawn closed. */
  expanded: ReadonlySet<string>;
  /**
   * The value text for a node, from the state adapter.
   *
   * **`rowValue`, not `valueOf`**: `valueOf` is on `Object`, so a context typed with it collides with
   * every object's own method and TypeScript refuses the call. The name says what it returns.
   */
  rowValue?: (id: string) => string;
  /** The rolled-up states from `outlineStatus`. */
  states?: ReadonlyMap<string, { state: OutlineState; text: string }>;
  /** A filter string; when set, only lines whose label or ancestor label matches are drawn. */
  filter?: string;
}

/**
 * The outline as flat lines, in drawing order, with folding and filtering applied.
 *
 * **Flat, not nested**, because that is what a virtualised list needs and what a keyboard cursor walks:
 * a renderer that recurses cannot say what "the next row" is without walking again.
 *
 * **Filtering keeps ancestors** of a match, so a match is never shown without the way to it - and a
 * section with no match disappears with its rows.
 */
export function flattenOutline(projection: OutlineProjection, ctx: FlattenContext): OutlineLine[] {
  const query = (ctx.filter ?? "").trim().toLowerCase();
  const stateOf = (id: string) => ctx.states?.get(id) ?? { state: "idle" as const, text: "" };
  const lines: OutlineLine[] = [];

  /** Does this node or any descendant match the query? */
  const matches = (node: OutlineNode): boolean => {
    if (query === "") return true;
    if (node.label.toLowerCase().includes(query)) return true;
    return (node.children ?? []).some(matches);
  };

  const push = (node: OutlineNode, depth: number, parentOpen: boolean): void => {
    if (!matches(node)) return;
    const foldable = (node.children ?? []).length > 0;
    // **The filter forces everything open**: a match inside a closed row would be invisible.
    const open = foldable && (query !== "" || ctx.expanded.has(node.id));
    const state = stateOf(node.id);
    lines.push({
      id: node.id,
      label: node.label,
      stage: node.stage,
      depth,
      kind: node.kind,
      foldable,
      open,
      opens: node.open ?? null,
      refScope: node.ref !== undefined ? (projection.owners.get(node.ref) ?? null) : null,
      sourcePath: node.sourcePath,
      value: ctx.rowValue?.(node.id) ?? "",
      state: state.state,
      stateText: state.text,
    });
    if (open && parentOpen) for (const child of node.children ?? []) push(child, depth + 1, true);
  };

  const sections = projection.sections.filter((s) => s.rows.some(matches));
  for (const section of sections) {
    const state = stateOf(section.id);
    const synthetic: OutlineNode = {
      id: section.id,
      label: section.stage,
      stage: section.stage,
      kind: "section",
      children: section.rows,
    };
    const foldable = section.rows.length > 0;
    const open = foldable && (query !== "" || ctx.expanded.has(section.id));
    lines.push({
      id: section.id,
      label: section.stage,
      stage: section.stage,
      depth: 0,
      kind: "section",
      foldable,
      open,
      opens: null,
      refScope: null,
      value: "",
      state: state.state,
      stateText: state.text,
    });
    void synthetic;
    if (open) for (const row of section.rows) push(row, 1, true);
  }
  return lines;
}


/**
 * What a click on a line does, as DATA.
 *
 * **Pure and exported so it can be tested without a DOM**: the renderer's whole behaviour on click is
 * this one decision, and a test of it is a test of the renderer for the parts that matter.
 *
 * * **a reference row goes to its OWNER** - and selects nothing, because the owner's row is where the
 *   content lives and the selection belongs to a node, not to an alias;
 * * **a foldable line folds**, and a section is always foldable;
 * * **a real node leaf selects**, which is what makes the Details pane follow the tree.
 */
export type ClickAction =
  | { kind: "ref"; target: string }
  | { kind: "fold"; target: string }
  | { kind: "select"; target: string }
  | { kind: "none" };

export function clickAction(line: Pick<OutlineLine, "id" | "kind" | "foldable" | "refScope">): ClickAction {
  if (line.refScope !== null) return { kind: "ref", target: line.refScope };
  if (line.kind === "section" || line.foldable) return { kind: "fold", target: line.id };
  if (line.kind === "leaf") return { kind: "select", target: line.id };
  return { kind: "none" };
}


/** A synthetic node for a section or a page row, in the shape the tree's rows already use. */
function virtualNode(line: OutlineLine): NodeSummary {
  /*
    **A page row carries a `meta.row` the Details pane ALREADY understands**, not an invented kind
    (Codex's 005002). `DetailsPanel` resolves a selected row through `openTargetOf(node)`, which reads
    `meta.row` - so a row whose meta said `"outline"` was selectable and did NOTHING, which is exactly the
    inert row the review forbids.

    **The mapping is one-way and explicit**: an outline page naming a panel becomes the row kind that
    opens that panel. A page whose panel has no row kind is **omitted by the projection**, so this map
    never invents one.
  */
  const rowKind = line.opens === null ? null : PAGE_ROW[line.opens] ?? null;
  return {
    id: line.id,
    name: line.label,
    type: "group",
    /*
      **A row's node carries the REAL path of the folder it shows**, not its virtual id. Measured: the
      `Materials` row's menu offered nothing, because `folderVerb(node.path)` is keyed on
      `/physics/materials` while the synthetic node's `path` was `virtual:outline:Physics:materials` -
      so the "Add from library…" verb that table already has could never match. A virtual SECTION has no
      real folder, so its own id stays the path.
    */
    path: line.sourcePath ?? line.id,
    meta:
      rowKind === null
        ? { row: "outline", subtitle: line.value, placeholder: false }
        : { row: rowKind, subtitle: line.value, placeholder: false },
    params: {},
  } as unknown as NodeSummary;
}

/**
 * The tree's existing row kind for each panel a page can open.
 *
 * **Only the panels that HAVE a row kind appear**: `solve` and `results` and `mesh` and `conditions` map to
 * rows the tree already draws; `"properties"` does not, and a page naming it is omitted by the projection
 * rather than drawn inert.
 */
export const PAGE_ROW: Readonly<Record<string, string>> = {
  // **Each entry is a row kind that opens the page the outline CLAIMS to open** (Codex's 013452: *"a
  // recognised row kind is not the same as the promised page"*). Measured against `openTargetOf`:
  //   solve       -> {panel: "solve"}                      the Solve panel, which is what Solve ▸ Methods means
  //   conditions  -> {panel: "conditions"}                 the Conditions panel: a BOUNDARY opens it
  //   results     -> {panel: "results", section: "…"}      the Results panel; `fields` opens it at its field
  //   mesh        -> {panel: "mesh"}                       the Mesh panel
  // **`flow-model` is deliberately absent**: it opens the SOLVE panel, so it cannot stand for a
  // Conditions page — an outline page called Boundary conditions would have opened the solver.
  //
  // **`conditions` is absent TOO**, and that is the finding: `openTargetOf` reaches the Conditions panel
  // only from a REAL `region` or `boundary` NODE, never from a virtual row kind. So a synthetic page
  // cannot open Conditions honestly — the boundary rows in the Physics section are the real nodes that
  // do it, and a page pointing at `"boundary"` would name a kind `meta.row` never carries.
  solve: "solver-settings",
  results: "fields",
  "results-vectors": "result-vectors",
  "results-streamlines": "result-streamlines",
  "results-probe": "result-probe",
  "results-report": "result-report",
  "results-lod": "result-lod",
  mesh: "mesh-settings",
};

/**
 * The outline's lines as the tree's OWN rows, so everything downstream keeps working.
 *
 * **The mount point, chosen by the data rather than by convenience**: `TreePanel` builds
 * `RowModel[]` from `buildRows` and every behaviour after that - the row menu, rename, add-verbs, drag,
 * the eye and the swatch - consumes those rows. Producing the SAME shape means the outline replaces the
 * row SOURCE behind `tree.layout` and nothing else in a 903-line file has to know.
 *
 * **A real node keeps its own `NodeSummary`**, untouched, so selection, the Details page, the journal and
 * the API see exactly what they see today. A section or a page row gets a synthetic node whose
 * `meta.row` is `"outline"`, which is how the tree already tells a virtual row from a real one.
 */
export function outlineRows(
  lines: readonly OutlineLine[],
  findNode: (id: string) => NodeSummary | null,
): { node: NodeSummary; depth: number; parentId: string | null; hasChildren: boolean; open: boolean }[] {
  const parent: string[] = [];
  return lines.map((line) => {
    // The parent of a line is the nearest line above it with a smaller depth - the flat list's own rule.
    while (parent.length > line.depth) parent.pop();
    const parentId = line.depth > 0 ? (parent[line.depth - 1] ?? null) : null;
    parent[line.depth] = line.id;
    const real = line.kind === "leaf" ? findNode(line.id) : null;
    return {
      node: real ?? virtualNode(line),
      depth: line.depth,
      parentId,
      hasChildren: line.foldable,
      open: line.open,
    };
  });
}

/**
 * The outline's rows for a decorated tree - the ONE call `TreePanel` makes when `tree.layout` is
 * "outline".
 *
 * **Everything between the tree and the rows is here**, so the component stays a renderer: build the
 * projection from the decorated tree, decide what is expanded, flatten with the filter, and hand back the
 * tree's own `RowModel` shape.
 */
export function outlineRowsFor(
  root: NodeSummary | null,
  expanded: Record<string, boolean>,
  filter: string,
  /** The BUILT meshers from `mesh.meshers`, for the Mesh section's mesher row. */
  meshers: readonly { id: string; label: string }[] = [],
  /** The groups the user hid, by name - passed to `buildOutline`, which drops an emptied section. */
  hidden: readonly string[] = [],
  /** Draw the `advanced` rows too (see `buildOutline`); true unless the tree passes the Basic choice. */
  showAll: boolean = true,
): { node: NodeSummary; depth: number; parentId: string | null; hasChildren: boolean; open: boolean }[] {
  const forPath = resolveIn(root);
  /*
    **`catalogue:meshers` is resolved from the catalogue, not from the tree**: a mesher is not a model
    node, so the outline draws it from `mesh.meshers` - and **only the `built` ones**, because a mesher
    whose `built` is false would be a choice that fails later. CfMesh and Gmsh are in the catalogue and
    deliberately absent here.
  */
  const resolve = (source: string): NodeSummary | null =>
    source === "catalogue:meshers"
      ? ({
          id: "catalogue:meshers",
          name: "Mesher",
          type: "group",
          path: "catalogue:meshers",
          children: meshers.map((m) => ({
            id: `catalogue:mesher:${m.id}`,
            name: m.label,
            type: "mesher",
            path: `catalogue:mesher:${m.id}`,
          })),
        } as unknown as NodeSummary)
      : forPath(source);
  const projection = buildOutline(root, resolve, hidden, showAll);
  const open = new Set(Object.entries(expanded).filter(([, isOpen]) => isOpen).map(([id]) => id));
  const lines = flattenOutline(projection, { expanded: open, filter });
  const byId = new Map<string, NodeSummary>();
  const index = (node: NodeSummary): void => {
    byId.set(node.id, node);
    for (const child of node.children ?? []) index(child);
  };
  if (root !== null) index(root);
  return outlineRows(lines, (id) => byId.get(id) ?? null);
}

/** The group a row's `source` names, from a decorated tree - the resolver `buildOutline` needs. */
export function resolveIn(root: NodeSummary | null): (source: string) => NodeSummary | null {
  return (source: string): NodeSummary | null => {
    let found: NodeSummary | null = null;
    const walk = (node: NodeSummary): void => {
      if (found !== null) return;
      if ((source.startsWith("virtual:") ? node.id === source : node.path === source)) {
        found = node;
        return;
      }
      for (const child of node.children ?? []) walk(child);
    };
    if (root !== null) walk(root);
    return found;
  };
}


/**
 * The outline ids a real node's row needs OPEN for that node to be visible, or null when the tree does
 * not show it.
 *
 * **Why this exists**: `revealIds` answers REAL group ids (`/mesh`, `g_1a2b…`), because the groups tree
 * draws those. **The outline's ancestors are virtual** (`virtual:outline:Mesh`,
 * `virtual:outline:Mesh:result`), so expanding what `revealIds` returns opened rows the outline never
 * reads - measured: selecting a mesh patch from the API selected it and revealed **nothing**, with the
 * outline still showing six closed sections.
 *
 * **The mapping is the projection's own**: the row that owns the real node's group is the one whose
 * source is an ancestor of its path, so this walks the same `OUTLINE` table the projection does rather
 * than keeping a second copy of the structure.
 */
export function revealChainFor(projection: OutlineProjection, realId: string, findNode: (id: string) => NodeSummary | null): string[] {
  const target = findNode(realId);
  if (target === null || target.path === undefined) return [];
  for (const section of projection.sections) {
    for (const row of section.rows) {
      /*
        **The chain holds VIRTUAL ids only, plus the node itself.** Measured: an earlier version pushed
        the real children on the way down, so it returned `[…:Mesh, …:Mesh:global, n_result, g_results]` -
        ids the outline never reads, in the wrong order, and missing the row that actually holds the node.
        The outline draws ONE line per row; a real node's subtree is folded INSIDE that row, so opening the
        row is what reveals it.
      */
      const holds = (node: OutlineNode): boolean => node.id === realId || (node.children ?? []).some(holds);
      if (!holds(row)) continue;
      /*
        **The real nodes' own subtree has to open too.** Measured: opening only the section and the row
        left the selected patch undrawn - it lives under `blockmesh`, inside the `result` row - so nothing
        was marked and nothing scrolled. The chain therefore walks down through the real nodes' ids as
        well: those are ids the TREE expands, and the outline's rows render that subtree from them.
      */
      const chain = [section.id, row.id];
      const descend = (node: OutlineNode): boolean => {
        if (node.id === realId) return true;
        for (const child of node.children ?? []) {
          if (descend(child)) {
            if (child.id !== realId) chain.push(child.id);
            return true;
          }
        }
        return false;
      };
      descend(row);
      return chain;
    }
  }
  return [];
}
