import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { ChevronDown, Expand, Eye, EyeOff, Layers, Pencil, PenLine, Search, Tag, Wrench, X } from "lucide-react";
import { StokesApiError } from "../api/client";
import type { TagRegion } from "../api/types";
import { activeFacePart, describePick, findByPath, findNode, patchForSource, pickAsMembers, pickFromRegion, useStore } from "../state/store";
import { pendingConversionText } from "../state/pick";
import { Button } from "../components/ui";
import { Tooltip } from "../components/Tooltip";
import { PromptDialog } from "../components/PromptDialog";
import { assignBoundary, boundaryPath } from "../panels/assignBoundary";
import { useAssignMenu } from "../panels/AssignMenu";
import { useRelatedMenu } from "../panels/RelatedMenu";
import { useMaterialMenu } from "../panels/MaterialMenu";
import { isStructural } from "../panels/nodeRules";
import { panelShown } from "../workspace/geometry";
import { useShortcut } from "../workspace/keymap";
import { useWorkspace } from "../workspace/store";
import { registerAction } from "./actions";
import { emitRename } from "./events";
import { usePrepareMenu } from "./PrepareMenu";
import { overlapNotice, overlapShort } from "../panels/coverage";
import { usePickOverlap } from "../panels/usePickOverlap";

/** Distance between the patch and the toolbar, and the toolbar's margin to whatever it clamps against. */
const GAP = 12;
const MARGIN = 8;
/** The island row at the bottom of the viewport. */
const BOTTOM_INSET = 56;

interface ContextToolbarProps {
  /** Free space left of the toolbar's range: the tree panel's right edge plus a margin. */
  leftInset: number;
  /** Same on the right: the properties panel. */
  rightInset: number;
  /** Free space above: the guided flow strip, which the toolbar must stay clear of. Default none. */
  topInset?: number;
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max);
}

/**
 * Actions for the selection, floating 12 px above the top edge of its projected bounds
 * (`patchAnchor`, set by the viewport), so it never sits on what it acts on; with no room above it
 * goes 12 px below the bottom edge. It stays inside the viewport and out from under the panels,
 * fades in over 160 ms, and steps aside while the camera is being dragged.
 *
 * With faces picked (face mode) the toolbar belongs to the pick (T-061): it says what the pick
 * holds ("3 faces on 2 parts"), leads with Name…, offers Assign as the menu of condition kinds
 * (`boundary.kinds`) that applies to exactly those faces, then Grow and Clear, and Isolate and
 * Hide name the part those faces are on. It never names a patch, because a face pick selects no
 * body. With a patch selected (body mode, or the tree) the actions are the patch's, and Assign is
 * the same menu, reading "Show boundary" only while the patch has one and nothing is picked.
 *
 * The position is written straight to the element: the anchor changes every frame during a
 * camera move, which must not cost a React render.
 */
export default function ContextToolbar({ leftInset, rightInset, topInset = 0 }: ContextToolbarProps) {
  const selected = useStore((s) => s.selectedPatch);
  const hasAnchor = useStore((s) => s.patchAnchor !== null);
  const pickMode = useStore((s) => s.pickMode);
  const geometry = useStore((s) => s.geometry);
  const selectPatch = useStore((s) => s.selectPatch);
  const isolate = useStore((s) => s.isolate);
  const toggleHidden = useStore((s) => s.toggleHidden);

  // Keep the last patch while fading out so the buttons do not change under the pointer.
  const [last, setLast] = useState<string | null>(selected);
  useEffect(() => {
    if (selected !== null) setLast(selected);
  }, [selected]);
  const patch = selected ?? last;

  // Name…, Grow and Clear act on the live pick (ADR-0021 3.5): they show while faces are picked or
  // more than one body is, Name… then leading; a single body keeps the toolbar it had. The sample
  // part has no part node, so its pick never makes a tag.
  const picked = useStore((s) => s.picked);
  const topologyLoaded = useStore((s) => s.topologyLoaded);
  // §15.4's pending state (A6): when a mode change could not convert because a record was not loaded,
  // say so instead of showing "Nothing is picked" — the spec forbids clearing the pick silently, and
  // an empty-looking pick is the same thing to the user. The pending text wins only when there is no
  // real pick to describe, so it never hides one that did convert.
  const pickLabel = describePick(picked, pickMode);
  const pending = pickLabel === null ? pendingConversionText(picked, pickMode, topologyLoaded) : null;
  const nameable = useMemo(() => pickAsMembers(picked, pickMode) !== null, [picked, pickMode]);
  // **An edge pick can be named too** (ADR-0022 §15.4: a cap loop is stored as a tag's edge members).
  // This gate was written when only faces and multiple bodies made a tag, so even after `pickAsMembers`
  // learned edges, an edge pick showed no Name… button and `cap loop=` stayed unreachable — a second
  // gate behind the first, which is why fixing one was not enough.
  const showName =
    nameable && (pickMode === "face" || pickMode === "edge" || picked.bodies.length > 1);
  // Face mode with faces held: the toolbar describes the pick and never the part under it (T-061).
  const facePick = pickMode === "face" && pickLabel !== null;
  // Isolate and Hide act on the part the picked faces belong to; on a patch in body mode.
  const activePart = facePick ? activeFacePart(picked) : null;
  const partPatch = facePick ? (activePart === null ? null : patchForSource(activePart, geometry)) : patch;
  const isolated = useStore((s) => partPatch !== null && s.isolatedPatch === partPatch);
  const hidden = useStore((s) => partPatch !== null && s.hiddenPatches.includes(partPatch));

  // The tree, which changes rarely; a selector that walked it would run on every camera frame.
  const tree = useStore((s) => s.tree);
  const assigned = useMemo(() => patch !== null && findByPath(tree, boundaryPath(patch)) !== null, [tree, patch]);
  const assign = useAssignMenu();
  // Faces of the pick a boundary already holds: said beside the pick, before Assign is pressed.
  const overlapAnswer = usePickOverlap();
  const overlap = facePick ? overlapShort(overlapAnswer) : null;
  const overlapTitle = overlapNotice(overlapAnswer) ?? undefined;
  // "Related ▾" (ADR-0022 §6.4, T-051): find faces like the ones picked.
  const related = useRelatedMenu();
  // "Material ▾" (ADR-0022 §4.4, T-045): give the picked bodies a material.
  const material = useMaterialMenu();
  // Prepare (T-029): the geometry operations, on the pick. It shows whenever the pick holds
  // something — a body for the enclosing and repairing entries, faces for Remove faces — and its
  // entries that the pick does not fit are disabled with a reason.
  const prepare = usePrepareMenu();
  /** "Show boundary" only while the patch has one and the live pick holds nothing. */
  const showBoundary = assigned && pickLabel === null;

  const ref = useRef<HTMLDivElement>(null);
  const insets = useRef({ leftInset, rightInset, topInset });
  /** Width with the text labels showing: below this much free room the toolbar drops to icons. */
  const fullWidth = useRef(0);
  const [orbiting, setOrbiting] = useState(false);
  const visible = (selected !== null || pickLabel !== null) && hasAnchor && !orbiting;

  const place = useCallback(() => {
    const el = ref.current;
    const parent = el?.parentElement;
    const anchor = useStore.getState().patchAnchor;
    if (!el || !parent || !anchor) return;
    const width = parent.clientWidth;
    const height = parent.clientHeight;
    const compact = width - insets.current.leftInset - insets.current.rightInset < fullWidth.current;
    if ((el.dataset.compact === "true") !== compact) el.dataset.compact = String(compact);
    const w = el.offsetWidth;
    const h = el.offsetHeight;
    let minX = insets.current.leftInset;
    let maxX = width - insets.current.rightInset - w;
    if (maxX < minX) {
      // not enough free width between the panels: the whole viewport will do
      minX = MARGIN;
      maxX = width - w - MARGIN;
    }
    let y = anchor.y - GAP - h;
    const top = MARGIN + insets.current.topInset;
    if (y < top) y = (anchor.bottom ?? anchor.y) + GAP; // no room above the bounds: flip below them
    const x = clamp(anchor.x - w / 2, minX, Math.max(minX, maxX));
    y = clamp(y, top, Math.max(top, height - BOTTOM_INSET - h));
    el.style.transform = `translate3d(${Math.round(x)}px, ${Math.round(y)}px, 0)`;
  }, []);

  // The labels change with the patch, its hidden state, its boundary, the pick's description and
  // the pick's actions (Name…, Grow, Clear), so measure the full width again then.
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el || (patch === null && pickLabel === null)) return;
    const compact = el.dataset.compact;
    el.dataset.compact = "false";
    fullWidth.current = el.offsetWidth;
    if (compact !== undefined) el.dataset.compact = compact;
  }, [patch, pickLabel, hidden, isolated, assigned, pickMode, showName, overlap]);

  useLayoutEffect(() => {
    insets.current = { leftInset, rightInset, topInset };
    place();
  }, [leftInset, rightInset, topInset, place, visible, patch, pickLabel, hidden, isolated, assigned, pickMode, showName, overlap]);

  useEffect(() => {
    const unsubscribe = useStore.subscribe((state, previous) => {
      if (state.patchAnchor !== previous.patchAnchor) place();
    });
    const parent = ref.current?.parentElement;
    const observer = parent ? new ResizeObserver(place) : null;
    if (parent) observer?.observe(parent);
    return () => {
      unsubscribe();
      observer?.disconnect();
    };
  }, [place]);

  // Hidden for the length of a press on the canvas (orbit, pan, or the click that picks).
  useEffect(() => {
    const down = (e: PointerEvent) => {
      if (e.target instanceof HTMLCanvasElement) setOrbiting(true);
    };
    const up = () => setOrbiting(false);
    window.addEventListener("pointerdown", down, true);
    window.addEventListener("pointerup", up, true);
    window.addEventListener("pointercancel", up, true);
    window.addEventListener("blur", up);
    return () => {
      window.removeEventListener("pointerdown", down, true);
      window.removeEventListener("pointerup", up, true);
      window.removeEventListener("pointercancel", up, true);
      window.removeEventListener("blur", up);
    };
  }, []);

  // The tree renames in place; when the workspace hides it, the toolbar does the job itself (a
  // name prompt). Assigning is the shared menu of condition kinds (T-061), which the properties
  // panel and the ribbon offer the same way.
  const treeShown = useWorkspace((s) => panelShown(s.document.layout, "tree"));
  const [renaming, setRenaming] = useState<{ id: string; name: string } | null>(null);
  const isolateKeys = useShortcut("selection.isolate");
  const hideKeys = useShortcut("selection.hide");
  const renameKeys = useShortcut("tree.rename");

  const rename = () => {
    if (patch === null) return;
    selectPatch(patch); // selects the matching node in the tree
    const nodeId = useStore.getState().selectedId;
    if (treeShown) {
      emitRename({ patch, nodeId });
      return;
    }
    const node = findNode(useStore.getState().tree, nodeId);
    if (!node) useStore.getState().pushToast("info", `${patch} has no node in the tree to rename.`);
    else if (isStructural(node)) useStore.getState().pushToast("info", `${node.name} is part of the project skeleton and cannot be renamed.`);
    else setRenaming({ id: node.id, name: node.name });
  };

  // Name… (ADR-0021 3.5): the dialog names the live pick as a tag; a name the server refuses keeps
  // it open with the server's message.
  const [naming, setNaming] = useState(false);
  const openName = useCallback(() => {
    const s = useStore.getState();
    if (pickAsMembers(s.picked, s.pickMode) !== null) setNaming(true);
    else if (s.pickMode === "point")
      s.pushToast(
        "info",
        "A point pick makes nothing nameable: tag the edges of the opening you mean, or pick faces.",
      );
    else s.pushToast("info", "Pick faces, edges or bodies of a part first.");
  }, []);
  const clearPick = useCallback(() => useStore.getState().clearPick(), []);
  // Grow: the feature-angle region around the picked faces (`tag.region`, read-only) becomes the pick.
  const growPick = useCallback(async () => {
    const s = useStore.getState();
    const pick = s.pickMode === "face" ? pickAsMembers(s.picked, "face") : null;
    if (pick === null) {
      s.pushToast("info", "Grow works on picked faces: switch to face picking and pick some.");
      return;
    }
    let result: TagRegion;
    try {
      result = (await s.execute("tag.region", { members: pick.members })) as TagRegion;
    } catch {
      return; // reported by execute
    }
    const now = useStore.getState();
    if (now.picked !== s.picked) return; // the pick moved on while the server worked
    // The active member stays on the part its faces belong to when the region still holds it.
    // A face pick sets no `selectedPatch` (T-061), so the pick itself names the part now.
    const keep = activeFacePart(s.picked);
    const grown = pickFromRegion(result.members, keep);
    const faces = Object.values(grown.picked.faces).some((ids) => ids.length > 0);
    useStore.setState({
      picked: grown.picked,
      pickMode: faces ? "face" : "body",
      // Grown faces select faces alone; grown whole bodies keep an active member, as body mode does.
      selectedPatch: faces || grown.active === null ? null : patchForSource(grown.active, now.geometry),
    });
  }, []);
  const nameSelection = async (name: string): Promise<string | null> => {
    const s = useStore.getState();
    const pick = pickAsMembers(s.picked, s.pickMode);
    if (pick === null) {
      setNaming(false);
      return null;
    }
    let created: { id: string };
    try {
      created = (await s.execute("tag.create", { name, members: pick.members, kind: pick.kind })) as { id: string };
    } catch (err) {
      return err instanceof StokesApiError ? err.message : (err as Error).message;
    }
    setNaming(false);
    await s.refresh(); // the tree holds the tag before the selection lands on it
    useStore.getState().select(created.id, { keepPick: true });
    return null;
  };

  // The palette and the keymap reach Name… and Grow through the action bus (no default chords).
  useEffect(() => {
    const stops = [registerAction("selection.name", openName), registerAction("selection.grow", () => void growPick())];
    return () => stops.forEach((stop) => stop());
  }, [openName, growPick]);

  // In face mode the isolate and hide labels name the part the picked faces are on (ADR-0021 3.1).
  const face = pickMode === "face";
  const isolateLabel = face ? "Isolate part" : "Isolate";
  const hideLabel = hidden ? (face ? "Show part" : "Show") : face ? "Hide part" : "Hide";
  const present = patch !== null || pickLabel !== null;
  const ariaLabel = facePick && pickLabel !== null ? `Actions for ${pickLabel}` : patch !== null ? `Actions for ${patch}` : "Selection actions";

  // The element stays mounted so that the position and size observers keep their target.
  return (
    <div ref={ref} className="ctx-toolbar glass" data-visible={visible} role="toolbar" aria-label={ariaLabel}>
      {present && (
        <>
          {facePick && <span className="ctx-pick">{pickLabel}</span>}
          {overlap !== null && (
            <span className="ctx-pick ctx-overlap" role="status" title={overlapTitle} data-ws="ctx-overlap">
              <span className="setup-dot" style={{ background: "var(--warning)" }} aria-hidden="true" /> {overlap}
            </span>
          )}
          {/* §15.4's pending state: which parts a mode conversion is still waiting for. Shown in the
              edge/point modes, where a conversion may legitimately have nothing to show yet. */}
          {pending !== null && <span className="ctx-pick ctx-pending">{pending}</span>}
          {showName && (
            <Tooltip label="Tag selection…" side="top" describe={false}>
              <Button variant="ghost" className="lead" aria-label="Tag selection" onClick={openName}>
                <PenLine size={14} /> <span className="ctx-label">Name…</span>
              </Button>
            </Tooltip>
          )}
          {showBoundary ? (
            <Tooltip label={`Select the boundary of ${patch}`} side="top" describe={false}>
              <Button variant="ghost" className={showName ? undefined : "lead"} aria-label="Show boundary" onClick={() => patch !== null && void assignBoundary(patch)}>
                <Tag size={14} /> <span className="ctx-label">Show boundary</span>
              </Button>
            </Tooltip>
          ) : (
            <>
              <Tooltip label={pickLabel !== null ? `Assign a condition to ${pickLabel}` : `Assign a condition to ${patch ?? "the selection"}`} side="top" describe={false}>
                <Button variant="ghost" className={showName || assigned ? undefined : "lead"} aria-label="Assign a condition" onClick={(e) => assign.open(e.currentTarget)}>
                  <Tag size={14} /> <span className="ctx-label">Assign</span> <ChevronDown size={13} />
                </Button>
              </Tooltip>
              {!facePick && picked.bodies.length > 0 && (
                <Tooltip label="Give the picked bodies a material" side="top" describe={false}>
                  <Button
                    variant="ghost"
                    aria-label="Assign a material"
                    onClick={(e) => material.open(e.currentTarget)}
                  >
                    <Layers size={14} /> <span className="ctx-label">Material</span> <ChevronDown size={13} />
                  </Button>
                </Tooltip>
              )}
              {facePick && (
                <Tooltip label="Find faces related to the picked ones" side="top" describe={false}>
                  <Button
                    variant="ghost"
                    aria-label="Find related faces"
                    aria-disabled={!related.ready}
                    onClick={(e) => related.open(e.currentTarget)}
                  >
                    <Search size={14} /> <span className="ctx-label">Related</span> <ChevronDown size={13} />
                  </Button>
                </Tooltip>
              )}
            </>
          )}
          {/* Prepare (T-029): shown while the pick holds something — a body for the enclosing and
              repairing entries, faces for Remove faces — and never for a bare patch selection:
              nothing but a live pick can be prepared. */}
          {pickLabel !== null && (
            <Tooltip label="Prepare the picked geometry: enclose it, extract its fluid, combine or repair it" side="top" describe={false}>
              <Button
                ref={prepare.buttonRef}
                variant="ghost"
                aria-haspopup="menu"
                aria-expanded={false}
                aria-label="Prepare"
                onClick={(e) => prepare.open(e.currentTarget)}
              >
                <Wrench size={14} /> <span className="ctx-label">Prepare</span> <ChevronDown size={13} />
              </Button>
            </Tooltip>
          )}
          {showName && (
            <>
              {face && (
                <Tooltip label="Grow the picked faces across smooth edges" side="top" describe={false}>
                  <Button variant="ghost" aria-label="Grow" onClick={() => void growPick()}>
                    <Expand size={14} /> <span className="ctx-label">Grow</span>
                  </Button>
                </Tooltip>
              )}
              <Tooltip label="Clear the pick" side="top" describe={false}>
                <Button variant="ghost" aria-label="Clear the pick" onClick={clearPick}>
                  <X size={14} /> <span className="ctx-label">Clear</span>
                </Button>
              </Tooltip>
            </>
          )}
          {partPatch !== null && (
            <>
              <Tooltip label={isolated ? "Show all patches again" : `Isolate ${partPatch}`} hint={isolateKeys.hint} side="top" describe={false}>
                <Button variant="ghost" active={isolated} aria-pressed={isolated} aria-label={isolateLabel} aria-keyshortcuts={isolateKeys.keys} onClick={() => isolate(isolated ? null : partPatch)}>
                  <Eye size={14} /> <span className="ctx-label">{isolateLabel}</span>
                </Button>
              </Tooltip>
              <Tooltip label={hidden ? `Show ${partPatch}` : `Hide ${partPatch}`} hint={hideKeys.hint} side="top" describe={false}>
                <Button variant="ghost" aria-label={hideLabel} aria-keyshortcuts={hideKeys.keys} onClick={() => toggleHidden(partPatch)}>
                  <EyeOff size={14} /> <span className="ctx-label">{hideLabel}</span>
                </Button>
              </Tooltip>
            </>
          )}
          {/* Rename selects the patch, which a face pick must not do (T-061): face mode leaves it out. */}
          {!facePick && patch !== null && (
            <Tooltip label={`Rename ${patch}`} hint={renameKeys.hint} side="top" describe={false}>
              <Button variant="ghost" aria-label="Rename" aria-keyshortcuts={renameKeys.keys} onClick={rename}>
                <Pencil size={14} /> <span className="ctx-label">Rename</span>
              </Button>
            </Tooltip>
          )}
        </>
      )}
      {assign.menu}
      {assign.dialog}
      {related.menu}
      {material.menu}
      {material.dialog}
      {prepare.menu}
      <PromptDialog
        open={renaming !== null}
        title="Rename"
        label="New name"
        initialValue={renaming?.name ?? ""}
        confirmLabel="Rename"
        onCancel={() => setRenaming(null)}
        onSubmit={(name) => {
          const target = renaming;
          setRenaming(null);
          if (target && name !== target.name) void useStore.getState().execute("node.rename", { node: target.id, name }).catch(() => undefined);
        }}
      />
      <PromptDialog
        open={naming}
        // **The dialog names what it makes** (claude-win2's 2000 item 3): the entry that opens it is
        // `Tag selection…`, so the title, the field and the button all say tag rather than the
        // vaguer "name" - which named neither the thing created nor the thing named.
        title="Tag selection"
        label="Tag name"
        placeholder="inlet"
        confirmLabel="Create tag"
        onCancel={() => setNaming(false)}
        onSubmit={nameSelection}
      />
    </div>
  );
}
