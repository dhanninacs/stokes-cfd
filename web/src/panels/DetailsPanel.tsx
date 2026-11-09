import { useCallback, useMemo, useState, type ComponentType } from "react";
import { ChevronLeft, Info } from "lucide-react";
import { PANELS } from "../workspace/registry";
import { openTargetOf } from "./stageTreeModel";
import { useDetailsRow } from "./treeRows";
import { detailsPage } from "../workspace/detailsPage";
import MeshStepper, { MeshStepperLine } from "./MeshStepper";
import GeometryCheckPage from "./meshTaskPages/GeometryCheckPage";
import BodiesPage from "./meshTaskPages/BodiesPage";
import SizingPage from "./meshTaskPages/SizingPage";
import SurfacePage from "./meshTaskPages/SurfacePage";
import LayersPage from "./meshTaskPages/LayersPage";
import QualityPage from "./meshTaskPages/QualityPage";
import { useStore } from "../state/store";
import { findNode } from "../state/store";
import { useNextStep } from "../app/NextStep";
import { STAGE_LEAD, STAGE_WORDS, stageOfStep } from "./stageLead";
import { Note } from "../components/Note";
import { bulkActionsFor, bulkCall, fieldValues, manyTitle, sameType, type BulkAction, type ChosenRow } from "./manySelection";
import FieldsPanel from "./FieldsPanel";
import MeshPanel from "./MeshPanel";
import RunPanel from "./RunPanel";
import { MESH_TASK_FIELDS } from "./settingsPresentation";
import { typeWord } from "./typeWord";

/**
 * The Details pane: the full page for whatever the tree has selected (the layout decision 2026-10-18).
 *
 * **"The tree selection IS the tab."** The owner asked whether the stage panel should sit next to the
 * tree and show the full list for the selection; the decision was yes, which dissolves the tab strip:
 * a mesh settings node shows Mesh, a tag shows Conditions, a run shows Solve.
 *
 * ## What it renders, and where the components come from
 *
 * The page is looked up in the workspace panel registry by id (`detailsPage`), so this file names no
 * panel component - registering one is what makes it reachable here. That is what keeps the pane a
 * *move* of panels rather than a second implementation of them.
 *
 * ## The two answers that are not a panel
 *
 * * **Nothing selected** shows the stage's next-step list, which is what the Next-step chip already
 *   computes. The pane is never blank on an empty selection - an empty pane reads as broken.
 * * **Several selected** shows the common actions rather than one node's page, because a page that is
 *   right for the first selection and wrong for the rest is worse than none.
 *
 * ## Narrow windows
 *
 * Below `NARROW_PX` the pane **overlays** the tree with a back arrow, per the decision. It is the same
 * component either way: only the wrapper's placement changes, so there is one implementation of the
 * content to get right.
 */
export interface DetailsProps {
  /** True when the pane is overlaying the tree rather than sitting beside it. */
  overlay?: boolean;
  /** Leaving the overlay; absent when the pane is beside the tree and there is nowhere to go back to. */
  onBack?: () => void;
}

/** The width below which the pane overlays the tree, from the decision ("< ~1100 px"). */
export const NARROW_PX = 1100;

/**
 * **The page for a multi-selection** (claude-win2's 1400 file).
 *
 * The brief's rule, taken literally: *"show only the actions valid for every selected item ... Anything
 * not supported for several is greyed out with the reason, or absent (my rule: no entry that will be
 * refused)."* Every judgement lives in `manySelection.ts`; this is the rendering.
 *
 * **A mixed selection says why it cannot be edited.** A part and a tag share Hide, Show and Delete and
 * nothing else, so the page lists those and prints the one line that explains the missing editor, rather
 * than showing a property sheet that would apply to half of what is chosen.
 */
/** How each bulk action reads. **One table**, so the page and any future menu cannot drift. */
const BULK_LABEL: Record<BulkAction, string> = {
  show: "Show",
  hide: "Hide",
  simulate: "In simulation",
  colour: "Colour and opacity",
  delete: "Delete",
};

function ManySelection({ rows, onPick }: { rows: readonly ChosenRow[]; onPick: (id: string) => void }) {
  const execute = useStore((s) => s.execute);
  const pushToast = useStore((s) => s.pushToast);
  const [confirming, setConfirming] = useState(false);
  const runAction = async (action: BulkAction) => {
    // **Delete asks once, with the count** - the brief's words - and a second press is what acts.
    if (action === "delete") {
      if (!confirming) {
        setConfirming(true);
        pushToast("info", `Delete ${rows.length} ${rows.length === 1 ? "item" : "items"}? Press Delete again to confirm.`);
        return;
      }
      setConfirming(false);
      for (const row of rows) {
        try {
          await execute("node.remove", { node: row.id });
        } catch {
          /* execute logged the error and showed a toast */
        }
      }
      useStore.getState().clearSelection();
      return;
    }
    const call = bulkCall(action, rows.map((r) => r.id), { visible: true });
    if (call === null) return;
    try {
      await execute(call.command, call.params);
    } catch {
      /* execute logged the error and showed a toast */
    }
  };
  const types = rows.map((r) => r.type);
  const actions = bulkActionsFor(types);
  const one = sameType(types);
  return (
    <div className="details-note" data-ws="details-many">
      <p className="details-many-title" data-ws="many-title">
        {manyTitle(types, rows.length)}
      </p>
      <ul className="details-many-list">
        {rows.map((row) => (
          <li key={row.id}>
            <button type="button" className="link no-drag" onClick={() => onPick(row.id)}>
              {row.id}
            </button>
            <span className="muted"> · {row.type}</span>
          </li>
        ))}
      </ul>
      {!one && (
        <Note tip="Select items of one kind to edit their properties together." data-ws="many-mixed">
          Mixed kinds
        </Note>
      )}
      {/*
        **The bulk actions actually DO something** (claude-win2's 1400 file). They were rendered as plain
        text - a list of words that looked like a menu and did nothing - which is worse than absent: the
        brief's rule is "no entry that will be refused", and an entry that does nothing is refused
        silently.

        `geometry.style` and `geometry.simulate` already take a LIST, so Hide, Show and In simulation need
        no new command. **Delete asks ONCE, with the count**, and only then removes.
      */}
      <ul className="details-many-actions" aria-label="Actions for the selection">
        {actions.map((action) => (
          <li key={action}>
            <button
              type="button"
              className={action === "delete" ? "link no-drag bulk-danger" : "link no-drag"}
              data-ws={`many-${action}`}
              onClick={() => void runAction(action)}
            >
              {BULK_LABEL[action]}
            </button>
          </li>
        ))}
      </ul>
      {/*
        **The bulk field** (claude-win2's 1400 file): one value for every chosen row, "mixed" when they
        disagree, written as ONE undo step through `parameter.set_many`.
      */}
      {one && actions.includes("colour") && (
        <>
          <BulkColour rows={rows} />
          <BulkOpacity rows={rows} />
        </>
      )}
    </div>
  );
}


/**
 * The one property a multi-selection can edit today: the colour.
 *
 * **`parameter.set_many`, not a loop** - the command exists for exactly this, and a loop would be one
 * undo step per row. The box is empty with a "mixed" placeholder when the rows disagree, so a user can
 * see the difference between "not set" and "set differently" before typing.
 */
function BulkColour({ rows }: { rows: readonly ChosenRow[] }) {
  const tree = useStore((s) => s.tree);
  const execute = useStore((s) => s.execute);
  const [draft, setDraft] = useState<string | null>(null);
  const values = useMemo(
    () => rows.map((r) => String(findNode(tree, r.id)?.params.color ?? "")),
    [rows, tree],
  );
  const field = fieldValues(values);
  const shown = draft ?? field.value;
  const commit = async () => {
    if (draft === null || draft === "") return;
    setDraft(null);
    try {
      await execute("parameter.set_many", { nodes: rows.map((r) => r.id), name: "color", value: draft });
    } catch {
      /* execute logged the error and showed a toast */
    }
  };
  return (
    <div className="details-many-field">
      <label className="details-many-label" htmlFor="many-colour">
        Colour
      </label>
      <input
        id="many-colour"
        className="field mono"
        type="text"
        spellCheck={false}
        value={shown}
        placeholder={field.mixed ? "mixed" : ""}
        aria-label="Colour of the selected items"
        data-ws="many-colour"
        onChange={(e) => setDraft(e.target.value)}
        onBlur={() => void commit()}
        onKeyDown={(e) => {
          if (e.key === "Enter") void commit();
          else if (e.key === "Escape") setDraft(null);
        }}
      />
    </div>
  );
}


/**
 * The second bulk field: **opacity**, in the same "mixed" shape as the colour (claude-win2's 1400 file
 * lists "Colour and opacity" as one action).
 *
 * Written through `parameter.set_many` for the same reason the colour is: one undo step for the whole
 * selection, not one per row.
 */
function BulkOpacity({ rows }: { rows: readonly ChosenRow[] }) {
  const tree = useStore((s) => s.tree);
  const execute = useStore((s) => s.execute);
  const [draft, setDraft] = useState<string | null>(null);
  const values = useMemo(
    () => rows.map((r) => String(findNode(tree, r.id)?.params.opacity ?? "")),
    [rows, tree],
  );
  const field = fieldValues(values);
  const commit = async () => {
    if (draft === null || draft === "") return;
    const parsed = Number(draft);
    setDraft(null);
    if (!Number.isFinite(parsed) || parsed < 0 || parsed > 1) return;
    try {
      await execute("parameter.set_many", { nodes: rows.map((r) => r.id), name: "opacity", value: parsed });
    } catch {
      /* execute logged the error and showed a toast */
    }
  };
  return (
    <div className="details-many-field">
      <label className="details-many-label" htmlFor="many-opacity">
        Opacity
      </label>
      <input
        id="many-opacity"
        className="field mono"
        type="text"
        inputMode="decimal"
        spellCheck={false}
        value={draft ?? field.value}
        placeholder={field.mixed ? "mixed" : "0 to 1"}
        aria-label="Opacity of the selected items, 0 to 1"
        data-ws="many-opacity"
        onChange={(e) => setDraft(e.target.value)}
        onBlur={() => void commit()}
        onKeyDown={(e) => {
          if (e.key === "Enter") void commit();
          else if (e.key === "Escape") setDraft(null);
        }}
      />
    </div>
  );
}


const MESH_TASK_PAGES: Readonly<Record<string, ComponentType>> = {
  geometry: GeometryCheckPage,
  bodies: BodiesPage,
  sizing: SizingPage,
  surface: SurfacePage,
  layers: LayersPage,
  quality: QualityPage,
};

export default function DetailsPanel({ overlay = false, onBack }: DetailsProps) {
  const selectedId = useStore((s) => s.selectedId);
  const outlineRow = useStore((s) => s.outlineRow);
  // The stepper's own choice (brief M-502): Geometry check and What each body is, the two tasks
  // whose pages have no tree row of their own to open through.
  const meshTask = useStore((s) => s.meshTask);
  const tree = useStore((s) => s.tree);
  const stage = useStore((s) => s.stage);
  const TaskPage = stage === "Mesh" && meshTask !== null ? MESH_TASK_PAGES[meshTask] ?? null : null;
  const picked = useStore((s) => s.picked);

  /**
   * **The marked row, not only the selected node.** A virtual row (a stage folder, a run, a field) is
   * a choice a user makes, and the gui-v3 review's warning 2 says the pane must work for them - so the
   * row that is marked wins, exactly as the tree's own highlight does.
   */
  const markedId = outlineRow ?? selectedId;
  const node = useMemo(() => findNode(tree, markedId), [tree, markedId]);
  /*
    **The count is the SIZE OF THE TREE SELECTION** (claude-win2's 1400 file). It used to be 0 or 1 -
    it counted the bodies picked in the viewport, not the rows chosen - so `detailsPage`'s
    `count > 1 -> "many"` branch could never be reached and the pane never said "3 parts".

    A viewport pick still counts when the tree has no selection, because that is how a body is chosen
    without the tree: the two are one selection store seen from two places.
  */
  const selectedCount = useStore((s) => s.selection.ids.length);
  const viewportCount = picked.bodies.length + Object.keys(picked.faces).length > 0 ? 1 : 0;
  const count = selectedCount > 0 ? selectedCount : node === null ? viewportCount : 1;

  /**
   * **A virtual row drives the pane through what it OPENS** (the gui-v3 review's warning 2).
   *
   * A run, a field, a slice and the placeholders are not nodes, so `findNode` returns null and the
   * pane fell back to the stage checklist - measured: selecting a run showed "NEXT STEP" rather than
   * the run. The row itself knows its panel (`openTargetOf`), and that is the same answer a
   * double-click gives, so the two cannot disagree.
   */
  const rowNode = useDetailsRow(node === null ? markedId : null);
  const opened = rowNode !== null ? openTargetOf(rowNode) : node?.type === "solver_settings" ? openTargetOf(node) : null;
  const markedNode = node ?? rowNode;
  // The Solve rows open their own section (`openTargetOf`: Initialisation opens the initial-turbulence fields).
  const solveSection = opened?.section;
  /** The chosen rows, with their types, for the multi-selection page. */
  /*
    **The ids, and the tree, selected SEPARATELY - never a derived array.** A `useStore` selector that
    returns a fresh array makes every render a state change and React throws #185 (infinite render) with a
    blank window. Measured: the whole app rendered nothing after this page was added. The derivation
    happens in `useMemo`, which is what it is for.
  */
  const selectedIds = useStore((s) => s.selection.ids);
  const treeForRows = useStore((s) => s.tree);
  const selectedRows = useMemo(
    () => selectedIds.map((id) => ({ id, type: findNode(treeForRows, id)?.type ?? "" })),
    [selectedIds, treeForRows],
  );
  /** A click on one of them narrows the selection to that row. */
  const onPickOne = useCallback((id: string) => void useStore.getState().select(id), []);
  const selectedTypes = selectedIds.length;
  const mixedTypes = selectedRows.some((r) => r.type !== node?.type);
  const target =
    opened !== null && opened.panel !== null
      ? { kind: "panel" as const, id: opened.panel }
      : // **A MIXED selection has no type**, so it is "many" rather than "3 of whatever the primary is".
        detailsPage(mixedTypes && selectedTypes > 1 ? null : (node?.type ?? null), count);
  const page = target.kind === "panel" ? PANELS.get(target.id) ?? null : null;
  const Page = page?.component ?? null;

  // **The chip's own step**, so the pane and the chip cannot say different things.
  const step = useNextStep();
  // **True when the next step belongs to a different stage than the one on screen**, which is the case
  // the card has to lead differently: the user is looking at Geometry and being told about the mesh.
  const stepElsewhere = stageOfStep(step.id) !== null && stageOfStep(step.id) !== stage;

  return (
    <section className="details-pane glass" aria-label="Details" data-ws="details-pane" data-overlay={overlay || undefined}>
      <header className="details-head">
        {overlay && (
          <button
            type="button"
            className="icon-button no-drag"
            aria-label="Back to the tree"
            title="Back to the tree"
            onClick={() => onBack?.()}
          >
            <ChevronLeft size={14} />
          </button>
        )}
        {/*
          **A proper header, the same chrome as the other cards** (claude-win2's 0530 item 4): the
          heading was a bare `<span>` with no styling, so "Properties" read as a stray label floating
          above the card. It now carries what identifies the thing being shown - its name, its type
          and its path - which is what the tree row would tell you, and the panel title as the
          section label above them.
        */}
        <div className="details-head-text">
          <span className="details-title">
            {page?.title ?? (target.kind === "many" ? "Several selected" : "Next step")}
          </span>
          {stage === "Mesh" && <MeshStepperLine />}
          {markedNode !== null && (
            <span className="details-sub" title={markedNode.path}>
              {markedNode.name}
              <span className="muted"> · {typeWord(markedNode)}</span>
            </span>
          )}
        </div>
        {/*
          **A close affordance only where closing means something.** Beside the tree the pane is part
          of the dock (the collapse button and Ctrl+B own it); as an overlay the back arrow above is
          the way out, and two controls for one action would be one too many.
        */}
      </header>
      <div className="details-body">
        {/* **The stepper is the body's first block in the Mesh stage** (brief M-502 §UI 3): the
           pane's own page follows it, so every Mesh-stage page has the nine tasks above it. The
           one-line form lives in the fixed header below. */}
        {stage === "Mesh" && <MeshStepper foot={!(Page !== null && target.kind === "panel" && target.id === "mesh")} />}
        {/* **The task pages for the two tasks with no tree row of their own** (brief M-502): the
           stepper's click sets the task; any other selection clears it (the store's rule). */}
        {TaskPage !== null && <TaskPage />}
        {/* **The Results page takes the row's section** (`openTargetOf`), so a selected field, slice or
           residual shows its own settings and not the whole panel. */}
        {Page !== null && (target.kind === "panel" && target.id === "results" ? <FieldsPanel section={opened?.section} /> : target.kind === "panel" && target.id === "solve" ? <RunPanel key={solveSection ?? "overview"} section={solveSection} /> : target.kind === "panel" && target.id === "mesh" ? <MeshPanel hideInAdvanced={TaskPage !== null && meshTask !== null ? MESH_TASK_FIELDS[meshTask] : undefined} /> : <Page />)}
        {target.kind === "many" && (
          <ManySelection rows={selectedRows} onPick={onPickOne} />
        )}
        {/*
          **An empty pane is the step card on every stage, Results included.** The Results page is not
          shown here: a page of every field read as a long list when nothing was chosen. The card says
          where the user is and how to reach a field, and a selected row still shows its own settings.
        */}
        {target.kind === "checklist" && TaskPage === null && (
          <div className="details-note" data-ws="details-checklist">
            {step === null ? (
              <p className="flex items-start gap-2" title="Select something in the tree to see its settings." aria-description="Select something in the tree to see its settings.">
                <Info size={13} className="mt-[2px] shrink-0" aria-hidden="true" />
                <span>Select a row in the tree</span>
              </p>
            ) : (
              <>
                {/*
                  **The reason ONCE** (claude-win2's GUI review item 6: *"it shows … twice (a bold line
                  and an identical grey line under it)"*). The first line was the reason and the second
                  was the reason again whenever the step was blocked - so a blocked step said the same
                  sentence twice, and a reader has to work out whether the repetition is a detail.

                  A step that IS available says how to take it; a blocked one has already said why.
                */}
                <p className="section-title">{stage}</p>
                {/*
                  **A step from ANOTHER stage is demoted to a secondary line** (claude-win2's GUI
                  review item 6, second half): under a GEOMETRY heading the card said *"The mesh is out
                  of date: build it again"*, which is the NEXT stage's step taken as this stage's
                  answer. The stage's own purpose leads, and the other stage's step follows it - which
                  is honest about where the user is and what is waiting for them.
                */}
                {/*
                  **A few words, the sentence in the tooltip** (the owner's rule). The step is named by its
                  label ("Generate mesh"); `step.reason` is the sentence. The flow strip on the viewport
                  takes the step (it replaced the top bar's chip), so the tooltip points there.
                */}
                {stepElsewhere ? (
                  <>
                    <Note tip={STAGE_LEAD[stage]}>{STAGE_WORDS[stage]}</Note>
                    <Note tip={step.reason}>Next: {step.label}</Note>
                  </>
                ) : (
                  <Note tip={step.enabled ? `${step.reason} Take it from the flow strip above the view.` : step.reason}>{step.label}</Note>
                )}
                {stage === "Results" && (
                  <Note tip="Select a field, slice, residual or report in the tree to see its settings." data-ws="results-hint">
                    Select a result in the tree
                  </Note>
                )}
              </>
            )}
          </div>
        )}
      </div>
    </section>
  );
}

/**
 * Where the pane belongs, for the layout: beside the tree in a wide window, over it in a narrow one.
 *
 * **The decision's rule as a function**, so the layout does not re-derive it and the test can pin the
 * boundary rather than a screenshot.
 */
export function detailsPlacement(width: number, collapsed: boolean): "beside" | "overlay" | "hidden" {
  if (collapsed) return "hidden";
  return width < NARROW_PX ? "overlay" : "beside";
}
