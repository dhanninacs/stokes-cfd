/**
 * What the command palette lists: the API commands, and what the window does by itself. The window's
 * own entries are the front-end actions of the shortcuts table (app/actions.ts, run through
 * `runAction`, each with the chord the workspace binds to it now), "Customise workspace" and "Reset
 * workspace", and "Move <panel> to <place>" for every panel the registry knows (the same moves
 * Customise mode makes, built with `movePanel` and written to the workspace with one `workspace.set`).
 * Built when the palette opens, so the chords, the places and the actions that have a handler are
 * today's.
 */
import type { CommandSchema } from "../api/types";
import { ACTIONS, hasAction, runAction } from "../app/actions";
import { useStore } from "../state/store";
import { movePanel, type PanelTarget } from "../workspace/arrange";
import type { LayoutSpec } from "../workspace/types";
import { placeOf } from "../workspace/geometry";
import { shortcutHint } from "../workspace/keymap";
import { PANELS, type PanelDef } from "../workspace/registry";
import { useWorkspace } from "../workspace/store";
import type { DockSlot } from "../workspace/types";
import { plainDescription } from "./plainDescription";

/** An API command; its key is its name, so the recents from before the actions still match. */
export interface CommandItem {
  kind: "command";
  key: string;
  name: string;
  /** The name as words, so the ranker can match what a user types (see `commandLabel`). */
  label: string;
  description: string;
  command: CommandSchema;
}

/** Something the window does itself. `key` starts with `ui:`, so it cannot be mistaken for a command's. */
export interface UiItem {
  kind: "ui";
  key: string;
  /** The row's title, in the interface font (a command's name is an identifier, in mono). */
  name: string;
  /** Muted text after it: the action's id, or what the entry does. */
  description: string;
  /** The first chord bound to the action, as a key cap; none when it has no chord. */
  chord?: string;
  section: "actions" | "layout";
  run(): void;
}

export type PaletteItem = CommandItem | UiItem;

/**
 * A command's name as words: `project.sample_open` becomes `project sample open`.
 *
 * **Why a label at all.** `CommandSchema` carries an identifier and a description, no title - so the
 * palette had nothing to rank but the identifier, and `rankCommands` scored it against a query the user
 * typed from the words on their screen. Splitting the name on its separators gives the ranker the same
 * words a user would type without inventing copy the API does not have.
 */
export function commandLabel(name: string): string {
  return name.replace(/[._:]/g, " ");
}

export function commandItems(commands: readonly CommandSchema[]): CommandItem[] {
  return commands.map((command) => ({
    kind: "command",
    key: command.name,
    name: command.name,
    label: commandLabel(command.name),
    description: plainDescription(command.description),
    command,
  }));
}

/** The palette is where these run from, and Customise mode has its own entry below. */
const NOT_LISTED: ReadonlySet<string> = new Set(["palette.open", "workspace.customise"]);

type Place = DockSlot | "float" | "hidden";

/** Where a panel can go: the phrase after "Move <panel> to", and how the panel stands there. */
const PLACES: readonly { place: Place; to: string; now: string }[] = [
  { place: "left", to: "the left side", now: "on the left" },
  { place: "right", to: "the right side", now: "on the right" },
  { place: "bottom", to: "the bottom drawer", now: "in the bottom drawer" },
  { place: "float", to: "a floating card", now: "as a floating card" },
  { place: "hidden", to: "hidden", now: "hidden" },
];

/** Moves a panel with the arrangement Customise mode's menu uses: at the end of a slot, or at the top right as a card. */
function movePanelTo(def: PanelDef, place: Place): void {
  const workspace = useWorkspace.getState();
  const layout = workspace.document.layout;
  const others = (slot: DockSlot | "float") => layout[slot].panels.filter((p) => p.id !== def.id).length;
  const target: PanelTarget =
    place === "hidden"
      ? { kind: "hidden" }
      : place === "float"
        ? { kind: "float", anchor: "tr", x: 16, y: 16 + 32 * others("float") }
        : { kind: "slot", slot: place, index: others(place) };
  /*
    **The move is computed from the layout AT WRITE TIME, not from the one read at click time.**

    `setValue` awaits a server round trip and is not queued, so two opens in quick succession both read
    the same document and the second write silently discarded the first - measured: the right slot held
    `properties, mesh, conditions` and ended up with `mesh, conditions`, because the third click rebuilt
    from a layout that predated the first two.
  */
  void workspace.setValue(["layout"], movePanel(useWorkspace.getState().document.layout, def.id, target)).then((ok) => {
    if (!ok) return;
    const label = PLACES.find((p) => p.place === place)?.to ?? place;
    useStore.getState().pushToast("ok", `Moved ${def.title} to ${label}.`, { label: "Undo", run: () => void useWorkspace.getState().setValue(["layout"], layout) });
  });
}

/** "Move <panel> to <place>" for every place the panel is not in already. */
function moveItems(): UiItem[] {
  const layout = useWorkspace.getState().document.layout;
  const items: UiItem[] = [];
  for (const def of PANELS.values()) {
    const here = placeOf(layout, def.id) ?? "hidden";
    for (const { place, to } of PLACES) {
      if (place === here) continue;
      const now = PLACES.find((p) => p.place === here)?.now ?? here;
      items.push({ kind: "ui", key: `ui:move:${def.id}:${place}`, name: `Move ${def.title} to ${to}`, description: `Now ${now}`, section: "layout", run: () => movePanelTo(def, place) });
    }
    /*
      **"Open <panel>" for a panel that is not on screen** (the owner, via claude-win2: with the
      right strip removed, Plots and the Design table are reachable from the palette and a View menu
      entry, and *"when opened they appear where the layout puts them, and the user can close them
      again"*).

      `Move X to the right side` already existed, but it is a MOVE - it assumes you know where a
      panel should go. "Open" is the question a user actually has: the panel is not visible, put it
      somewhere I can see it. It goes to the slot the panel registry names for it, which is the same
      answer Customise gives, and it is only offered when the panel is hidden - an entry that would
      do nothing must not show.
    */
    /*
      **"Open" is offered whenever the panel is not on SCREEN, which is not the same as being hidden.**

      The owner's report: *"hiding the tree and settings tabs is broken"*. `mesh` and `conditions` sit
      in the right slot of the document but belong to other stages (Mesh, Physics), so stage focus
      folds them - and the palette offered only **"Move Mesh to hidden"**, an entry that would have done
      nothing, while no way to show the panel existed at all. Measured in the Geometry stage.
    */
    /*
      **The entry exists whether or not the panel is already open** (G-301's H-3, Claude's 0630 point 2).
      It used to be offered only when the panel was hidden or folded away - correct as a list of actions,
      and wrong as a place to find a panel: measured, a user who typed "Open Results" a second time got a
      list with no "Open Results" in it, because the first use had placed the panel. The same words gave a
      different answer the second time, which is the shape of the owner's "not working properly".

      It now always appears, reading "Results (open)" once it is on screen, and choosing it FOCUSES the
      panel rather than moving it.
    */
    {
      const open = !(here === "hidden" || !visibleNow(def));
      const home = here === "hidden" ? homeSlot(def) : (here as Exclude<Place, "hidden">);
      items.push({
        kind: "ui",
        key: `ui:open:${def.id}`,
        name: open ? `${def.title} (open)` : `Open ${def.title}`,
        description: open ? "Bring it to the front" : `Show it ${PLACES.find((p) => p.place === home)?.now ?? home}`,
        section: "layout",
        // **Opening also UNCOLLAPSES the slot it lands in**, which a plain move does not: the right
        // slot is collapsed by default, so moving a panel there left it visible only as a 40 px strip -
        // measured after `Open Properties`, which "succeeded" and showed the user a strip. A move is a
        // rearrangement of a visible thing; an OPEN is a request to see the panel.
        run: () => openPanelTo(def, home),
      });
    }
  }
  return items;
}


/**
 * Puts a panel back on screen and OPENS the slot it lands in, in **one write**.
 *
 * **Two writes lose**, measured: `movePanel` sends the whole `layout`, and so does the `collapsed`
 * write, so the second overwrote the first and `Open Properties` left the right slot unrendered. The
 * arrangement is one document, so one call builds it: the move, then `collapsed: false` on the slot -
 * which is a rearrangement, not a preference, so it belongs inside the same value.
 */
function openPanelTo(def: PanelDef, place: Exclude<Place, "hidden">): void {
  const workspace = useWorkspace.getState();
  /*
    **MARK IT OPENED FIRST, and only MOVE it when the document does not already place it there.**

    That order is the fix for the whole report. A panel like `mesh` is already in the right slot but
    folded by stage focus, so it needs no move at all - and moving it re-sorted the slot, which lost a
    neighbouring panel (measured: `properties, mesh, conditions` became `mesh, conditions` after one
    open). Recording the open is what unfolds it; the move is only for a panel that is genuinely
    somewhere else.
  */
  useWorkspace.getState().markOpened(place, def.id);
  const current = useWorkspace.getState().document.layout;
  const already = placeOf(current, def.id) === place;
  if (already) {
    // Unfold the slot if it was collapsed - "open" must put it on screen - and nothing else.
    if (current[place].collapsed) {
      void workspace.setValue(["layout", place, "collapsed"], false);
    }
    requestAnimationFrame(() => useWorkspace.getState().setActiveTab(place, def.id));
    useStore.getState().pushToast("ok", `Opened ${def.title}.`);
    return;
  }
  const others = (slot: DockSlot | "float") => current[slot].panels.filter((p) => p.id !== def.id).length;
  const target: PanelTarget =
    place === "float"
      ? { kind: "float", anchor: "tr", x: 16, y: 16 + 32 * others("float") }
      : { kind: "slot", slot: place, index: others(place) };
  const moved = movePanel(current, def.id, target);
  const next: LayoutSpec =
    place === "float" ? moved : { ...moved, [place]: { ...moved[place], collapsed: false } };
  void workspace.setValue(["layout"], next).then((ok) => {
    if (!ok) return;
    requestAnimationFrame(() => useWorkspace.getState().setActiveTab(place, def.id));
    useStore.getState().pushToast("ok", `Opened ${def.title}.`, { label: "Undo", run: () => void useWorkspace.getState().setValue(["layout"], current) });
  });
}


/**
 * Whether a panel is on screen right now.
 *
 * **Three things can take it away and only one is "hidden"**: the document can place it in `hidden`, the
 * slot can be collapsed, and stage focus can fold a panel that belongs to another stage. A user asking
 * "why can I not see Mesh" is asking all three questions, so "Open" answers all three.
 */
function visibleNow(def: PanelDef): boolean {
  const document = useWorkspace.getState().document.layout;
  const place = placeOf(document, def.id);
  if (place === null || place === "hidden") return false;
  if (place !== "float" && document[place].collapsed) return false;
  const stages = def.stages;
  if (stages === undefined) return true;
  const stage = useStore.getState().stage;
  if (stages.includes(stage)) return true;
  // Opened explicitly in this session, so it is on screen despite the stage.
  return (useWorkspace.getState().opened[place] ?? []).includes(def.id);
}

/** Where a panel belongs when it is opened: its registry slot, or a floating card if it has none. */
function homeSlot(def: PanelDef): Exclude<Place, "hidden"> {
  // **`defaultPlace` is the field**, which the registry already uses for "where this panel belongs" -
  // asking it rather than hard-coding a table keeps one answer.
  const slot = PLACES.find((p) => p.place === def.defaultPlace)?.place;
  return slot === "hidden" || slot === undefined ? "float" : slot;
}

/** The window's own entries; `reset` opens the confirmation of "Reset workspace" (the palette hosts it). */
export function uiItems(reset: () => void): UiItem[] {
  const items: UiItem[] = [];
  for (const spec of ACTIONS) {
    if (spec.context !== "global" || NOT_LISTED.has(spec.id) || !hasAction(spec.id)) continue;
    items.push({ kind: "ui", key: `ui:${spec.id}`, name: spec.title, description: spec.id, chord: shortcutHint(spec.id), section: "actions", run: () => void runAction(spec.id) });
  }
  items.push(
    {
      kind: "ui",
      key: "ui:customise",
      name: "Customise workspace",
      description: "Move panels, islands and top-bar items",
      chord: shortcutHint("workspace.customise"),
      section: "actions",
      run: () => useWorkspace.getState().setCustomising(true),
    },
    { kind: "ui", key: "ui:reset", name: "Reset workspace", description: "Back to the Stokes defaults; it asks first", section: "actions", run: reset },
  );
  return [...items, ...moveItems()];
}
