export interface QuantityValue {
  value: number;
  unit: string;
  expr?: string;
}
/** JSON-compatible data: what a `json` parameter holds. */
export type JsonValue = string | number | boolean | null | JsonValue[] | { [key: string]: JsonValue };
export type ParamValue = QuantityValue | JsonValue;
export type NodeStatus = "ok" | "warning" | "stale" | "failed" | "new";

export interface NodeSummary {
  id: string;
  type: string;
  name: string;
  status: NodeStatus;
  params: Record<string, ParamValue>;
  meta?: Record<string, unknown>;
  path: string;
  children_count: number;
  /** Display-unit strings per quantity parameter, e.g. { value: "2 m/s" }. */
  displays?: Record<string, string>;
  /** The root or a skeleton group: the server refuses to rename or delete it. */
  structural?: boolean;
  children?: NodeSummary[];
}

/** `license.info`: the edition this session runs as. */
export interface LicenseInfo {
  edition: "free" | "licensed";
  features: string[];
  holder: string | null;
  expires: string | null;
  id: string | null;
}

/** `engine.info`: the solver engine attached to the session, none before M3 (`foamRun`). */
export interface EngineInfo {
  attached: boolean;
  name: string | null;
  version: string | null;
}

export interface ParamSchema {
  title?: string;
  description?: string;
  type?: string | string[];
  enum?: string[];
  default?: unknown;
  items?: ParamSchema;
  "x-kind"?: string;
  "x-dimension"?: string;
  "x-ref-type"?: string;
  "x-required"?: boolean;
  "x-max-bytes"?: number;
  /**
   * Keep this property out of the property grid, **while the API keeps accepting it**.
   *
   * Set for a parameter the UI expresses somewhere else: a part's `visible` is the tree row's eye,
   * which is a view filter rather than a saved change, so drawing a second toggle here would offer
   * to save what the eye deliberately does not.
   */
  "x-hidden"?: boolean;
}
/** A property of a node type's schema (the contract name for `ParamSchema`). */
export type PropertySchema = ParamSchema;
/** The display-unit string of one quantity parameter, e.g. "2 m/s". */
export type DisplayValue = string;

export interface NodeTypeSchema {
  title: string;
  description: string;
  "x-type": string;
  "x-children": string[];
  properties: Record<string, ParamSchema>;
}

export interface CommandParamSchema {
  type?: string;
  description?: string;
  default?: unknown;
}

export interface CommandSchema {
  name: string;
  description: string;
  mutates: boolean;
  requires_project: boolean;
  feature: string | null;
  input_schema: { properties: Record<string, CommandParamSchema>; required: string[] };
}

export interface ParameterGet {
  node: string;
  name: string;
  kind: string;
  value: ParamValue;
  unit?: string;
  expr?: string | null;
  dimension?: string;
  display?: string;
}

export interface ProjectInfo {
  name: string;
  path: string | null;
  /** A few words when the folder path is long enough to hit Windows' 260-character limit. */
  path_warning?: string | null;
  display_units: string;
  nodes: number;
  can_undo: boolean;
  can_redo: boolean;
  /**
   * Whether the project has changes that are not on disk.
   *
   * **The server's own comparison** (`Project.revision` against the revision at the last write), not
   * a flag this client keeps: the Close prompt must not offer to discard work the user did not do,
   * nor lose work it did, and only the model knows which. Absent from an older server, which is why
   * it is optional - callers treat `undefined` as "assume dirty" so a missing field prompts rather
   * than silently discards.
   */
  dirty?: boolean;
  /**
   * When unsaved work from an earlier session was found beside the project (autosave's recovery copy,
   * an ISO time), or null. `project.recover` restores it, `project.discard_recovery` throws it away.
   */
  recovery?: string | null;
}

export interface UnitsSchema {
  unit_systems: string[];
  dimensions: Record<string, { si_unit: string; display: Record<string, string> }>;
}

export function isQuantity(value: ParamValue): value is QuantityValue {
  return typeof value === "object" && value !== null && "value" in value && "unit" in value;
}

/**
 * What the node events add to their ids (`node.added`, `node.changed`, `node.removed`, and the
 * geometry events that follow a silent change of status), see `stokes/api/summaries.py`. A client
 * patches its tree with them instead of asking for all of it again; read with `readPatches`.
 */
export interface NodeEventExtras {
  /** The summary of the node the event names (not on `node.removed`). */
  node?: NodeSummary;
  /** Where an added node goes: its parent's id, and its position unless it is the last sibling. */
  parent?: string;
  index?: number;
  /** The summaries of other nodes the change reached: expressions that read an edited parameter. */
  also?: NodeSummary[];
  /** More nodes were reached than an event carries: fetch the tree. */
  resync?: boolean;
}

/**
 * Payloads of the geometry.* events (stokes.geometry.store). `path` is null when the node is already
 * gone. The store reads them with `readGeometryEvent`, because an event is data from the wire.
 */
export interface ImportProgressEvent {
  nodeId: string;
  path: string | null;
  /**
   * "read" when the import starts (count 0, nothing known yet) and again once the file is read
   * (count is the number of solids), then "coarse" and "fine" for every solid.
   */
  pass: string;
  /** The solid's name in the file; null for the read. */
  solid: string | null;
  /** Position of the solid among those being meshed, from 0. */
  index: number;
  count: number;
  triangles: number;
}
export interface PartReadyEvent {
  nodeId: string;
  path: string | null;
  /** "coarse", "fine" or "cache". */
  pass: string;
  /** True once the part's final mesh is in place. */
  complete: boolean;
}
export interface PartRemovedEvent {
  nodeId: string;
  path: string | null;
}
export interface ImportFailedEvent {
  nodeId: string;
  path: string | null;
  /** `geometry.cancelled` when the user stopped the import. */
  code: string;
  message: string;
}
export type GeometryEvent =
  | ({ kind: "progress" } & ImportProgressEvent)
  | ({ kind: "ready" } & PartReadyEvent)
  | ({ kind: "removed" } & PartRemovedEvent)
  | ({ kind: "failed" } & ImportFailedEvent);

/** One member of a tag in the expanded `tag.members` form: the faces of one part. */
export interface TagMember {
  part: string;
  path: string | null;
  name: string | null;
  /** False when the part has left the project. */
  exists: boolean;
  /** True when the member names every face of the part. */
  whole: boolean;
  /** The face ids (0 to part_faces - 1) the member holds; truncated to what the part has. */
  faces: number[];
  count: number | null;
  part_faces: number | null;
  /** Face ids the member lists that the part no longer has. */
  missing: number[];
  stale: boolean;
}

/** The `members` of `tag.members`, `tag.region` and the like, one entry per part. */
export type TagMembers = TagMember[];

/** A member as the `members` parameter of the tag commands: whole part or inclusive face ranges. */
/**
 * The tag a boundary proxies, as `boundary.list` gives it. Every field is nullable because a
 * boundary whose tag is gone still lists — with `missing: true` — so the UI can show the problem
 * rather than the row vanishing.
 */
/** One provider of material records (`material.providers`), e.g. the built-in catalogue or CoolProp. */
export interface MaterialProvider {
  name: string;
  available: boolean;
  version: string;
  /** Why it is unavailable (empty when it is). */
  reason: string;
  /** Fluids the provider can offer, 0 for a provider that is not fluid-based. */
  fluids: number;
  note: string;
}

/** One property a library record carries a method for. */
export interface MaterialProperties {
  density: boolean;
  viscosity: boolean;
  specific_heat: boolean;
  thermal_conductivity: boolean;
}

/** One record of `material.library_search`. */
export interface LibraryRecord {
  provider: string;
  /** The provider's own id, which `material.add_from_library` takes. */
  fluid: string;
  name: string;
  formula: string;
  cas: string;
  /** "fluid" or "solid": the section §4.4 sorts the menu into. */
  kind: string;
  aliases: string[];
  T_min: number | null;
  T_max: number | null;
  p_max: number | null;
  T_critical: number | null;
  p_critical: number | null;
  molar_mass: number | null;
  properties: MaterialProperties;
  composition: unknown;
  basis: unknown;
  reference: { source: string; eos?: string } | null;
}

/** The answer of `material.library_search`. */
export interface LibrarySearch {
  records: LibraryRecord[];
  total: number;
}

export interface SetUpTagRef {
  id: string | null;
  name: string | null;
  path: string | null;
  /** `#rrggbb` the tag draws with, and the row's swatch colour. */
  color: string | null;
  /** The tag's own eye: whether it is drawn. */
  visible: boolean | null;
  parts: number;
  /** Face count, null while a body's count is unknown (the tag has not been read). */
  faces: number | null;
  /** The tag's ids refer to an earlier B-rep of a part (needs `tag.rematch`). */
  stale: boolean;
  /** The tag node is referenced but gone: the row is `failed`. */
  missing: boolean;
}

/** The primary value of a condition, already formatted in the project's display units. */
export interface SetUpPrimary {
  name: string;
  value: number;
  /** Without the name: "2 m/s". The row prints this rather than formatting a number itself. */
  display: string;
}

/** The region a boundary belongs to, as `boundary.list` gives it. */
export interface SetUpRegionRef {
  id: string;
  path: string;
  name: string;
}

/**
 * One entry of `boundary.list`: a typed `boundary` node, or a plain group from before typed
 * boundaries (`legacy`). Its **status** is derived by the server and by the front end from the tag
 * link and `setup.check` (ADR-0022 D2.10); nothing stores it.
 */
export interface BoundaryEntry {
  id: string;
  path: string;
  name: string;
  region: SetUpRegionRef;
  /** Present when typed; absent on a legacy group. */
  kind?: string;
  label?: string;
  family?: string;
  tag?: SetUpTagRef | null;
  primary?: SetUpPrimary | null;
  /** Finding codes the server attached to this boundary. */
  warnings?: string[];
  status?: string;
  legacy?: boolean;
}

/** The material a region holds, as `region.list` gives it (`missing` = the node is gone). */
export interface SetUpMaterialRef {
  id: string;
  /** Null when the material node is missing. */
  name: string | null;
  kind?: unknown;
  missing: boolean;
}

/** The smaller tag shape a region carries (it is read for the swatch and the body count). */
export interface SetUpRegionTagRef {
  id: string;
  name: string;
  path: string;
  color: string | null;
  visible: boolean | null;
}

/**
 * One entry of `region.list`: a typed `region` node (with `kind`, material, tag, bodies and the
 * uncovered-face count) or a legacy plain group, which carries only its id, path, name and `legacy`.
 */
export interface RegionEntry {
  id: string;
  path: string;
  name: string;
  /** The region's kind (a typed region always has one; a legacy group does not). */
  kind?: string;
  material?: SetUpMaterialRef | null;
  tag?: SetUpRegionTagRef | null;
  /** Part node ids of the whole bodies the region holds. */
  bodies?: string[];
  /** Ids of the region's boundary nodes. */
  boundaries?: string[];
  /** Faces of the bodies no boundary tag covers; null while a count is unknown. */
  uncovered_faces?: number | null;
  /** True when one of the bodies' tags refers to an earlier B-rep. */
  stale?: boolean;
  /** Finding codes the server attached to this region. */
  warnings?: string[];
  status?: string;
  legacy?: boolean;
}

export interface TagRegionMember {
  part: string;
  faces: "all" | [number, number][];
}

/** `tag.region`: the feature-angle region around the given members, without touching any tag. */
export interface TagRegion {
  members: TagRegionMember[];
}

/**
 * One parameter of a condition kind as `boundary.kinds` returns it: its use (rule, whether the
 * kind needs a value to work, and the switches it is used under) beside the JSON schema of the
 * `boundary` parameter of the same name, so a form can be built without a second query.
 */
export interface ConditionParam {
  name: string;
  rule: string;
  required: boolean;
  /** Switch parameter name to the choices under which this one is used; `{}` means always. */
  when: Record<string, string[]>;
  schema: ParamSchema;
}

/** One entry of the condition catalogue (`boundary.kinds`). */
/**
 * The set-up readiness report (`setup.check`, ADR-0022 §5, §11.1). `state` drives the status dot's
 * colour, `next` is the one-line "what to do next" the readout shows, `counts` gives the figures
 * beside it, and `findings` are what the conditions list shows in full.
 */
export type SetupState = "not_defined" | "incomplete" | "stale" | "ready";

export interface SetupFinding {
  code: string;
  severity: "error" | "warning" | "info";
  message: string;
  suggestion?: string;
  nodes?: string[];
  fix?: { action?: string; [key: string]: unknown };
}

export interface SetupCounts {
  regions: number;
  materials: number;
  boundaries: number;
  uncovered_faces: number;
  legacy: number;
}

/** One entry of `boundary.overlap`: a boundary-kind tag that already holds some of the asked faces. */
export interface BoundaryOverlap {
  tag: { id: string; name: string; path: string; color?: string | null };
  /** The boundaries that use the tag; empty for a boundary-kind tag no boundary uses yet. */
  boundaries: { id: string; name: string; path: string; kind?: string | null }[];
  part: string;
  /** Faces shared on that part; null when both hold the whole part and its face count is unknown. */
  faces: number | null;
}

/** What `boundary.overlap` answers for a set of members. */
export interface BoundaryOverlapAnswer {
  overlaps: BoundaryOverlap[];
  faces: number | null;
}

/** One part of `boundary.uncovered`: the faces no boundary holds, as merged inclusive ranges. */
export interface UncoveredPart {
  part: string;
  name: string;
  path: string;
  total: number | null;
  faces: number | null;
  ranges: [number, number][];
  /** With `describe`: what the first unassigned faces look like (SI units). */
  described?: FaceLook[];
}

/** One face as `boundary.uncovered {describe}` describes it. */
export interface FaceLook {
  face: number;
  /** m² */
  area: number;
  flat: boolean;
  centroid: [number, number, number];
  normal: [number, number, number];
}

export interface UncoveredAnswer {
  parts: UncoveredPart[];
  faces: number | null;
}

export interface SetupCheck {
  state: SetupState;
  next: string;
  counts: SetupCounts;
  findings: SetupFinding[];
}

export interface ConditionKind {
  id: string;
  label: string;
  family: string;
  fields: string[];
  needs: Record<string, string[]>;
  /** The tag colour the kind gives (#rrggbb). */
  color: string;
  glyph: string;
  /** Null when the kind can be chosen now, else the milestone that brings it. */
  arrives: string | null;
  available: boolean;
  /** False when the project's engine cannot write the kind; absent from an older server. */
  supported?: boolean;
  /** Why it cannot, in a few words, for a tooltip. */
  unsupported_reason?: string | null;
  note: string;
  params: ConditionParam[];
}
