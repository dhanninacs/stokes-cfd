import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import * as THREE from "three";
import { Menu } from "../components/Menu";
import { PromptDialog } from "../components/PromptDialog";
import { useAssignMenu } from "../panels/AssignMenu";
import { useMaterialMenu } from "../panels/MaterialMenu";
import { useRelatedMenu } from "../panels/RelatedMenu";
import { describePick, findNode, readablePick, useStore, type StokesState } from "../state/store";
import { currentScene } from "../viewport/scene";
import { directionVector, type ViewDirection } from "../viewport/viewDirections";
import { panelShown } from "../workspace/geometry";
import { shortcutHint } from "../workspace/keymap";
import { useWorkspace } from "../workspace/store";
import { copyText } from "../workspace/WorkspacePanel";
import { actionContextOf, type ActionContext, type ContextInput } from "./actionContext";
import { enabledFor, hasAction, registerAction, runAction, setMouseScheme } from "./actions";
import { contextMenuItems, type ChainedMenu } from "./contextMenuItems";
import { resolveKeyboardTarget, resolveViewportTarget, selectionFor, type ContextTarget } from "./contextTarget";
import { usePrepareMenu } from "./PrepareMenu";
import { removeNode } from "./removeNode";
import { renameTarget, type RenameRequest } from "./renameTarget";

/** A right press that moves further than this is a pan, not a click (the scene's `POINTER_SLOP`). */
const SLOP = 4;

/** What an action's `enabled(ctx)` reads, from the stores as they are now. */
function contextInput(): ContextInput {
  const s = useStore.getState();
  const w = useWorkspace.getState().document;
  const { bodies, faces } = readablePick(s.picked, s.pickMode);
  return {
    project: s.project !== null,
    canUndo: s.project?.can_undo ?? false,
    canRedo: s.project?.can_redo ?? false,
    pickMode: s.pickMode,
    picked: s.picked,
    bodies,
    faces,
    pickLabel: describePick(s.picked, s.pickMode),
    selectedPatch: s.selectedPatch,
    hiddenPatches: s.hiddenPatches,
    isolatedPatch: s.isolatedPatch,
    previousPicks: s.pickHistory.length,
    navMode: s.navMode,
    orbitMode: s.orbitMode,
    viewMode: s.viewMode,
    orthographic: s.orthographic,
    grid: w.viewport.grid,
    sectionOn: s.section !== null,
    paintMode: s.paintMode,
    measureMode: s.measureMode,
    treeShown: panelShown(w.layout, "tree"),
    // **Whether the bodies in play are already out of the simulation** (T-221). Computed here because
    // `actionContext` imports only types, and the answer needs the tree: the entry offers "Use in
    // simulation" exactly when there is nothing left to leave out.
    simulateOut: simulateOutOf(s),
  };
}

/** The part-bearing targets: the ones Rename, Delete and Copy act on. */
type PartTarget = Extract<ContextTarget, { kind: "body" | "face" }>;
const isPartTarget = (t: ContextTarget): t is PartTarget => t.kind === "body" || t.kind === "face";

/** What the menu was opened on, and the action context as it was then. */
interface OpenMenu {
  at: { x: number; y: number };
  target: ContextTarget;
  ctx: ActionContext;
}

/** Frame the content from one of the seven standard directions. */
function viewFrom(dir: ViewDirection): void {
  currentScene()?.rig.fit(new THREE.Vector3(...directionVector(dir)));
}

/** Copy the part's name or path; the node is found through the project tree. */
async function copyOf(target: PartTarget, what: "name" | "path"): Promise<void> {
  const s = useStore.getState();
  const node = findNode(s.tree, target.part);
  if (node === null) {
    s.pushToast("info", "The sample part has no node");
    return;
  }
  const text = what === "name" ? node.name : node.path;
  if (await copyText(text, null)) useStore.getState().pushToast("ok", `Copied ${text}`);
  else useStore.getState().pushToast("error", "The clipboard is not available here");
}

/** Delete the part's node with the tree's Undo toast. */
async function deleteOf(target: PartTarget): Promise<void> {
  const s = useStore.getState();
  const node = findNode(s.tree, target.part);
  if (node === null) {
    s.pushToast("info", "The sample part has no node");
    return;
  }
  await removeNode(node);
}

/**
 * The viewport's context menu (T-172, ux-menus PLAN a): a right click on a body, a face, the pick or
 * empty space, and the `context.menu` action for the keyboard. What it lists is `contextMenuItems`; this
 * component finds the target, applies the CAD selection rule, supplies the actions that need the stores,
 * the camera and the clipboard, and hangs the toolbar's hook menus (Assign, Material, Prepare, Related)
 * from the point it opened at.
 *
 * The right button pans in every mouse scheme, so the click is judged on release: the press must start
 * on the viewport's own canvas, stay within `SLOP`, be the only button held throughout, and end with every
 * button up. The browser's `contextmenu` event stays prevented (App.tsx) and is not the trigger: it fires
 * on press on Linux and macOS and on release on Windows.
 */
/**
 * Whether every body the menu could act on is ALREADY left out of the simulation (T-221).
 *
 * **"Every", not "any"**: the entry says "Leave out of simulation" while there is something left to
 * leave out, so it offers the other label only when nothing in play is still in. The action itself
 * decides the direction the same way, from the same bodies, so the label and the click agree.
 */
function simulateOutOf(s: StokesState): boolean {
  const ids = new Set<string>(s.picked.bodies);
  if (s.selectedId !== null) {
    const node = findNode(s.tree, s.selectedId);
    if (node?.type === "part") ids.add(node.id);
    else if (node?.type === "group") for (const inside of node.children ?? []) if (inside.type === "part") ids.add(inside.id);
  }
  if (ids.size === 0) return false;
  return [...ids].every((id) => findNode(s.tree, id)?.params?.simulate === false);
}

export default function ContextMenu() {
  const [open, setOpen] = useState<OpenMenu | null>(null);
  const [renaming, setRenaming] = useState<RenameRequest | null>(null);
  const pointRef = useRef<HTMLSpanElement>(null);
  const canvasRef = useRef<HTMLElement | null>(null);

  const assign = useAssignMenu();
  const related = useRelatedMenu();
  const material = useMaterialMenu();
  // The toolbar's Prepare hook owns the `prepare.*` actions; a second owner would take them over.
  const prepare = usePrepareMenu({ registerActions: false });

  /** Open at a client point for a target; the pick has already been changed if the rule says so. */
  const show = useCallback((at: { x: number; y: number }, target: ContextTarget) => {
    canvasRef.current = document.querySelector<HTMLElement>(".viewport-bg canvas");
    const point = pointRef.current;
    if (point) {
      point.style.left = `${Math.round(at.x)}px`;
      point.style.top = `${Math.round(at.y)}px`;
    }
    setOpen({ at, target, ctx: actionContextOf(contextInput(), target) });
  }, []);

  // The mouse trigger. Capture listeners on the window, installed once, as the context toolbar's are.
  useEffect(() => {
    let press: { x: number; y: number } | null = null;
    const down = (e: PointerEvent) => {
      press = null;
      if (e.button !== 2 || e.pointerType === "touch") return;
      // The canvas of the viewport, not any canvas: the Explore plots draw on canvases of their own.
      if (e.target !== document.querySelector(".viewport-bg canvas")) return;
      if (document.querySelector("[aria-modal='true']")) return;
      const s = useStore.getState();
      // Paint and Measure own the left button; a right click during their stroke is part of it.
      if ((s.paintMode || s.measureMode) && (e.buttons & 1) !== 0) return;
      press = { x: e.clientX, y: e.clientY };
    };
    const move = (e: PointerEvent) => {
      if (press === null) return;
      // A second button pressed or released mid-press arrives here, not as down or up (chorded buttons).
      if (e.buttons !== 2 || Math.hypot(e.clientX - press.x, e.clientY - press.y) > SLOP) press = null;
    };
    const up = (e: PointerEvent) => {
      if (e.button !== 2) return;
      const started = press;
      press = null;
      if (started === null || e.buttons !== 0) return;
      if (Math.hypot(e.clientX - started.x, e.clientY - started.y) > SLOP) return;
      const s = useStore.getState();
      if (s.seedPick) return; // the operation card is waiting for a point
      const hit = currentScene()?.idPickAt(e.clientX, e.clientY) ?? null;
      const target = resolveViewportTarget(hit, s, describePick);
      // The owner's rule: the click selects what it lands on, unless it is already in the pick.
      const sel = selectionFor(target, { pickMode: s.pickMode, paintMode: s.paintMode, measureMode: s.measureMode });
      if (sel?.what === "body") s.pickBody(sel.part, "replace", sel.patch);
      else if (sel?.what === "face" && sel.face !== undefined) s.pickFace(sel.part, sel.face, "replace");
      show({ x: e.clientX, y: e.clientY }, target);
    };
    const cancel = () => {
      press = null;
    };
    window.addEventListener("pointerdown", down, true);
    window.addEventListener("pointermove", move, true);
    window.addEventListener("pointerup", up, true);
    window.addEventListener("pointercancel", cancel, true);
    window.addEventListener("blur", cancel);
    return () => {
      window.removeEventListener("pointerdown", down, true);
      window.removeEventListener("pointermove", move, true);
      window.removeEventListener("pointerup", up, true);
      window.removeEventListener("pointercancel", cancel, true);
      window.removeEventListener("blur", cancel);
    };
  }, [show]);

  const startRename = useCallback((patch: string) => {
    setRenaming(renameTarget(patch, panelShown(useWorkspace.getState().document.layout, "tree")));
  }, []);

  // The keyboard and the palette: the menu at the selection, and the edit actions on the current pick.
  useEffect(() => {
    /** The keyboard's target and its context, or null (with a toast) when the action cannot run on it. */
    const forAction = (id: string): PartTarget | null => {
      const s = useStore.getState();
      const target = resolveKeyboardTarget(s, describePick);
      const ok = enabledFor(id, actionContextOf(contextInput(), target));
      if (ok !== true) {
        s.pushToast("info", ok);
        return null;
      }
      if (!isPartTarget(target)) {
        s.pushToast("info", "Select one body first");
        return null;
      }
      return target;
    };
    const stops = [
      registerAction("context.menu", () => {
        const s = useStore.getState();
        const target = resolveKeyboardTarget(s, describePick);
        const host = document.querySelector(".viewport-bg")?.getBoundingClientRect();
        if (!host) return;
        const anchor = s.patchAnchor;
        show(anchor ? { x: host.left + anchor.x, y: host.top + anchor.y } : { x: host.left + host.width / 2, y: host.top + host.height / 2 }, target);
      }),
      ...(["front", "back", "left", "right", "top", "bottom", "iso"] as const).map((d) => registerAction(`view.from.${d}`, () => viewFrom(d))),
      registerAction("edit.rename", () => {
        const target = forAction("edit.rename");
        if (target) startRename(target.patch);
      }),
      registerAction("edit.delete", () => {
        const target = forAction("edit.delete");
        if (target) void deleteOf(target);
      }),
      registerAction("edit.copyName", () => {
        const target = forAction("edit.copyName");
        if (target) void copyOf(target, "name");
      }),
      registerAction("edit.copyPath", () => {
        const target = forAction("edit.copyPath");
        if (target) void copyOf(target, "path");
      }),
    ];
    return () => stops.forEach((stop) => stop());
  }, [show, startRename]);

  const chain = useCallback(
    (which: ChainedMenu) => {
      const point = pointRef.current;
      if (!point) return;
      if (which === "assign") assign.open(point);
      else if (which === "material") material.open(point);
      else if (which === "prepare") prepare.open(point);
      else related.open(point);
    },
    [assign, material, prepare, related],
  );

  const items = useMemo(() => {
    if (open === null) return [];
    const { ctx, target } = open;
    return contextMenuItems({
      ctx,
      target,
      run: (id) => void runAction(id),
      chain,
      ready: { assign: assign.ready, material: ctx.bodies > 0, prepare: prepare.ready, related: ctx.faces > 0 },
      prepareWhy: prepare.why,
      has: hasAction,
      viewFrom,
      setViewMode: (mode) => useStore.getState().setViewMode(mode),
      // **Both halves** (claude-win2's 1330 answer): the viewport now, the preference for next
      // time. `setMouseScheme` is the one place that knows it, so this menu and the Settings row
      // cannot disagree about whether a scheme is remembered.
      setNavMode: (mode) => setMouseScheme(mode),
      toggleGrid: () => void useWorkspace.getState().setValue(["viewport", "grid"], !useWorkspace.getState().document.viewport.grid),
      copy: (what) => {
        if (isPartTarget(target)) void copyOf(target, what);
      },
      rename: () => {
        if (isPartTarget(target)) startRename(target.patch);
      },
      remove: () => {
        if (isPartTarget(target)) void deleteOf(target);
      },
      isolate: (patch) => {
        const s = useStore.getState();
        s.isolate(s.isolatedPatch ? null : patch);
      },
      hide: (patch) => useStore.getState().toggleHidden(patch),
      hint: shortcutHint,
    });
  }, [open, chain, assign.ready, prepare.ready, prepare.why, startRename]);

  return (
    <>
      <span ref={pointRef} aria-hidden="true" style={{ position: "fixed", left: 0, top: 0, width: 0, height: 0, pointerEvents: "none" }} />
      <Menu open={open !== null} at={open?.at ?? null} returnFocus={canvasRef} onClose={() => setOpen(null)} items={items} label="Viewport" />
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
          const request = renaming;
          setRenaming(null);
          if (request && name !== request.name) void useStore.getState().execute("node.rename", { node: request.id, name }).catch(() => undefined);
        }}
      />
    </>
  );
}
