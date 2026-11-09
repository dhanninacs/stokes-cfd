/**
 * "Prepare": the geometry operations of ADR-0021 decision 8, as a menu (T-029).
 *
 * The entries are data (`app/prepareEntries.ts`, ADR-0022 10.2 adds two above Enclosure… in T-050
 * without touching this file). The same hook serves the context toolbar's Prepare button and the
 * ribbon's Geometry task strip, so both open one menu, and it registers the four `prepare.*`
 * actions while it is mounted (the palette reaches them; none has a default chord).
 *
 * An entry that opens a submenu (`prepare.boolean`, `prepare.repair`) shows its leaves in the same
 * surface, with the parent's label as the heading: the `Menu` component has no submenu of its own.
 */
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode, type RefObject } from "react";
import { readablePick, useStore } from "../state/store";
import { Menu, type MenuItemSpec } from "../components/Menu";
import { registerAction } from "./actions";
import { prepareEntries, type PrepareEntry } from "./prepareEntries";

/** Open a flow helper on the first picked body, or say why not (T-050). */
function startFlow(template: "external" | "internal"): void {
  const state = useStore.getState();
  const target = state.picked.bodies[0];
  if (target === undefined) {
    state.pushToast("info", "Pick a body first");
    return;
  }
  useStore.setState({
    flowRun: { id: Date.now(), template, target, velocity: "", addDefaults: true },
  });
  state.openOperation(template === "external" ? "enclosure" : "extract_fluid");
}

/** The pick as the entries read it, with the operation types the server has. */
function liveEntries(): PrepareEntry[] {
  const s = useStore.getState();
  const { bodies, faces } = readablePick(s.picked, s.pickMode);
  const built: Record<string, boolean> = {};
  for (const [type, info] of Object.entries(s.operationTypes)) built[type] = info.built;
  return prepareEntries({ bodies, faces, built });
}

export interface PrepareMenu {
  /** Open the menu from a press on `button` (the context toolbar's, the ribbon's, or none). */
  open(button?: HTMLElement | null): void;
  /** True when the pick can do at least one of the entries: render the button enabled. */
  ready: boolean;
  /** Why nothing can be prepared, for a disabled button's tooltip. */
  why: string;
  /** The Prepare button's element: the menu hangs from it when the palette runs an action. */
  buttonRef: RefObject<HTMLButtonElement | null>;
  /** The menu: render it beside the button. */
  menu: ReactNode;
}

/**
 * The Prepare menu, as the buttons use it: the entries of the live pick, the submenu state, and the
 * four `prepare.*` actions. Opening an entry whose pick does not fit says why as a toast, as the
 * Assign menu does, so the palette's route into a submenu refuses the same way the button does.
 *
 * The actions are registered by one instance only: a second mounted one (the viewport menu's, T-172)
 * passes `registerActions: false`, or it would take them over and the ribbon and palette would open this
 * menu at its stale point.
 */
export function usePrepareMenu({ registerActions = true }: { registerActions?: boolean } = {}): PrepareMenu {
  const picked = useStore((s) => s.picked);
  const pickMode = useStore((s) => s.pickMode);
  const operationTypes = useStore((s) => s.operationTypes);
  const entries = useMemo(() => {
    const { bodies, faces } = readablePick(picked, pickMode);
    const built: Record<string, boolean> = {};
    for (const [type, info] of Object.entries(operationTypes)) built[type] = info.built;
    return prepareEntries({ bodies, faces, built });
  }, [picked, pickMode, operationTypes]);

  const buttonRef = useRef<HTMLButtonElement | null>(null);
  const [open, setOpen] = useState(false);
  /** The submenu shown in place of the list: the id of the entry that opened it. */
  const [sub, setSub] = useState<string | null>(null);

  const ready = entries.some((entry) => entry.enabled);
  const why = entries.find((entry) => !entry.enabled && entry.why)?.why ?? "Pick a body to prepare it";

  const choose = useCallback((entry: PrepareEntry) => {
    if (entry.entries !== undefined) {
      setSub(entry.id);
      setOpen(true);
      return;
    }
    if (entry.action === "prepare.externalFlow") {
      startFlow("external");
      return;
    }
    if (entry.action === "prepare.internalFlow") {
      startFlow("internal");
      return;
    }
    if (entry.op !== undefined) useStore.getState().openOperation(entry.op, entry.params);
  }, []);

  /** Opens the list, or one entry's submenu by id; a pick that fits nothing says why instead. */
  const show = useCallback((id: string | null, button?: HTMLElement | null) => {
    if (button) buttonRef.current = button as HTMLButtonElement;
    const list = liveEntries();
    const entry = id === null ? null : (list.find((item) => item.id === id) ?? null);
    if (!list.some((item) => item.enabled)) {
      useStore.getState().pushToast("info", list.find((item) => item.why)?.why ?? "Pick a body to prepare it");
      return;
    }
    if (entry !== null && !entry.enabled) {
      useStore.getState().pushToast("info", entry.why ?? "The pick does not fit this operation");
      return;
    }
    setSub(entry?.id ?? null);
    setOpen(true);
  }, []);

  // The four actions of the workspace. The submenu ones hang from the Prepare button on screen: the
  // toolbar's button exists whenever the pick can do anything, which is when they are not refused.
  useEffect(() => {
    if (!registerActions) return;
    const stops = [
      registerAction("prepare.enclosure", () => useStore.getState().openOperation("enclosure")),
      registerAction("prepare.extractFluid", () => useStore.getState().openOperation("extract_fluid")),
      // The two flow helpers (ADR-0022 §10.2, T-050): each opens its card and runs the operation
      // through the ordinary card, so there is no second operation implementation.
      registerAction("prepare.externalFlow", () => startFlow("external")),
      registerAction("prepare.internalFlow", () => startFlow("internal")),
      registerAction("prepare.boolean", () => show("boolean", buttonRef.current)),
      registerAction("prepare.repair", () => show("repair", buttonRef.current)),
    ];
    return () => stops.forEach((stop) => stop());
  }, [show, registerActions]);

  const items = useMemo((): MenuItemSpec[] => {
    const submenu = sub === null ? null : entries.find((entry) => entry.id === sub);
    const list = submenu?.entries ?? entries;
    const items: MenuItemSpec[] = [];
    if (submenu) items.push({ type: "heading", id: `heading-${submenu.id}`, label: submenu.label });
    for (const entry of list) {
      items.push({
        id: entry.id,
        label: entry.entries !== undefined ? `${entry.label} ▸` : entry.label,
        disabled: !entry.enabled,
        hint: entry.enabled ? undefined : entry.why,
        onSelect: () => choose(entry),
      });
    }
    return items;
  }, [entries, sub, choose]);

  return {
    open: (button) => show(null, button),
    ready,
    why,
    buttonRef,
    menu: (
      <Menu
        open={open}
        anchor={buttonRef}
        onClose={() => {
          setOpen(false);
          setSub(null);
        }}
        items={items}
        label={sub === null ? "Prepare" : "Repair"}
        placement="bottom-start"
      />
    ),
  };
}
