/**
 * What the viewport context menu lists (T-172), as a pure function of the action context.
 *
 * Four targets, one table (ux-menus PLAN a): a body, a face, the pick as a whole and empty space. Each
 * entry that has an action id takes its label, its enabled state and the reason it cannot run from the
 * action table (`labelFor`, `enabledFor`): a reason goes in `description`, which the menu shows as a
 * tooltip and a screen reader reads, and never in `hint`, which is for a chord. The component supplies
 * what the module must not import (the stores, the camera, the clipboard) as `MenuDeps`.
 *
 * Four rows open another menu (Assign, Material, Prepare, Related): they are the hooks of the context
 * toolbar, chained this slice. They are `popup` rows, and a hook that cannot run now disables its row
 * with the reason the toolbar's button would give.
 */
import { createElement } from "react";
import { Trash2 } from "lucide-react";
import type { MenuEntry, MenuItemSpec } from "../components/Menu";
import type { NavMode, ViewMode } from "../state/store";
import { VIEW_DIRECTIONS, type ViewDirection } from "../viewport/viewDirections";
import type { ActionContext } from "./actionContext";
import { NAV_MODES, VIEW_MODES, enabledFor, labelFor } from "./actions";
import type { ContextTarget } from "./contextTarget";

export type ChainedMenu = "assign" | "material" | "prepare" | "related";

/** What the component supplies; the pure module never imports a store. */
export interface MenuDeps {
  ctx: ActionContext;
  target: ContextTarget;
  /** `runAction`. */
  run(id: string): void;
  /** Close this menu and open that hook's, hanging from the point the menu opened at. */
  chain(which: ChainedMenu): void;
  /** Whether the hook behind each chained row can run now. */
  ready: Record<ChainedMenu, boolean>;
  /** Why Prepare cannot run (`usePrepareMenu().why`). */
  prepareWhy: string;
  /** `hasAction`: an action nothing handles yet is listed disabled, not hidden. */
  has(id: string): boolean;
  viewFrom(dir: ViewDirection): void;
  setViewMode(mode: ViewMode): void;
  setNavMode(mode: NavMode): void;
  toggleGrid(): void;
  copy(what: "name" | "path"): void;
  rename(): void;
  remove(): void;
  /** Isolate (or show everything again) and hide (or show) one patch: the part the menu was opened on. */
  isolate(patch: string): void;
  hide(patch: string): void;
  /** `shortcutHint`: the chord of an action, or undefined. */
  hint(id: string): string | undefined;
}

export const SAMPLE_REASON = "The sample part has no node";
export const ASSIGN_REASON = "Pick faces or bodies first, or select a patch";
export const MATERIAL_REASON = "Pick bodies first";
export const RELATED_REASON = "Pick faces first";
/**
 * Why an entry cannot be used yet, **in words a user can act on** - never a ticket number.
 *
 * The owner's rule (claude-win2's 2000 message): internal task numbers do not appear in user text.
 * These two said "Arrives in T-175" and "Arrives in T-178", which is a promise addressed to the
 * wrong reader.
 *
 * **A reason is shown only when the action is not REGISTERED** (`pending` checks `deps.has`), so
 * these are the fallbacks for the moment before a handler installs itself - not a statement about the
 * feature. `view.fitSelection` has no handler yet and needs the scene's fit points; `app.settings`
 * does have one, so its reason appears only while the workspace is still loading.
 */
/** Kept for the day `view.fitSelection` gets a handler - see `bodyGroups` for what it needs.
 *
 * **Unused in the menus on purpose** (claude-win2's 2000 item 2): the entry was removed rather than
 * shipped permanently disabled, so this string has no caller in the UI. It stays because the action
 * spec does, and a reader adding the handler should not have to invent the wording. */
export const FIT_SELECTION_REASON = "Select something to fit first";
export const SETTINGS_REASON = "Open a project first";

/** An entry for an action id: label, chord and enabled state from the table, running the action. */
function act(deps: MenuDeps, id: string, over: Partial<MenuEntry> = {}): MenuEntry {
  const hint = deps.hint(id);
  const entry: MenuEntry = { id, label: labelFor(id, deps.ctx), onSelect: () => deps.run(id), ...(hint === undefined ? {} : { hint }), ...over };
  const ok = enabledFor(id, deps.ctx);
  if (ok !== true) {
    entry.disabled = true;
    entry.description = ok;
  }
  return entry;
}

/** An action nothing handles yet: listed, disabled, with the task that builds it. */
function pending(deps: MenuDeps, id: string, reason: string, over: Partial<MenuEntry> = {}): MenuEntry {
  const entry = act(deps, id, over);
  if (!deps.has(id)) {
    entry.disabled = true;
    entry.description = reason;
  }
  return entry;
}

const CHAIN_LABEL: Record<ChainedMenu, string> = {
  assign: "Assign condition",
  material: "Material",
  prepare: "Prepare",
  related: "Related",
};

/** A row that opens another menu; disabled with the reason when that menu has nothing to offer. */
function chained(deps: MenuDeps, which: ChainedMenu): MenuEntry {
  const sample = (deps.target.kind === "body" || deps.target.kind === "face") && deps.target.sample;
  const reason: Record<ChainedMenu, string> = {
    assign: sample ? SAMPLE_REASON : ASSIGN_REASON,
    material: sample ? SAMPLE_REASON : MATERIAL_REASON,
    prepare: deps.prepareWhy,
    related: RELATED_REASON,
  };
  const entry: MenuEntry = { id: `chain.${which}`, label: CHAIN_LABEL[which], popup: true, onSelect: () => deps.chain(which) };
  if (!deps.ready[which]) {
    entry.disabled = true;
    entry.description = reason[which];
  }
  return entry;
}

/** "View ▸": the seven standard views. */
function viewList(deps: MenuDeps): MenuEntry {
  return {
    id: "view",
    label: "View",
    items: VIEW_DIRECTIONS.map((d) => {
      const id = `view.from.${d.id}`;
      return act(deps, id, { label: d.label, onSelect: () => deps.viewFrom(d.id) });
    }),
  };
}

const patchOf = (target: ContextTarget): string | null => (target.kind === "body" || target.kind === "face" ? target.patch : null);

/** Isolate and Hide act on the patch the menu was opened on, not on whatever `selectedPatch` holds. */
function isolateHide(deps: MenuDeps, wording: "body" | "part"): MenuEntry[] {
  const { ctx } = deps;
  const patch = patchOf(deps.target);
  const isolate = act(deps, "selection.isolate", {
    ...(wording === "part" ? { label: ctx.isolated ? "Show all" : "Isolate part" } : {}),
    onSelect: () => patch !== null && deps.isolate(patch),
  });
  const hide = act(deps, "selection.hide", {
    ...(wording === "part" ? { label: ctx.hiddenTarget ? "Show part" : "Hide part" } : {}),
    onSelect: () => patch !== null && deps.hide(patch),
  });
  /*
    **The viewport menu's entry** (the workflow-v2 plan §2, T-221's third entry point). It sits with
    Isolate and Hide because a user looking for "make this not be there" finds all three - and the
    label says which: Hide is the view, this is the simulation.
  */
  const simulate = act(deps, "selection.simulate", {
    label: ctx.simulateOut ? "Use in simulation" : "Leave out of simulation",
  });
  // **`"body"` is the menu opened ON a body; `"part"` styles the wording for a nested part.** The
  // entry belongs on the body menu, and I had this inverted - two shape tests caught it, because the
  // entry appeared on the menu that has no bodies to act on.
  return wording === "body" ? [isolate, hide, simulate] : [isolate, hide];
}

function bodyGroups(deps: MenuDeps): MenuItemSpec[][] {
  return [
    // **`view.fitSelection` is GONE, not disabled** (claude-win2's 2000 item 2: "implement or
    // hide"). It had no handler anywhere and its reason named a task; a row that is always greyed
    // with a promise is dead weight in a menu, and the spec's own words are to implement it or take
    // it out. Isolate and Hide stay - they work.
    //
    // **What it would take to bring it back**: `scene.rig.fit()` already accepts focus points, so
    // the missing piece is the selection's bounding box reaching the viewport - a real addition
    // rather than wiring, which is why it is out rather than half-built. The action spec stays in
    // `actions.ts` so the palette entry and its chord are ready when that lands.
    [...isolateHide(deps, "body")],
    [
      act(deps, "selection.name", { label: "Tag selection…" }),
      chained(deps, "assign"),
      chained(deps, "material"),
      chained(deps, "prepare"),
      act(deps, "selection.siblings"),
      act(deps, "selection.allFaces"),
    ],
    [
      act(deps, "edit.rename", { onSelect: deps.rename }),
      act(deps, "edit.copyName", { onSelect: () => deps.copy("name") }),
      act(deps, "edit.copyPath", { onSelect: () => deps.copy("path") }),
      act(deps, "edit.delete", { icon: createElement(Trash2, { size: 14, style: { color: "var(--danger)" } }), onSelect: deps.remove }),
    ],
    [viewList(deps)],
  ];
}

function faceGroups(deps: MenuDeps): MenuItemSpec[][] {
  return [
    isolateHide(deps, "part"),
    [
      act(deps, "selection.name", { label: "Tag selection…" }),
      chained(deps, "assign"),
      chained(deps, "related"),
      act(deps, "selection.grow", { label: "Grow" }),
      chained(deps, "prepare"),
      act(deps, "selection.allFaces", { label: "All faces of the body" }),
      act(deps, "selection.clearPick", { label: "Clear" }),
    ],
    [act(deps, "edit.copyName", { onSelect: () => deps.copy("name") }), act(deps, "edit.copyPath", { onSelect: () => deps.copy("path") })],
    [viewList(deps)],
  ];
}

function pickGroups(deps: MenuDeps): MenuItemSpec[][] {
  const { ctx } = deps;
  const middle: MenuEntry[] = [act(deps, "selection.name", { label: "Tag selection…" }), chained(deps, "assign")];
  if (ctx.pickMode === "body") middle.push(chained(deps, "material"));
  middle.push(chained(deps, "prepare"));
  if (ctx.pickMode === "face") middle.push(chained(deps, "related"));
  middle.push(act(deps, "selection.previous"), act(deps, "selection.clearPick", { label: "Clear" }));
  // `view.fitSelection` removed here too - see `bodyGroups` for why, and for what it would take.
  return [middle, [viewList(deps)]];
}

function emptyGroups(deps: MenuDeps): MenuItemSpec[][] {
  const { ctx } = deps;
  const projection: MenuEntry = { id: "view.projection", label: "Orthographic", checked: ctx.orthographic, onSelect: () => deps.run("view.projection") };
  const projectionHint = deps.hint("view.projection");
  if (projectionHint !== undefined) projection.hint = projectionHint;
  const display: MenuEntry = {
    id: "display",
    label: "Display",
    items: VIEW_MODES.map((m) => ({ id: `display.${m.id}`, label: m.label, radio: true, checked: ctx.viewMode === m.id, onSelect: () => deps.setViewMode(m.id) })),
  };
  const mouse: MenuEntry = {
    id: "mouse",
    label: "Mouse",
    items: NAV_MODES.map((m) => ({ id: `mouse.${m.id}`, label: m.label, description: m.summary, radio: true, checked: ctx.navMode === m.id, onSelect: () => deps.setNavMode(m.id) })),
  };
  const grid: MenuEntry = { id: "grid", label: "Grid", checked: ctx.grid, onSelect: deps.toggleGrid };
  const settings = pending(deps, "app.settings", SETTINGS_REASON, { label: "Settings…" });
  return [
    [act(deps, "view.fit"), viewList(deps), projection, display, mouse, grid],
    [act(deps, "view.showAll"), act(deps, "selection.clearPick", { label: "Clear" }), act(deps, "selection.previous")],
    [act(deps, "history.undo"), act(deps, "history.redo")],
    [settings],
  ];
}

/** The entries for the target, in groups with a separator between them. */
export function contextMenuItems(deps: MenuDeps): MenuItemSpec[] {
  const kind = deps.target.kind;
  const groups = kind === "body" ? bodyGroups(deps) : kind === "face" ? faceGroups(deps) : kind === "pick" ? pickGroups(deps) : emptyGroups(deps);
  const items: MenuItemSpec[] = [];
  groups.filter((group) => group.length > 0).forEach((group, index) => {
    if (index > 0) items.push({ type: "separator", id: `sep-${index}` });
    items.push(...group);
  });
  return items;
}
