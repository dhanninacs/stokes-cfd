import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type ReactNode, type UIEvent } from "react";
import { Eye, EyeOff, ListTree, Palette, Pencil, Play, Plus, Search, Sigma, Trash2, Zap, ZapOff } from "lucide-react";
import type { NodeSummary } from "../api/types";
import { findNode, useStore } from "../state/store";
import { newMeshResults, resultsWithPatches, revealIds } from "../state/meshReveal";
import { isNodeHidden } from "../viewport/hiddenNodes";
import { shortcutHint, useShortcut } from "../workspace/keymap";
import { useWorkspace } from "../workspace/store";
import { stageGroups, stageLinked, under } from "../workspace/stageMap";
import { chooseField } from "./fieldsModel";
import { openPanel } from "./panelOpen";
import { folderVerb, type FolderEntry, type FolderVerb } from "./folderVerbs";
import { arrangeTree } from "../workspace/tree";
import { runAction } from "../app/actions";
import { buildOutline, outlineRowsFor, resolveIn, revealChainFor } from "./outlineModel";
import { withFindingValues } from "./outlineStatus";
import { focusOutlineStage } from "./outlineStageFocus";
import { useMeshContext } from "./meshRun";
import { setShowAllRows, useRowMode } from "./outlineMode";
import { useTreeRows } from "./treeRows";
import { enterTargetOf, isVirtualId, openTargetOf, rowMetaOf } from "./stageTreeModel";
import { RENAME_EVENT } from "../app/events";
import { prefersReducedMotion } from "../app/prefs";
import { IconButton, Panel, SectionTitle } from "../components/ui";
import { Menu, type CloseReason, type MenuItemSpec } from "../components/Menu";
import TreeRow, { type MenuOrigin } from "./TreeRow";
import TreeGuide from "./TreeGuide";
import type { StylePatch } from "./StylePopover";
import {
  ROW_HEIGHT,
  ancestorsOf,
  buildRows,
  canAddParameter,
  indexOfRow,
  isStructural,
  neighbourAfterRemoval,
  rowControls,
  rowDomId,
  siblingInfo,
  subtreeIds,
  windowSegments,
  type RowModel,
} from "./nodeRules";
import { FOCUS_TREE_EVENT, treeKeyHandler } from "./treeKeyboard";
import { actionTargetsForContext, bulkTreeMenuActions, isCompleteActionSelection, shouldSelectContextTarget, visibilityChangePlan } from "./treeMenuModel";

// ---------------------------------------------------------------------------------------------- panel
/**
 * Rows are drawn only near the part of the list that is in view (a window, with a gap of the rows'
 * height above and below it, no library): a project of thousands of parts has one DOM row per
 * screen line and not per part. The rows that have to stay whatever the scroll position are
 * drawn as well (see `windowSegments`), so focus, rename and the row menu survive a scroll.
 */
const OVERSCAN = 6;
/** Rows assumed in view until the scroller has been measured, which happens before the first paint. */
const FIRST_VIEW_ROWS = 40;

type MenuState = { kind: "node"; nodeId: string; at: { x: number; y: number } | null } | { kind: "plus" };

export default function TreePanel() {
  const tree = useStore((s) => s.tree);
  const expanded = useStore((s) => s.expanded);
  const filter = useStore((s) => s.treeFilter);
  const setFilter = useStore((s) => s.setTreeFilter);
  const selectedId = useStore((s) => s.selectedId);
  const nodeTypes = useStore((s) => s.nodeTypes);
  // What the stage rows are derived from (T-219 B) - the condition catalogue, the runs, the results
  // and the set-up check - is read by `useTreeRows` now, so this component no longer needs them.
  /** The operation types (`operation.types`): what tells an operation node from another (T-029). */
  const operationTypes = useStore((s) => s.operationTypes);
  // The colour the viewport reports for a file, for the swatch of a part without a `color` parameter.
  const geometry = useStore((s) => s.geometry);
  const select = useStore((s) => s.select);
  const toggleExpanded = useStore((s) => s.toggleExpanded);
  const execute = useStore((s) => s.execute);
  const refresh = useStore((s) => s.refresh);
  const pushToast = useStore((s) => s.pushToast);

  const [menu, setMenu] = useState<MenuState | null>(null);
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  /** The folder verb whose name is being asked for, or null. **One form for every verb**: the
   *  command, the label and the placeholder all come from the table, so materials, regions and
   *  studies share the asking and differ only in what they call. */
  const [addingVerb, setAddingVerb] = useState<FolderEntry | null>(null);
  /** The row chosen among the rows that are not nodes (a placeholder, a run, a field): it is highlighted, `selectedId` stays the node's. */
  // **The store's, not local state** (the gui-v3 review's warning 2): the Details pane beside the
  // tree has to read this, and it cannot read another component's `useState`.
  const virtualSel = useStore((s) => s.outlineRow);
  const setVirtualSel = useStore((s) => s.setOutlineRow);
  /** The first row in view and the number of rows that fit: the window is these plus the overscan. */
  const [scrollRow, setScrollRow] = useState(0);
  const [viewRows, setViewRows] = useState(FIRST_VIEW_ROWS);
  const scrollerRef = useRef<HTMLDivElement>(null);
  const plusRef = useRef<HTMLButtonElement>(null);
  const rowButtonRef = useRef<HTMLElement | null>(null); // the row button that opened the current node menu
  const revealed = useRef<string | null>(null);
  /** A row to scroll to once the rows that open it have been made: the id, and the rows it was asked against. */
  const pendingReveal = useRef<{ id: string; rows: readonly RowModel[] } | null>(null);
  const selectedHint = useRef(0);

  // The tree with every stage's rows in place: the settings that do not exist yet as placeholders, the runs and the results as rows.
  // **One computation of the rows, shared with the Details pane** (`useTreeRows`): the pane has to
  // look up a row that is not a node to know which page to show, and computing the decorations twice
  // would let the two disagree the first time a decoration changed.
  /*
    **A finding BOUND to a node reaches its row as the row's value text** (the missing-finding picture).
    `treeValue` reads `meta.subtitle` first, so a finding's own message - *"`/regions/fluid` has no
    inlet"* - shows on the region's row without a second rendering path.

    **Only an `error`**: a warning is information, not something to do, and the row's value is for what a
    user must act on. A finding that names no node lands nowhere, which is the `solve.check` case.
  */
  const shown = useTreeRows();
  const setupCheck = useStore((s) => s.setup);
  /*
    **The Mesh section's mesher row reads the CATALOGUE, not a hard-coded list** (Codex's 040513): the
    shared `useMeshContext` store already holds `mesh.meshers`, which carries `built` and the user-visible
    `why`. **Only `built` meshers are drawn**, so the outline offers blockMesh and snappyHexMesh and never
    a choice that would fail later. Subscribing costs nothing after the one load the Mesh panel already
    makes.
  */
  const meshers = useMeshContext((s) => s.meshers);
  const builtMeshers = useMemo(
    () => meshers.filter((m) => m.built).map((m) => ({ id: m.id, label: m.label })),
    [meshers],
  );
  const shownWithFindings = useMemo(() => withFindingValues(shown, setupCheck), [shown, setupCheck]);

  const query = filter.trim().toLowerCase();
  /*
    **The outline is the row SOURCE behind `tree.layout`** (G-306): it produces the same `RowModel[]` the
    groups path does, so every behaviour after this line - the row menu, rename, add-verbs, drag, the eye
    and the swatch - is untouched and cannot disagree between the two arrangements.

    **"groups" keeps exactly today's path**, so a user who arranged their own tree is not moved by an
    upgrade, and the comparison below is the only place the two differ.
  */
  const treeLayout = useWorkspace((s) => s.document.tree.layout);
  /** The groups the user hid, so a section with none of its groups left is dropped. */
  const hiddenGroups = useWorkspace((s) => s.document.tree.hidden);
  /** "Show all rows": off draws the Basic tree, without the rows flagged `advanced` (`outlineMode.ts`). */
  const showAllRows = useRowMode((s) => s.showAll);
  const rows = useMemo(
    () =>
      treeLayout === "groups"
        ? buildRows(shownWithFindings, expanded, query)
        : outlineRowsFor(shownWithFindings, expanded, query, builtMeshers, hiddenGroups, showAllRows),
    [shownWithFindings, expanded, query, treeLayout, builtMeshers, hiddenGroups, showAllRows],
  );
  // What every row's swatch and eye show; `rowControls` keeps its answers per node, so only a
  // patch that touched a node derives it again. A `region` or a `boundary` proxies the tag it
  // references (ADR-0022 §9.1), so the resolver looks that tag up in the same tree.
  const tagOf = useCallback((id: string) => findNode(tree, id), [tree]);
  const rowStyles = useMemo(
    () => rows.map((row) => rowControls(row.node, geometry, tagOf)),
    [rows, geometry, tagOf],
  );
  const selectedNode = useMemo(() => findNode(tree, selectedId), [tree, selectedId]);
  // A real selection elsewhere (the viewport, the Conditions panel) ends the choice of a row that is not a node.
  useEffect(() => setVirtualSel(null), [selectedId]);
  const markedId = virtualSel ?? selectedId;
  /**
   * **Every chosen row is drawn as chosen** (claude-win2's 1400 file). `markedId` stays the row with the
   * focus ring - the primary - and this set is what the row's highlight reads, so a Ctrl-click shows the
   * whole selection rather than only the last row touched.
   */
  const selection = useStore((s) => s.selection);
  const chosen = useMemo(() => new Set(selection.ids), [selection.ids]);
  // A mesh that has just been made opens the tree to its patches: `mesh/results` and the result under it
  // arrive folded, and a count with no row to look at reads as a mesh that is not there. A project that opens with
  // its mesh keeps the tree the stage gave it: the first tree only records what is there.
  const meshSeen = useRef<Set<string> | null>(null);
  useEffect(() => {
    if (!tree) return;
    const fresh = newMeshResults(tree, meshSeen.current);
    meshSeen.current = new Set(resultsWithPatches(tree));
    if (fresh.length === 0) return;
    const { expanded: open, stage, toggleExpanded: toggle } = useStore.getState();
    for (const id of revealIds(tree, fresh, stage, open)) toggle(id);
  }, [tree]);
  // Handlers read the row list through a ref so their identity stays stable for the memoised rows.
  const rowsRef = useRef(rows);
  useEffect(() => {
    rowsRef.current = rows;
  }, [rows]);
  const siblings = useMemo(() => siblingInfo(rows), [rows]);

  /** Scrolls the row at `index` into view, the least that does it, and shows the rows around it before the next paint. */
  const scrollToRow = useCallback((index: number) => {
    const scroller = scrollerRef.current;
    if (!scroller) return;
    const top = index * ROW_HEIGHT;
    const bottom = top + ROW_HEIGHT;
    if (top < scroller.scrollTop) scroller.scrollTop = top;
    else if (bottom > scroller.scrollTop + scroller.clientHeight) scroller.scrollTop = bottom - scroller.clientHeight;
    setScrollRow(Math.floor(scroller.scrollTop / ROW_HEIGHT));
  }, []);

  // An arrow key onto a row that is not a node must show it, as a real selection does (its reveal effect is `selectedId`'s).
  useLayoutEffect(() => {
    if (virtualSel === null) return;
    const index = rowsRef.current.findIndex((row) => row.node.id === virtualSel);
    if (index >= 0) scrollToRow(index);
  }, [virtualSel, scrollToRow]);

  // The window follows the scroller: its scroll position and its height (a resize, the drawer, the stage chips).
  useLayoutEffect(() => {
    const scroller = scrollerRef.current;
    if (!scroller) return;
    const measure = () => {
      setViewRows(Math.ceil(scroller.clientHeight / ROW_HEIGHT) + 1);
      setScrollRow(Math.floor(scroller.scrollTop / ROW_HEIGHT));
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(scroller);
    return () => observer.disconnect();
  }, []);
  const onScroll = useCallback((e: UIEvent<HTMLDivElement>) => {
    const row = Math.floor(e.currentTarget.scrollTop / ROW_HEIGHT);
    setScrollRow((before) => (before === row ? before : row));
  }, []);

  const focusTree = useCallback(() => scrollerRef.current?.focus({ preventScroll: true }), []);

  // Esc in a properties field hands focus back here.
  useEffect(() => {
    window.addEventListener(FOCUS_TREE_EVENT, focusTree);
    return () => window.removeEventListener(FOCUS_TREE_EVENT, focusTree);
  }, [focusTree]);

  // The context toolbar's Rename (stokes:rename): edit the name of the node selected for the patch.
  useEffect(() => {
    const onRename = (e: CustomEvent<{ patch: string; nodeId: string | null }>) => {
      const node = findNode(useStore.getState().tree, e.detail.nodeId);
      if (!node) pushToast("info", `${e.detail.patch} has no node in the tree to rename.`);
      else if (isStructural(node)) pushToast("info", `${node.name} is part of the project skeleton and cannot be renamed.`);
      else setRenamingId(node.id);
    };
    window.addEventListener(RENAME_EVENT, onRename);
    return () => window.removeEventListener(RENAME_EVENT, onRename);
  }, [pushToast]);

  // Selecting from elsewhere (a patch in the viewport, a created node) must show the row: open its
  // ancestors once per selection, then scroll it into view. Selecting the same node again counts as a
  // new selection (revealRequest), which brings its row back after a stage folded the group it is in.
  // Manual collapses in between, and tree refreshes, are left alone.
  // The row is scrolled to by its place in the list, not found in the DOM: it may not be drawn yet.
  // Layout effects, so that the window moves before the next paint and the row is never seen missing.
  const revealRequest = useStore((s) => s.revealRequest);
  useLayoutEffect(() => {
    const key = `${selectedId}#${revealRequest}`;
    if (!selectedId || revealed.current === key) return;
    /*
      **The outline's ancestors are VIRTUAL, so `ancestorsOf` cannot find them** (Codex's 015013: *"selecting
      an already-visible leaf is not external-selection reveal"*). Measured: selecting a mesh patch from
      the API expanded five REAL group ids and revealed **nothing** - the outline still showed six closed
      sections. In outline mode the chain comes from the projection, which knows which virtual rows hold
      the node.
    */
    const outlineChain =
      treeLayout === "groups"
        ? null
        : revealChainFor(
            buildOutline(tree, resolveIn(tree), [], showAllRows),
            selectedId,
            (id) => findNode(tree, id),
          );
    const chain = outlineChain !== null ? outlineChain.map((id) => ({ id })) : ancestorsOf(tree, selectedId);
    if (chain === null) return; // the node is not in the tree yet; this effect runs again when it arrives
    if (outlineChain !== null && outlineChain.length === 0) return; // the outline does not show it
    revealed.current = key;
    const { expanded: open, toggleExpanded: toggle } = useStore.getState();
    let opened = false;
    for (const ancestor of chain) {
      if (open[ancestor.id]) continue;
      toggle(ancestor.id);
      opened = true;
    }
    if (opened) {
      pendingReveal.current = { id: selectedId, rows }; // the rows that show it come with the next render
    } else {
      const index = indexOfRow(rows, selectedId, selectedHint.current);
      if (index >= 0) scrollToRow(index);
    }
  }, [selectedId, tree, revealRequest, rows, scrollToRow, showAllRows]);
  useLayoutEffect(() => {
    const pending = pendingReveal.current;
    if (pending === null || pending.rows === rows) return; // not the render that opened the rows yet
    pendingReveal.current = null;
    const index = rows.findIndex((row) => row.node.id === pending.id);
    if (index >= 0) scrollToRow(index);
  }, [rows, scrollToRow]);
  useEffect(() => {
    if (!selectedId) revealed.current = null;
  }, [selectedId]);

  // A stage chip or a stage key opened the stage's groups and folded the rest (the store did that in
  // the same update, so the rows are already laid out): bring the stage's first group to the top.
  // stageFocus is zero until somebody chooses a stage, which keeps the load as it was.
  // The groups come from the workspace (tree.stages, in the tree's display order); with linkStage off
  // the stage and the tree do not move each other.
  const stageFocus = useStore((s) => s.stageFocus);
  useEffect(() => {
    if (stageFocus === 0 || !stageLinked()) return;
    const { stage, tree: current } = useStore.getState();
    const { groupOrder: order, hidden } = useWorkspace.getState().document.tree;
    let target: string | null;
    if (treeLayout === "outline") {
      // Outline sections have virtual ids; opening real group ids cannot reveal them.
      const projection = buildOutline(shownWithFindings, resolveIn(shownWithFindings), hidden, showAllRows);
      const focused = focusOutlineStage(projection, stage, useStore.getState().expanded);
      target = focused.target;
      if (target !== null) useStore.setState({ expanded: focused.expanded });
    } else {
      const groups = stageGroups(stage);
      target = arrangeTree(current, order, hidden)?.children?.find((top) => groups.some((g) => under(g, top.path)))?.id ?? null;
    }
    if (target === null) return;
    const frame = requestAnimationFrame(() => {
      const scroller = scrollerRef.current;
      const index = rowsRef.current.findIndex((row) => row.node.id === target);
      if (!scroller || index < 0) return; // filtered out of the list
      scroller.scrollTo({ top: Math.max(0, index * ROW_HEIGHT - 4), behavior: prefersReducedMotion() ? "auto" : "smooth" });
    });
    return () => cancelAnimationFrame(frame);
  }, [stageFocus, treeLayout]);

  // ------------------------------------------------------------------ the stage rows
  /** Brings the panel of a row forward; a field row also chooses that field (and shows the results). */
  const openNode = useCallback((node: NodeSummary) => {
    const meta = rowMetaOf(node);
    if (meta?.row === "field" && meta.ref !== undefined) {
      const ref = meta.ref;
      useStore.setState((s) => ({ results: chooseField(s.results, ref) }));
      if (useStore.getState().stage !== "Results") useStore.getState().setStage("Results");
    }
    const target = openTargetOf(node);
    if (target !== null) openPanel(target);
  }, []);
  /** Chooses a row. `activate` is a click: a row that is not a node also opens its panel. Arrow keys only move the mark. */
  const chooseRow = useCallback(
    (id: string, activate: boolean) => {
      if (!isVirtualId(id)) {
        setVirtualSel(null);
        select(id);
        return;
      }
      setVirtualSel(id);
      if (activate) {
        const row = rowsRef.current.find((r) => r.node.id === id);
        if (row) openNode(row.node);
      }
    },
    [select, openNode],
  );
  const clickRow = useCallback((id: string) => chooseRow(id, true), [chooseRow]);
  /**
   * A click WITH its modifiers (claude-win2's 1400 file). The visible order comes from `rows`, so a Shift
   * range covers only rows the user can see.
   */
  const clickRowWith = useCallback(
    (id: string, gesture: { ctrl?: boolean; shift?: boolean }) => {
      if (isVirtualId(id)) {
        // A row that is not a node - a stage folder, a count - has no place in a selection of nodes.
        chooseRow(id, true);
        return;
      }
      setVirtualSel(null);
      const visible = rowsRef.current.map((r) => r.node.id).filter((x) => !isVirtualId(x));
      useStore.getState().selectRow(id, gesture, visible);
    },
    [chooseRow],
  );
  const selectContextTarget = useCallback(
    (id: string) => {
      const state = useStore.getState();
      if (shouldSelectContextTarget(id, state.selection.ids, state.selectedId)) clickRowWith(id, {});
    },
    [clickRowWith],
  );
  const moveToRow = useCallback((id: string) => chooseRow(id, false), [chooseRow]);

  // ------------------------------------------------------------------ actions

  const addParameter = useCallback(
    async (name: string) => {
      try {
        const created = (await execute("parameter.define", { name, dimension: "dimensionless", value: "0" })) as { id: string };
        await refresh();
        select(created.id);
        setAdding(false);
        focusTree();
      } catch {
        /* reported by execute; the form stays open so the name can be fixed */
      }
    },
    [execute, refresh, select, focusTree],
  );

  const undoDelete = useCallback(
    async (id: string) => {
      try {
        // The undo stack is linear: only undo when the delete is still its latest entry.
        const state = (await execute("history.state", {}, { silent: true })) as { undo_kind: string | null };
        if (state.undo_kind !== "remove_node") {
          const undo = shortcutHint("history.undo");
          pushToast("info", `Other changes were made since. ${undo ? `Use ${undo} to step back` : "Undo steps back"} through them.`);
          return;
        }
        await execute("history.undo");
        await refresh();
        select(id);
        focusTree();
      } catch {
        /* reported by execute */
      }
    },
    [execute, refresh, select, pushToast, focusTree],
  );

  const removeNode = useCallback(
    async (node: NodeSummary) => {
      if (isStructural(node)) {
        pushToast("info", `${node.name} is part of the project skeleton and cannot be deleted.`);
        return;
      }
      const index = rowsRef.current.findIndex((r) => r.node.id === node.id);
      const neighbour = index >= 0 ? neighbourAfterRemoval(rowsRef.current, index) : null;
      const doomed = new Set(subtreeIds(node));
      try {
        await execute("node.remove", { node: node.id });
      } catch {
        return; // reported by execute
      }
      const current = useStore.getState().selectedId;
      if (current && doomed.has(current)) select(neighbour);
      setRenamingId((r) => (r && doomed.has(r) ? null : r));
      pushToast("info", `Deleted ${node.name}`, { label: "Undo", run: () => void undoDelete(node.id) });
      focusTree(); // the row that had the focus is gone
    },
    [execute, select, pushToast, undoDelete, focusTree],
  );

  const renameNode = useCallback(
    async (node: NodeSummary, name: string, refocus: boolean) => {
      try {
        await execute("node.rename", { node: node.id, name });
      } catch {
        return false; // reported by execute
      }
      setRenamingId(null);
      if (refocus) focusTree();
      return true;
    },
    [execute, focusTree],
  );

  // The eye and the style card write through one command (ADR-0021 4.3): one call per gesture, so
  // one undo step, and the answer comes back as a `node.changed` patch like any other change.
  const toggleHiddenNode = useStore((s) => s.toggleHiddenNode);
  const hiddenNodes = useStore((s) => s.hiddenNodes);

  /**
   * Leave a body out of the simulation, or put it back (the workflow-v2 plan §2, T-221).
   *
   * **Through `geometry.simulate`, which is the one place the flag changes** - so the tree's toggle,
   * the row menu and the viewport menu cannot disagree about what the switch does, and the change is
   * one undo step and one journal record.
   *
   * A group is left out with its parts: the command resolves one to the parts beneath it, so the
   * toggle on a group would be a second implementation of the same rule.
   */
  const toggleSimulate = useCallback(
    async (node: NodeSummary) => {
      try {
        await execute("geometry.simulate", { nodes: [node.id], simulate: node.params?.simulate === false });
        await refresh();
      } catch {
        /* execute logged the error and showed a toast */
      }
    },
    [execute, refresh],
  );

/**
 * Whether a group has any part still in the simulation.
 *
 * **Any, not all**: the menu entry says "Leave out" when there is something to leave out, which is the
 * reading a user expects of a mixed group. "All" would offer "Use in simulation" for a group with one
 * body already in it, and the click would then change only that one - a label describing an action
 * that did not happen.
 */
function partsSimulate(group: NodeSummary): boolean {
  const walk = (n: NodeSummary): boolean => {
    if (n.type === "part") return n.params?.simulate !== false;
    return (n.children ?? []).some(walk);
  };
  return (group.children ?? []).some(walk);
}

  const styleNode = useCallback(
    async (id: string, patch: StylePatch) => {
      try {
        await execute("geometry.style", { nodes: [id], ...patch });
      } catch {
        /* execute logged the error and showed a toast */
      }
    },
    [execute],
  );

  /**
   * Opens the style card of a row. The card belongs to the row (it hangs from the row's own
   * swatch and closes with it), so the menu reaches it through the swatch of its row.
   */
  const openStyleCard = useCallback((id: string) => {
    document.getElementById(rowDomId(id))?.querySelector<HTMLElement>("[data-style-swatch]")?.click();
  }, []);

  const openNodeMenu = useCallback((node: NodeSummary, from: MenuOrigin, toggle: boolean) => {
    const at = from instanceof HTMLElement ? null : from;
    if (from instanceof HTMLElement) rowButtonRef.current = from;
    setMenu((m) => (toggle && m?.kind === "node" && m.nodeId === node.id ? null : { kind: "node", nodeId: node.id, at }));
  }, []);
  const closeMenu = useCallback(
    (reason: CloseReason) => {
      setMenu(null);
      if (reason !== "outside") focusTree(); // an action that wants the focus (rename, add) takes it after this
    },
    [focusTree],
  );
  const cancelRename = useCallback(
    (refocus: boolean) => {
      setRenamingId(null);
      if (refocus) focusTree();
    },
    [focusTree],
  );

  // Running an operation (T-029): a stale one — a parameter, an input or a tag it reads changed, or
  // its run was cancelled — runs again from its row, as the amber dot says it should.
  const runOperation = useCallback(
    async (node: NodeSummary) => {
      try {
        await execute("operation.run", { op: node.id });
      } catch {
        /* reported by execute */
      }
    },
    [execute],
  );

  // ------------------------------------------------------------------ menus
  const renameKeys = useShortcut("tree.rename").hint;
  const deleteKeys = useShortcut("tree.delete").hint;
  /**
   * Ask for a name and create a folder's item through its command.
   *
   * **The name is asked rather than invented**: a material called `material_2` is a thing a user has
   * to rename before it means anything, and the commands take a name for exactly that reason.
   */
  /**
   * Run a folder's Add verb with what the user gave it.
   *
   * **Three kinds of answer, one entry point** (review item 2): a name for a material, region or
   * study; a PATH for an import; a SHAPE for a primitive. The verb says which it wants, so this does
   * not have to know one folder from another - and a fourth kind would be one case here rather than a
   * second prompt.
   *
   * **An import's answer is a path, so the file picker supplies it** and no name field is shown: the
   * import names the part from the file, which is what a user expects it to be called.
   */
  const addToFolder = useCallback(
    // **A command verb**, not an entry: a selection verb runs an ACTION and never reaches here, so
    // typing the parameter this way lets the body use `verb.command` without a check that cannot fail.
    async (verb: FolderVerb, answer: string) => {
      try {
        if (verb.asks === "file") await execute(verb.command, { path: answer, ...(verb.params ?? {}) });
        else if (verb.asks === "shape")
          await execute(verb.command, { type: "primitive", params: { shape: answer }, ...(verb.params ?? {}) });
        else await execute(verb.command, { name: answer, ...(verb.params ?? {}) });
        await refresh();
        setAddingVerb(null);
      } catch {
        /* execute logged the error and showed a toast */
      }
    },
    [execute, refresh],
  );

  /**
   * Ask for what the verb needs: a name in a field, a shapes menu, or the host's file picker.
   *
   * **The picker is the same one the Import action uses** (`useImport`), so a file chosen here goes
   * through the staging path a large CAD file needs - a `<input type=file>` in the tree would be a
   * second upload route, and the one that skipped staging would fail on the files that matter.
   */
  const askAdd = useCallback(
    (verb: FolderEntry) => {
      // **A selection verb runs an ACTION**, not a command: "Add tag from selection…" names the
      // picked faces, and `selection.name` already opens the naming card the viewport toolbar uses.
      // `"action" in verb` is the narrowing TypeScript follows reliably: the union's other side has no
      // `action` property at all, so this is a discriminant rather than a value check.
      if ("action" in verb && verb.action !== undefined) {
        runAction(verb.action);
        return;
      }
      if (verb.asks === "file") {
        // **Announced, not re-implemented**: the import flow (picker, browser fallback, units
        // question, staging for a large file) lives in `ProjectMenu`, and a second picker here would
        // be a second route that skips it.
        document.dispatchEvent(new CustomEvent("stokes:import-geometry"));
        return;
      }
      setAddingVerb(verb);
    },
    [],
  );

  /**
   * **The Details pane's Add button, heard here.**
   *
   * The folder card lives in another component and the name prompt lives in this one, so the card
   * announces what was asked for rather than owning a second copy of the form. A custom event keeps
   * the two apart without a new store field, and the verb comes from `folderVerbs` on this side too -
   * so the button and the menu entry cannot name different commands.
   */
  useEffect(() => {
    const heard = (e: Event) => {
      const path = (e as CustomEvent<string>).detail;
      const verb = folderVerb(path);
      // **Through `askAdd`, not straight to the form.** Setting `addingVerb` here opened a NAME field
      // for every verb, so the folder card's "Import geometry…" would have asked for a name and then
      // called the import with it - measured while adding the third verb kind. `askAdd` is the one
      // place that knows a verb may want a file, a shape or an action instead.
      if (verb !== null) askAdd(verb);
    };
    document.addEventListener("stokes:folder-add", heard);
    return () => document.removeEventListener("stokes:folder-add", heard);
  }, [askAdd]);

  /**
   * Whether the "+" has anything to offer for the current selection.
   *
   * **The same two questions the menu asks**, so the button and the menu cannot disagree: does the
   * folder have a verb, and is this where a parameter lives.
   */
  const addable = useMemo(() => {
    const target = selectedNode;
    if (target === null) return false;
    return folderVerb(target.path) !== null || canAddParameter(target);
  }, [selectedNode]);

  const menuItems = useMemo((): MenuItemSpec[] => {
    if (!menu) return [];
    if (menu.kind === "plus") {
      /*
        **"+" means "add inside the selected folder"** (claude-win2's package-two review item 3): it
        offered only "Parameter" whatever was selected, so on `materials` it could not add a material
        and on `regions` not a region. It now reads the same `folderVerbs` table the row menu and the
        Details pane read, so the three cannot name different commands.

        **No "Add group"** (his 0200 item 1, the tree guide's finding 1): a group is a folder no
        command can put anything into - there is no drag and no `node.move` - so the entry created
        clutter that was saved, and under `/regions` it reads as a LEGACY region. Existing group nodes
        are kept and `node.add type=group` stays in the API.

        **And when the selection has nothing to add, the menu does not open**: the caller checks
        `plusHasSomething` first, so an empty menu is refused rather than shown (rule 2).
      */
      const target = selectedNode;
      if (target === null) return [];
      const entries: MenuItemSpec[] = [];
      const verb = folderVerb(target.path);
      if (verb !== null) {
        entries.push({
          id: "folder-add",
          label: verb.label,
          icon: <Plus size={14} />,
          onSelect: () => askAdd(verb),
        });
      }
      // A parameter lives in one folder only, which the predicate knows rather than this guessing.
      if (canAddParameter(target)) {
        entries.push({
          id: "parameter",
          label: "New parameter…",
          icon: <Sigma size={14} />,
          onSelect: () => setAdding(true),
        });
      }
      return entries;
    }
    /*
      **The menu reads the node the ROW shows, falling back to the raw tree.** (Codex's 042012: the
      remaining acceptance item is the row and virtual-row menus on outline rows.)

      **Measured**: every outline row offered an "Actions for …" button and the menu closed itself with no
      entries - `findNode(tree, menu.nodeId)` returns `null` for `virtual:outline:…`. An "outline" row is
      a section, a page or a reference, none of which is a persisted node, so looking it up in the raw tree
      could never succeed.

      Where an outline row DOES carry a real node (a boundary under Boundary conditions), that node is the
      one the row shows and the one its menu must act on - so it wins over the lookup.
    */
    const node = rowsRef.current.find((r) => r.node.id === menu.nodeId)?.node ?? findNode(tree, menu.nodeId);
    if (!node) return [];
    /*
      **Rename and Delete need a NODE the server can name.** A virtual row - a section, a page, a reference
      - is not one: measured, every one of them offered "Rename" and "Delete", and either would target an
      id no command accepts. Marking them "fixed" is **not** right either, because a page is not a group of
      the project. They simply get no entry that would be refused (rule 2).
    */
    const virtualRow = node.id.startsWith("virtual:") || !node.path.startsWith("/");
    const idsForActions = actionTargetsForContext(node.id, selection.ids);
    const resolvedTargets = idsForActions.map((id) => findNode(tree, id)).filter((target): target is NodeSummary => target !== null);
    const multiSelection = idsForActions.length > 1;
    const completeSelection = isCompleteActionSelection(idsForActions, resolvedTargets.map((target) => target.id));
    const actionNodes = multiSelection && completeSelection ? resolvedTargets : [node];
    /** **A real, fixed group of the project**: the heading and the refusal of Rename/Delete both read it. */
    const structural = isStructural(node);
    /** Whether Rename and Delete are offered - false for a fixed group **and** for a virtual row. */
    const fixed = structural || virtualRow;
    const items: MenuItemSpec[] = [];
    if (multiSelection) {
      const selectedIds = actionNodes.map((target) => target.id);
      const actions = completeSelection
        ? bulkTreeMenuActions(
            selectedIds,
            actionNodes.every((target) => (target.type === "part" || target.type === "tag") && rowControls(target, geometry) !== null),
            actionNodes.every((target) => target.type === "part"),
          )
        : [];
      items.push({ type: "heading", id: "selection", label: `Actions for ${idsForActions.length} selected items` });
      for (const action of actions) {
        items.push({
          id: action.id,
          label: action.label,
          icon: action.id.startsWith("show") ? <Eye size={14} /> : action.id.startsWith("hide") ? <EyeOff size={14} /> : action.id.startsWith("simulate") ? <Zap size={14} /> : <ZapOff size={14} />,
          onSelect: () => {
            if (action.kind === "visibility") {
                const current = useStore.getState();
                const visibility = Object.fromEntries(
                  action.nodeIds.flatMap((id) => {
                    const target = findNode(current.tree, id);
                    return target ? [[id, target.params?.visible !== false] as const] : [];
                  }),
                );
                const plan = visibilityChangePlan(action.nodeIds, action.value, current.hiddenNodes, visibility);
                for (const id of [...plan.hideInSession, ...plan.revealInSession]) toggleHiddenNode(id);
                if (plan.restoreModelVisibility.length > 0) {
                  void execute("geometry.style", { nodes: plan.restoreModelVisibility, visible: true }).then(() => refresh()).catch(() => {
                    /* execute logged the error and showed a toast */
                  });
                }
            } else {
              void execute("geometry.simulate", { nodes: [...action.nodeIds], simulate: action.value }).then(() => refresh()).catch(() => {
                /* execute logged the error and showed a toast */
              });
            }
          },
        });
      }
      if (actions.length > 0) items.push({ type: "separator", id: "selection-sep" });
    }
    // **The heading only for a real fixed group.** A page is not one, and saying so is worse than nothing.
    if (structural) items.push({ type: "heading", id: "fixed", label: "Fixed group of the project" });
    // **The folder's own Add verb, first** (claude-win2's 0200 item 3, the guide's finding 3): a
    // folder that looks like a container must be able to create the thing it is named for. The table
    // is `folderVerbs`, so materials, regions and studies are one row each and the viewport menus
    // that already create two of those cannot drift from it. **A folder with no verb gets no entry**
    // rather than a disabled one - `/scenes` has no command at all, and `/runs` is made by pressing
    // Run.
    const verb = folderVerb(node.path);
    if (verb !== null) {
      items.push({ id: "folder-add", label: multiSelection ? `${verb.label} in ${node.name}` : verb.label, icon: <Plus size={14} />, onSelect: () => askAdd(verb) });
      items.push({ type: "separator", id: "add-sep" } as MenuItemSpec);
    }
    // A stale operation runs again from its row (T-029); the tree dot is amber already.
    if (node.status === "stale" && node.type in operationTypes) {
      items.push({ id: "run", label: multiSelection ? `Run ${node.name}` : "Run", icon: <Play size={14} />, onSelect: () => void runOperation(node) });
      items.push({ type: "separator", id: "run-sep" } as MenuItemSpec);
    }
    // **Rename and Delete are ABSENT on a fixed node, not disabled** (claude-win2's package-two
    // review, item 1, restating rule 2): "a menu entry that will be refused must not show". A greyed
    // row still asks a user to work out why, and the heading above already says this is a fixed
    // group - so the entries would only repeat it in the form of a refusal.
    if (!fixed && !multiSelection) {
      items.push({ id: "rename", label: "Rename", icon: <Pencil size={14} />, hint: renameKeys, onSelect: () => setRenamingId(node.id) });
    }
    // The eye and the swatch of the row, as menu entries, so a keyboard reaches both (ADR-0021 4.3).
    const controls = rowControls(node, geometry);
    // **Both sources, through the tested rule** rather than an inline `&&`: the session filter and
    // the model's flag, the second because `geometry.style` still writes it and a project saved with
    // a part hidden must draw it hidden.
    const shown = controls === null || !isNodeHidden(node.id, hiddenNodes, controls.visible);
    if (controls && !multiSelection) {
      items.push({
        id: "visibility",
        // **The label and icon read the SESSION filter**, which is the thing this entry changes -
        // not the model's `visible`, which it no longer touches. Reading the model here is how the
        // menu came to disagree with the state it had just set.
        label: shown ? "Hide" : "Show",
        icon: shown ? <EyeOff size={14} /> : <Eye size={14} />,
        // **A VIEW filter, not a model change** (claude-win2's 2000 item 2): the same label and the
        // same icon as the viewport menu and the toolbar, and the same session-only state they use.
        // It used to write the model's `visible` through `geometry.style`, so one word meant two
        // things - saved and undoable here, transient there.
        onSelect: () => toggleHiddenNode(node.id),
      });
      if (controls.styled && !multiSelection) items.push({ id: "style", label: "Colour and opacity…", icon: <Palette size={14} />, onSelect: () => openStyleCard(node.id) });
    }
    /*
      **The row menu's own entry** (the workflow-v2 plan §2, T-221 names three entry points, and this
      is the second). It sits beside Hide because a user looking for "make this body not be there"
      finds both, and the two labels say which is which: Hide is the view, this is the simulation.

      **Only a part and a group of parts**: a tag is not a body a mesh can leave out, and offering the
      entry greyed would invite a click that cannot work. A group takes its parts with it - the command
      resolves one to the parts beneath it - so the entry on a group is the same sentence about an
      assembly.
    */
    // **`/geometry/parts` itself is the "all the bodies" folder**, so leaving it out leaves out every
    // body - which is the reading a user has of that row. `partsSimulate` decides the label from
    // whether anything beneath it is still in.
    if (!multiSelection && (node.type === "part" || node.path === "/geometry/parts")) {
      const inside = node.type === "part" ? node.params?.simulate !== false : partsSimulate(node);
      items.push({
        id: "simulate",
        label: inside ? "Leave out of simulation" : "Use in simulation",
        icon: inside ? <ZapOff size={14} /> : <Zap size={14} />,
        onSelect: () => void toggleSimulate(node),
      });
    }
    if (canAddParameter(node)) items.push({ id: "add-parameter", label: multiSelection ? `Add parameter to ${node.name}` : "Add parameter", icon: <Sigma size={14} />, onSelect: () => setAdding(true) });
    // **The row's own "Add group" is gone too** - the same finding, the same reason, so the two
    // entry points cannot disagree about whether the entry exists.
    if (!fixed) {
      items.push(
        { type: "separator", id: "sep" },
        { id: "delete", label: multiSelection ? `Delete ${node.name}` : "Delete", icon: <Trash2 size={14} style={{ color: "var(--danger)" }} />, hint: deleteKeys, onSelect: () => void removeNode(node) },
      );
    }
    return items;
  }, [menu, tree, selection.ids, selectedNode, nodeTypes, operationTypes, geometry, execute, refresh, removeNode, runOperation, styleNode, openStyleCard, renameKeys, deleteKeys, toggleSimulate, toggleHiddenNode]);

  // A menu for a node that no longer exists closes itself.
  useEffect(() => {
    if (menu && menuItems.length === 0) setMenu(null);
  }, [menu, menuItems]);

  const keys = treeKeyHandler({
    rows,
    selectedId: markedId,
    select: moveToRow,
    startRename: (node) => setRenamingId(node.id),
    remove: (node) => void removeNode(node),
    openMenu: (node, at) => {
      selectContextTarget(node.id);
      openNodeMenu(node, at, false);
    },
  });

  // Enter on a row that has a panel of its own (a placeholder, a run, the mesh or solver settings) opens it; the rest is the tree's.
  const onKeyDown = (e: ReactKeyboardEvent<HTMLElement>) => {
    if (e.key === "Enter" && !e.ctrlKey && !e.metaKey && !e.altKey && (e.target as HTMLElement).tagName !== "INPUT") {
      const row = rowsRef.current.find((r) => r.node.id === markedId);
      if (row && enterTargetOf(row.node) !== null) {
        e.preventDefault();
        openNode(row.node);
        return;
      }
    }
    keys(e);
  };

  const onFilterKey = (e: ReactKeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Escape" && filter) {
      e.preventDefault();
      setFilter("");
    } else if (e.key === "ArrowDown" && rows.length > 0) {
      e.preventDefault();
      // Start from the first row that matches itself, not from the ancestors shown to give it context.
      if (!markedId || !rows.some((r) => r.node.id === markedId)) moveToRow((rows.find((r) => r.node.name.toLowerCase().includes(query)) ?? rows[0]).node.id);
      focusTree();
    }
  };

  const loading = !tree;
  const nodeMenu = menu?.kind === "node" ? menu : null;

  // The rows to draw: the window, and the rows that stay drawn wherever they are.
  const total = rows.length;
  const first = Math.min(scrollRow, Math.max(0, total - 1));
  const selectedIndex = indexOfRow(rows, markedId, selectedHint.current);
  selectedHint.current = Math.max(0, selectedIndex);
  const segments = windowSegments(total, first - OVERSCAN, first + viewRows + OVERSCAN, [
    selectedIndex,
    indexOfRow(rows, renamingId, 0),
    indexOfRow(rows, nodeMenu?.nodeId ?? null, 0),
  ]);
  const items: ReactNode[] = [];
  let cursor = 0;
  const gap = (until: number) => {
    if (until > cursor) items.push(<div key={`gap-${cursor}`} role="presentation" aria-hidden="true" style={{ height: (until - cursor) * ROW_HEIGHT }} />);
  };
  for (const [from, to] of segments) {
    gap(from);
    for (let i = from; i < to; i++) {
      const model = rows[i];
      items.push(
        <TreeRow
          key={model.node.id}
          model={model}
          /*
            **The marked row is drawn as selected too**, not only the multi-selection. Measured: an
            external `select()` (a viewport pick, the API) sets `selectedId` and leaves `selection.ids`
            empty - `select` fills that list only for a PART's picked bodies - so a node selected from
            outside the tree scrolled into view **unmarked**, while the same row clicked in the tree showed
            `selected`. The row a user is looking at should not depend on WHICH gesture selected it.
          */
          selected={chosen.has(model.node.id) || model.node.id === selectedId}
          primary={model.node.id === markedId}
          renaming={model.node.id === renamingId}
          menuOpen={nodeMenu?.nodeId === model.node.id}
          posInSet={siblings.position[i]}
          setSize={siblings.size[i]}
          controls={rowStyles[i]}
          onSelect={clickRow}
          onSelectWith={clickRowWith}
          onOpen={openNode}
          onToggle={toggleExpanded}
          onStyle={styleNode}
          onToggleVisible={toggleHiddenNode}
          onToggleSimulate={toggleSimulate}
          onContextSelect={selectContextTarget}
          onMenu={openNodeMenu}
          onRename={renameNode}
          onCancelRename={cancelRename}
        />,
      );
    }
    cursor = to;
  }
  gap(total);
  // Right-click and keyboard menus hang from a point; button menus from their button.
  const anchor = menu?.kind === "plus" ? plusRef : nodeMenu?.at ? undefined : rowButtonRef;
  return (
    <Panel className="flex flex-col overflow-hidden">
      <SectionTitle
        right={
          /*
            **The "+" hides when the selection has nothing to add** (claude-win2's rule 2, applied to
            a button rather than a menu entry): a control that opens an empty menu is the same failure
            as an entry that refuses. Its tooltip says why, because a button that vanishes with no
            explanation is its own puzzle - and "select something that holds items" is the answer.
          */
          <div className="flex items-center gap-1">
            {treeLayout === "outline" && (
              <IconButton
                label="Show all rows"
                hint="Off: only the rows a first run needs"
                className={`!h-6 !w-6 ${showAllRows ? "active" : ""}`}
                aria-pressed={showAllRows}
                onClick={() => setShowAllRows(!showAllRows)}
              >
                <ListTree size={14} />
              </IconButton>
            )}
            {addable ? (
            <IconButton
              ref={plusRef}
              label="Add inside the selected folder"
              className={`!h-6 !w-6 ${menu?.kind === "plus" ? "active" : ""}`}
              aria-haspopup="menu"
              aria-expanded={menu?.kind === "plus"}
              onClick={() => setMenu((m) => (m?.kind === "plus" ? null : { kind: "plus" }))}
            >
              <Plus size={14} />
            </IconButton>
          ) : (
            <IconButton
              label="Select a folder that can hold something to add to it"
              className="!h-6 !w-6"
              disabled
            >
              <Plus size={14} />
            </IconButton>
            )}
          </div>
        }
      >
        Simulation
      </SectionTitle>
      <div className="px-3 pb-2">
        <div className="relative">
          <Search size={13} className="pointer-events-none absolute top-1/2 left-2.5 -translate-y-1/2 text-[var(--text-faint)]" />
          <input
            className="field !h-8 !pl-8 text-[12.5px]"
            placeholder="Filter the tree"
            aria-label="Filter the tree"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            onKeyDown={onFilterKey}
          />
        </div>
      </div>
      {(adding || addingVerb !== null) && (
        <form
          className="mx-3 mb-2 flex gap-1"
          onSubmit={(e) => {
            e.preventDefault();
            const name = String(new FormData(e.currentTarget).get("name") ?? "").trim();
            if (!name) return;
            // The form only opens for a verb that ASKS FOR A NAME, so `"action" in` narrows it
            // away here - a selection verb never reaches the form.
            if (addingVerb !== null && !("action" in addingVerb)) void addToFolder(addingVerb, name);
            else void addParameter(name);
          }}
        >
          <input
            name="name"
            className="field !h-8 text-[12.5px]"
            placeholder={addingVerb === null ? "new parameter name" : `new ${addingVerb.placeholder}`}
            aria-label={addingVerb === null ? "New parameter name" : `New ${addingVerb.placeholder} name`}
            autoComplete="off"
            spellCheck={false}
            autoFocus
            onKeyDown={(e) => {
              if (e.key === "Escape") {
                e.preventDefault();
                setAdding(false);
                setAddingVerb(null);
                focusTree();
              }
            }}
          />
          <button type="submit" className="btn !h-8">
            Add
          </button>
        </form>
      )}
      <div
        ref={scrollerRef}
        role="tree"
        aria-label="Simulation tree"
        aria-activedescendant={markedId && selectedIndex >= 0 ? rowDomId(markedId) : undefined}
        tabIndex={0}
        // The selected row carries the focus indication (see the Row class); the global ring would frame the whole list.
        // Scroll anchoring off: the gaps above and below the window change as it moves, and the browser must not compensate.
        style={{ outline: "none", boxShadow: "none", overflowAnchor: "none" }}
        /* **Its own scroll, contained** (1430): the tree scrolls inside its card and a wheel at the end
           does not chain to the pane or zoom the viewport. */
        className="group/tree min-h-0 flex-1 overflow-y-auto overscroll-contain px-2 pb-2"
        onKeyDown={onKeyDown}
        onScroll={onScroll}
      >
        {items}
        {loading && <div className="muted px-2 py-6 text-center">Connecting to the server…</div>}
        {!loading && rows.length === 0 && query && <div className="muted px-2 py-6 text-center">Nothing matches “{filter.trim()}”.</div>}
      </div>
      <TreeGuide />
      <Menu
        open={menu !== null && menuItems.length > 0}
        anchor={anchor}
        at={nodeMenu?.at ?? null}
        returnFocus={scrollerRef}
        onClose={closeMenu}
        items={menuItems}
        label={menu?.kind === "plus" ? "Add to the tree" : "Node actions"}
        placement={nodeMenu?.at ? "bottom-start" : "bottom-end"}
      />
    </Panel>
  );
}
