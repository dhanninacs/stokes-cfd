import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { MousePointerClick, TriangleAlert } from "lucide-react";
import type { NodeStatus, NodeSummary } from "../api/types";
import { basename } from "../state/parts";
import { describePick, findNode, useStore } from "../state/store";
import { Kbd, Panel, SectionTitle } from "../components/ui";
import { Tooltip } from "../components/Tooltip";
import { useShortcut } from "../workspace/keymap";
import { formatNumber } from "../util/units";
import { NameField } from "./PlainField";
import FolderCard from "./FolderCard";
import TagCard from "./TagCard";
import BodyMeshCard from "./BodyMeshCard";
import { propertyWhy } from "./propertyValue";
import { isFolderNode } from "./typeWord";
import { ConditionPicker, MaterialPicker } from "./NodeVerbs";
import { changedFromDefault, hasBasicSet, readSetupContext, visibleFields } from "../setup/basicSets";
import BasicAdvanced from "./BasicAdvanced";
import BodyRoleChips from "./BodyRoleChips";
import MaterialReadout from "./MaterialReadout";
import SchemaFields from "./SchemaFields.tsx"; // the .tsx: "./SchemaFields" would case-match the helpers' schemaFields.ts on Windows
import SettingsNodeFields from "./SettingsNodeFields";
import { parameterCommand, settingsMode } from "./settingsCommand";
import { isRunActive } from "./runApi";
import { loadEngines, useRunContext } from "./runControl";
import { loadMeshContext, useMeshContext } from "./meshRun";
import { findMeshNodes, isActive, phaseNow, readResult } from "./meshPanelModel";
import PatchCard from "./PatchCard";
import PickCard from "./PickCard";
import { useStudyCard } from "./explore/StudyCard";
import { useCaseCard } from "./CaseCard";
import { FOCUS_PROPERTIES_EVENT } from "./treeKeyboard";
import { Note } from "../components/Note";
import DetailsMoreButton from "./DetailsMoreButton";

/** The parameters of a part that the import fills in. They are facts about the file, so they are shown as text and never edited. */
const PART_FACTS: ReadonlySet<string> = new Set(["source", "solid", "units", "family", "triangles", "faces", "bbox"]);

/**
 * A part's internal identifiers, in a collapsed **Technical** section
 * (claude-win2's package-two review item 11: *"'B-rep version' and 'Made by n_79…' (internal, move to
 * a collapsed Technical section)"*).
 *
 * **They are not hidden**, because they answer a real question - which B-rep a face id refers to, and
 * which operation made this part - and a person debugging a stale tag needs them. But they are a hex
 * hash and a node id, so they belong behind a disclosure rather than between a body's size and its
 * colour.
 */
const PART_TECHNICAL: ReadonlySet<string> = new Set(["basis", "derived"]);

/**
 * The fields a node's own picker already owns, hidden from the schema rows.
 *
 * **One control per setting** (claude-win2's package-two review item 4): the region page showed
 * **two Material selects** - the picker above and the schema's own row below - and the same shape
 * would repeat for a boundary's condition. The picker is the one that knows what a material IS (a
 * node reference, chosen from the catalogue), so the generic row is the duplicate.
 *
 * Hidden here rather than removed from the type: the parameter is real and the API still takes it,
 * which is what lets a script set a material without the UI.
 */
const PICKER_FIELDS: Record<string, ReadonlySet<string>> = {
  region: new Set(["material"]),
  boundary: new Set(["kind"]),
};

/** Metres per unit of the length units a display system can name. */
const METRES_PER: Record<string, number> = { m: 1, mm: 1e-3, cm: 1e-2, um: 1e-6, km: 1e3, in: 0.0254, ft: 0.3048 };

function count(value: unknown): string {
  return typeof value === "number" && value > 0 ? value.toLocaleString() : "—";
}

/**
 * The read-only parameters of a part: its file and which solid of it this is, the units that file
 * declared, how many triangles and B-rep faces its mesh has, and the size of its bounding box in the
 * project's length unit.
 */
function PartFacts({ node }: { node: NodeSummary }) {
  const unitsSchema = useStore((s) => s.units);
  const system = useStore((s) => s.project?.display_units);
  const params = node.params as Record<string, unknown>;
  const source = typeof params.source === "string" ? params.source : "";
  const fileUnits = typeof params.units === "string" ? params.units : "";
  // Which solid of the file this part is; absent from a project saved before parts were one solid each.
  const solid = typeof params.solid === "number" && Number.isInteger(params.solid) && params.solid >= 0 ? params.solid : null;

  // The bounding box is minimum then maximum corner in metres; its size is shown in the project's length unit.
  const raw = params.bbox;
  const box = Array.isArray(raw) && raw.length === 6 && raw.every((v) => typeof v === "number" && Number.isFinite(v)) ? (raw as number[]) : null;
  const symbol = (system && unitsSchema?.dimensions.length?.display[system]) || "m";
  const scale = METRES_PER[symbol] ?? 1;
  const unit = METRES_PER[symbol] ? symbol : "m";
  const size = box ? [box[3] - box[0], box[4] - box[1], box[5] - box[2]].map((v) => formatNumber(v / scale, 4)).join(" × ") : null;
  const corners = box ? `from ${box.slice(0, 3).map((v) => formatNumber(v / scale, 4)).join(", ")} to ${box.slice(3).map((v) => formatNumber(v / scale, 4)).join(", ")} ${unit}` : "";

  return (
    <div role="group" aria-label="Part details" className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-[12.5px]">
      <span className="muted">Source</span>
      {source ? (
        <Tooltip label={source} side="top" describe={false}>
          <span className="truncate">{basename(source)}</span>
        </Tooltip>
      ) : (
        <span className="muted">—</span>
      )}
      {solid !== null && (
        <>
          <span className="muted">Solid</span>
          <Tooltip label={`Index ${solid} in the source file (0 is the first)`} side="top" describe={false}>
            <span>{`#${solid + 1}`}</span>
          </Tooltip>
        </>
      )}
      <span className="muted">File units</span>
      <span className={fileUnits ? "" : "muted"}>{fileUnits || "—"}</span>
      <span className="muted">Triangles</span>
      <span>{count(params.triangles)}</span>
      <span className="muted">Faces</span>
      <span>{count(params.faces)}</span>
      <span className="muted">Size</span>
      {size ? (
        <Tooltip label={corners} side="top" describe={false}>
          <span className="truncate">{`${size} ${unit}`}</span>
        </Tooltip>
      ) : (
        <span className="muted">—</span>
      )}
    </div>
  );
}

/**
 * The node's status: a 7 px dot (the colour the tree uses for it) and 11 px text, with no fill behind
 * them (DESIGN.md section 3b). Only a failed node's text is coloured; the rest stays quiet.
 */
function StatusPill({ status }: { status: NodeStatus }) {
  const known = status === "ok" || status === "warning" || status === "stale" || status === "failed";
  return (
    <span className="inline-flex items-center gap-1.5 text-[11px] font-medium" style={{ color: status === "failed" ? "var(--danger)" : "var(--text-muted)" }}>
      <span className={`dot ${known ? status : "new"}`} aria-hidden="true" />
      {status}
    </span>
  );
}

/** Enter in the tree lands here: the first quantity field, else the first field, else the name. */
function firstField(root: HTMLElement | null): HTMLElement | null {
  if (!root) return null;
  const pick = (selector: string) => root.querySelector<HTMLElement>(selector);
  return (
    pick('[data-prop-field="quantity"]') ??
    pick('[data-prop-field="plain"], [data-prop-switch] [role="switch"]') ??
    pick('[data-prop-field="name"]:not(:disabled)') ??
    pick('[data-prop-field="action"]:not(:disabled)')
  );
}

/** The key caps are the workspace's bindings (`palette.open`, `view.fit`); an action without a chord shows none. */
function EmptyState() {
  const palette = useShortcut("palette.open");
  const fit = useShortcut("view.fit");
  return (
    <div className="px-4 py-4">
      <div className="section-title">Properties</div>
      <Note className="mt-2 text-[12.5px]" tip="Pick an item in the tree or a patch in the viewport.">Nothing is selected</Note>
      <ul className="mt-3 space-y-2 text-[12.5px]">
        <li className="flex items-center gap-2.5">
          <span className="grid min-w-[52px] shrink-0 place-items-start">{palette.hint && <Kbd>{palette.hint}</Kbd>}</span>
          <span className="muted">Run a command</span>
        </li>
        <li className="flex items-center gap-2.5">
          <span className="grid min-w-[52px] shrink-0 place-items-start">{fit.hint && <Kbd>{fit.hint}</Kbd>}</span>
          <span className="muted">Fit the part in view</span>
        </li>
        <li className="flex items-center gap-2.5">
          <span className="grid min-w-[52px] shrink-0 place-items-start" style={{ color: "var(--text-muted)" }}>
            <MousePointerClick size={16} aria-label="Click" />
          </span>
          <span className="muted">Click a patch to select it</span>
        </li>
      </ul>
    </div>
  );
}

/**
 * The card's heading: the node type's own label, except for a FOLDER.
 *
 * **`group` is two things at once** (claude-win2's package-two review item 8): a skeleton folder such
 * as `/geometry/tags`, and a real container a multi-solid import makes so that picking it stands for
 * all its parts. The owner reads `GROUP` on a folder and does not know what it means, while "Group"
 * is exactly right for the second - so the heading follows the PATH rather than the type, which is
 * the distinction the model itself uses.
 */
function titleFor(node: NodeSummary, label: string | undefined): string {
  if (node.type === "project") return "Project";
  if (node.type !== "group") return label ?? node.type;
  // A path a user reads as a folder: a top-level group, or one of the fixed sub-folders.
  return isFolderNode(node) ? "Folder" : (label ?? node.type);
}


/**
 * A region's material id, from its own parameters.
 *
 * **The parameter holds a NODE REFERENCE, not a name** - measured from the tree: a region carries
 * `"material": "n_9b380717"` - so this reads it as the id a picker needs, and treats anything else as
 * absent rather than passing a name where an id belongs.
 */
function materialOf(node: NodeSummary): string | null {
  const value = node.params?.material;
  return typeof value === "string" && value !== "" ? value : null;
}

/** A boundary's condition kind, from its own parameters (`"kind": "velocity_inlet"`). */
function conditionOf(node: NodeSummary): string | null {
  const value = node.params?.kind;
  return typeof value === "string" && value !== "" ? value : null;
}

export default function PropertiesPanel() {
  const tree = useStore((s) => s.tree);
  const selectedId = useStore((s) => s.selectedId);
  const selectedPatch = useStore((s) => s.selectedPatch);
  const picked = useStore((s) => s.picked);
  const pickMode = useStore((s) => s.pickMode);
  const geometry = useStore((s) => s.geometry);
  const nodeTypes = useStore((s) => s.nodeTypes);
  const execute = useStore((s) => s.execute);
  const connected = useStore((s) => s.connected);
  const run = useStore((s) => s.run.runs[0] ?? null);
  const meshLive = useStore((s) => s.mesh);
  const engines = useRunContext((s) => s.engines);
  const enginesLoaded = useRunContext((s) => s.loaded);
  const meshers = useMeshContext((s) => s.meshers);
  const meshersLoaded = useMeshContext((s) => s.loaded);
  const rootRef = useRef<HTMLDivElement>(null);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});

  const node = findNode(tree, selectedId);
  const patch = geometry.find((g) => g.name === selectedPatch);
  const pick = describePick(picked, pickMode);
  const schema = node ? nodeTypes[node.type] : undefined;
  const isPart = node?.type === "part";
  const mode = node ? settingsMode(node) : null;
  const meshResult = findMeshNodes(tree).result;
  const settingsLocked = !connected || (mode === "solver"
    ? run !== null && isRunActive(run.state)
    : mode === "mesh" && isActive(phaseNow(readResult(meshResult), meshLive)));
  useEffect(() => {
    if (!connected) return;
    if (mode === "solver" && !useRunContext.getState().loaded) void loadEngines();
    if (mode === "mesh" && !useMeshContext.getState().loaded) void loadMeshContext();
  }, [connected, mode]);
  /**
   * A container that gets a folder's page: the root, and a group that READS as a folder.
   *
   * **`group` alone was the first version, and it was wrong** (claude-win2's verdict leftover 5): a
   * multi-solid import makes a group to stand for its parts, so `demo_pipe · group` got the folder
   * card - "4 items inside" - and the one-sentence explanation never showed, because it was gated on
   * `!isFolder`. The distinction the model already draws is the path, and `isFolderNode` is the one
   * place that says it, so this asks rather than repeating the test.
   */
  const isFolder = node !== null && (node.type === "project" || isFolderNode(node));
  /**
   * **A region's own verbs and a boundary's** (claude-win2's 0200 item 6, the 0630 go-ahead): a region
   * gets its Material and its kind, a boundary its condition. Every one calls a command that already
   * exists - `region.set` and `boundary.assign` - so nothing here needed a new server verb.
   */
  const isRegion = node?.type === "region";
  const isBoundary = node?.type === "boundary";
  // A study node: the Study card (Explore) goes under its fields, and the parameters it shows itself are not fields.
  const study = useStudyCard(node);
  // An imported OpenFOAM case: the case card shows its import facts as text and offers its settings and Run.
  const foamCase = useCaseCard(node);
  /** The part's internal identifiers, rendered in the collapsed Technical section. */
  const technical = useMemo(() => {
    if (!isPart || schema === undefined) return null;
    const kept = Object.entries(schema.properties).filter(([name]) => PART_TECHNICAL.has(name));
    return kept.length === 0 ? null : Object.fromEntries(kept);
  }, [isPart, schema]);
  const pickerOwned = node === null ? undefined : PICKER_FIELDS[node.type];
  // Boundary, region, material and body pages show a few Basic fields and the rest under Advanced. The field
  // levels follow the boundary's kind and the run's physics (`setup/basicSets.ts`); other pages show all.
  const conditionKinds = useStore((s) => s.conditionKinds);
  const setupCtx = useMemo(() => readSetupContext(tree), [tree]);
  const levels = useMemo(
    () =>
      node !== null && schema !== undefined && mode === null && hasBasicSet(node.type)
        ? visibleFields(node.type, Object.keys(schema.properties), node.params as Record<string, unknown>, setupCtx, conditionKinds)
        : null,
    [node, schema, mode, setupCtx, conditionKinds],
  );
  const [reveal, setReveal] = useState(0);
  const owned = (name: string): boolean =>
    !(isPart && PART_FACTS.has(name)) &&
    !(isPart && PART_TECHNICAL.has(name)) &&
    !(pickerOwned?.has(name) ?? false) &&
    !study.hidden.has(name) &&
    !foamCase.hidden.has(name);
  const include = (name: string): boolean => owned(name) && (levels === null || levels[name] !== "hidden");
  const includeAt = (level: "basic" | "advanced") => (name: string): boolean => owned(name) && levels?.[name] === level;
  const namesAt = (level: "basic" | "advanced"): string[] =>
    schema === undefined ? [] : Object.keys(schema.properties).filter(includeAt(level));
  const advancedNames = levels === null ? [] : namesAt("advanced");
  const basicNames = levels === null ? [] : namesAt("basic");
  const advancedChanged =
    schema !== undefined && node !== null && changedFromDefault(schema.properties, node.params as Record<string, unknown>, advancedNames.filter((n) => n !== "tag" && n !== "color")).length > 0;

  // A field was typed in or reverted: its old refusal is no longer under it.
  const dropFieldError = useCallback((name: string) => {
    setFieldErrors((e) => {
      if (!(name in e)) return e;
      const next = { ...e };
      delete next[name];
      return next;
    });
  }, []);

  // A field's commit: a silent parameter.set. A refusal shows under the field from `fieldErrors`
  // and is rethrown, so the field can tell success from refusal; a success arrives as the tree's
  // new value and clears the field's old one.
  const commitParam = useCallback(
    async (name: string, value: unknown) => {
      if (!node || (mode !== null && settingsLocked)) return;
      dropFieldError(name);
      try {
        const request = parameterCommand(node, name, value);
        await execute(request.command, request.params, { silent: true });
      } catch (err) {
        setFieldErrors((e) => ({ ...e, [name]: err instanceof Error ? err.message : String(err) }));
        throw err;
      }
    },
    [execute, node, dropFieldError, mode, settingsLocked],
  );

  // New selection: no field's error of the previous one.
  useEffect(() => {
    setFieldErrors({});
  }, [selectedId]);

  // Enter in the tree: put the cursor in the first field. The panel may be about to appear (Enter
  // also selects), so look again on the next frame before giving up.
  useEffect(() => {
    const focus = (): boolean => {
      const el = firstField(rootRef.current);
      if (!el) return false;
      el.focus();
      if (el instanceof HTMLInputElement) el.select();
      return true;
    };
    const onRequest = () => {
      if (!focus()) requestAnimationFrame(focus);
    };
    window.addEventListener(FOCUS_PROPERTIES_EVENT, onRequest);
    return () => window.removeEventListener(FOCUS_PROPERTIES_EVENT, onRequest);
  }, []);

  /**
   * **Where the node's own verbs live.** They go ABOVE the schema fields because they are the thing a user
   * comes to this page to change: the fields below are the node's stored values, and these are what a mesh
   * and a solve read. A region gets its Material, a boundary its condition (`region.set`, `boundary.assign`).
   */
  const verbs =
    node !== null && (isRegion || isBoundary) ? (
      <div className="flex flex-col gap-1.5" data-ws="node-verbs">
        {isRegion && <MaterialPicker regionId={node.id} currentId={materialOf(node)} />}
        {isBoundary && <ConditionPicker boundaryId={node.id} current={conditionOf(node)} />}
      </div>
    ) : null;

  /** The schema's fields for a node page, filtered by `include`; nothing for a page the settings own. */
  const fields = (include_: (name: string) => boolean, key: string) =>
    node !== null && schema && mode === null ? (
      <SchemaFields
        key={key} // not node.id: the Study card beside it already has that key
        properties={schema.properties}
        values={node.params}
        displays={node.displays}
        status={node.status}
        onCommit={commitParam}
        onEdit={dropFieldError}
        include={include_}
        errors={fieldErrors}
        /*
          **A property whose method computes its value shows a dash** (verdict leftover 3):
          measured as "density 0 kg/m³" for air, whose method is `ideal_gas` - the stored
          zero is a placeholder for a number that depends on pressure and temperature.
        */
        notStored={(name) => (node.type === "property" && name === "value" ? propertyWhy(node.params.method) || null : null)}
      />
    ) : null;

  /**
   * **The internal identifiers, folded away** (review item 11). A B-rep hash and an operation id answer a
   * real question and belong on the page - behind a disclosure rather than between a body's size and its colour.
   */
  const technicalBlock =
    node !== null && isPart && technical !== null ? (
      <details className="text-[12.5px]" data-ws="technical">
        <summary className="muted cursor-pointer select-none">Technical</summary>
        <div className="mt-1.5">
          <SchemaFields
            properties={technical}
            values={node.params}
            displays={node.displays}
            status={node.status}
            onCommit={commitParam}
            onEdit={dropFieldError}
            errors={fieldErrors}
          />
        </div>
      </details>
    ) : null;

  return (
    <Panel className="flex flex-col overflow-hidden">
      {/*
        **The body scrolls as a whole** (claude-win2's 1230 picture review: the Opacity row was
        clipped to "1"). The field group used to be the scroll area while the SELECTION card below it
        was a sibling taking its own height - so on a short panel the card won and the fields were
        squeezed to a sliver with no way to reach them. One scroll region means the last row is
        always reachable, whatever the panel's height.
      */}
      <div
        ref={rootRef}
        className="flex min-h-0 flex-1 flex-col overflow-y-auto"
        aria-label="Properties"
        role="region"
      >
        {!node && !patch && pick === null && <EmptyState />}
        {/* A face pick has no card of its own (T-061): the pick card stands in for the
            patch card, so nothing here reads as the whole body being selected. */}
        {!node && !patch && pick !== null && <PickCard divider={false} />}
        {node && (
          <>
            <SectionTitle
              right={
                <span className="flex items-center gap-1">
                  <StatusPill status={node.status} />
                  <DetailsMoreButton path={node.path} id={node.id} />
                </span>
              }
            >{titleFor(node, schema?.title)}</SectionTitle>
            <div className="px-3">
              <NameField key={node.id} node={node} />
              <Tooltip label={node.path} side="bottom" describe={false}>
                <div className="muted mt-1 truncate text-[11.5px]">{node.path}</div>
              </Tooltip>
              {typeof node.meta?.error === "string" && (
                <div
                  role="alert"
                  className="mt-2 flex items-start gap-2 rounded-md px-2 py-1.5 text-[12px]"
                  style={{ background: "var(--danger-soft)", color: "var(--danger)" }}
                >
                  <TriangleAlert size={13} className="mt-[2px] shrink-0" />
                  <span className="min-w-0 break-words">{node.meta.error}</span>
                </div>
              )}
            </div>
            <div className="space-y-3 px-3 py-3">
              {isPart && levels === null && <PartFacts node={node} />}
              {/*
                **A folder gets a folder's page** (claude-win2's 0530 item 3). It used to say
                *"2 items, no settings"*, which is not an answer for a folder - and it is worse now
                that the Details pane is where creation lives. The card says what the folder is for,
                lists what is in it as rows you can click, and offers its Add.
              */}
              {isFolder && <FolderCard node={node} />}
              {/*
                **A real Group says what it is** (review item 8): a multi-solid import makes one so that
                picking it stands for all its parts, which is a thing worth one sentence - the owner
                asked what "Add a group" was for, and the answer is that an assembly is a group while a
                folder is a folder.
              */}
              {node.type === "group" && !isFolder && (
                <Note className="text-[12.5px]" tip="A set of bodies imported together; selecting this stands for all of them." data-ws="group-note">
                  Imported body set
                </Note>
              )}
              {/* **A tag's own page** (review item 5): what it holds and what kind it is, in place of
                  the raw `members` JSON the schema used to render. */}
              {node.type === "tag" && <TagCard node={node} />}
              {levels !== null ? (
                /*
                  **Basic first, the rest under Advanced** (the set-up declutter, S1). A boundary shows its kind
                  and its one lead value, a region its material, a material its density and viscosity, a body
                  its role. Everything else is today's field list, unchanged, in the Advanced disclosure.
                */
                <BasicAdvanced
                  key={node.type}
                  type={node.type}
                  changed={advancedChanged}
                  hasError={advancedNames.some((name) => name in fieldErrors)}
                  reveal={reveal}
                  basic={
                    <>
                      {isPart && <BodyRoleChips node={node} />}
                      {node.type === "material" && <MaterialReadout node={node} onChange={() => setReveal((n) => n + 1)} />}
                      {verbs}
                      {basicNames.length > 0 && fields(includeAt("basic"), `${node.id}:basic`)}
                    </>
                  }
                  advanced={
                    <>
                      {isPart && <PartFacts node={node} />}
                      {advancedNames.length > 0 && fields(includeAt("advanced"), `${node.id}:fields`)}
                      {isPart && <BodyMeshCard node={node} />}
                      {technicalBlock}
                    </>
                  }
                />
              ) : (
                <>
                  {verbs}
                  {schema && mode !== null && (
                    <SettingsNodeFields
                      key={`${node.id}:settings`}
                      node={node}
                      schema={schema}
                      mode={mode}
                      tree={tree}
                      onCommit={commitParam}
                      onEdit={dropFieldError}
                      errors={fieldErrors}
                      disabled={settingsLocked}
                      engines={engines}
                      enginesLoaded={enginesLoaded}
                      meshers={meshers}
                      meshersLoaded={meshersLoaded}
                    />
                  )}
                  {fields(include, `${node.id}:fields`)}
                  {isPart && <BodyMeshCard node={node} />}
                  {technicalBlock}
                </>
              )}
              {study.card}
              {foamCase.card}
            </div>
          </>
        )}
        {/*
          **The patch card shows only when a PATCH is the subject** (review item 11: *"a second
          'Selection' card repeating triangles/vertices/faces"*).

          Selecting a body selects its patch too (`patchForNode`), so a body's page drew its own
          triangles/vertices/faces and then a card headed **Selection** with the same three numbers.
          The card is about a PATCH - its kind, colour and boundary - so it belongs where a patch is
          what was picked; on a body's page those facts are the body's.
        */}
        {patch && node === null && (
          <PatchCard key={patch.name} patch={patch} divider={node !== null} />
        )}
      </div>
    </Panel>
  );
}
