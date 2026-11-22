/**
 * "Create": the implicit modeller's operations (ADR-0035) as a menu, with three groups (Shape,
 * Transform, Combine). The entries are data (`app/createEntries.ts`); each leaf opens the ordinary
 * operation card (`panels/OperationCard.tsx`) for its type, with the kind already chosen.
 *
 * The same hook serves every button that opens it (the viewport toolbar's, the Guided Geometry
 * card's), and one instance registers the three `create.*` actions so the palette reaches them.
 */
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode, type RefObject } from "react";
import { findNode, useStore } from "../state/store";
import type { Picked } from "../state/pick";
import type { NodeSummary } from "../api/types";
import { Menu, type MenuItemSpec } from "../components/Menu";
import { registerAction } from "./actions";
import { startSketch } from "./editOperation";
import { CREATE_COMBINE_ACTION, CREATE_MODIFY_ACTION, CREATE_PATTERN_ACTION, CREATE_SHAPE_ACTION, CREATE_SKETCH_ACTION, CREATE_TRANSFORM_ACTION, SKETCHER_ID, createEntries, type CreateEntry } from "./createEntries";

/** The picked bodies by family: implicit parts, and the rest (of which `meshBodies` are mesh-only). */
export function pickFamilies(picked: Picked, tree: NodeSummary | null): { implicitBodies: number; otherBodies: number; meshBodies: number } {
  let implicitBodies = 0;
  let otherBodies = 0;
  let meshBodies = 0;
  for (const id of picked.bodies) {
    const family = findNode(tree, id)?.params.family;
    if (family === "implicit") implicitBodies++;
    else otherBodies++;
    if (family === "mesh") meshBodies++;
  }
  return { implicitBodies, otherBodies, meshBodies };
}

function entriesNow(): CreateEntry[] {
  const s = useStore.getState();
  const built: Record<string, boolean> = {};
  for (const [type, info] of Object.entries(s.operationTypes)) built[type] = info.built;
  return createEntries({ ...pickFamilies(s.picked, s.tree), built });
}

export interface CreateMenu {
  /** Open the menu from a press on `button`. */
  open(button?: HTMLElement | null): void;
  buttonRef: RefObject<HTMLButtonElement | null>;
  menu: ReactNode;
}

export function useCreateMenu({ registerActions = true }: { registerActions?: boolean } = {}): CreateMenu {
  const picked = useStore((s) => s.picked);
  const tree = useStore((s) => s.tree);
  const operationTypes = useStore((s) => s.operationTypes);
  const entries = useMemo(() => {
    const built: Record<string, boolean> = {};
    for (const [type, info] of Object.entries(operationTypes)) built[type] = info.built;
    return createEntries({ ...pickFamilies(picked, tree), built });
  }, [picked, tree, operationTypes]);

  const buttonRef = useRef<HTMLButtonElement | null>(null);
  const [open, setOpen] = useState(false);
  const [sub, setSub] = useState<string | null>(null);

  const choose = useCallback((entry: CreateEntry) => {
    if (entry.entries !== undefined) {
      setSub(entry.id);
      setOpen(true);
      return;
    }
    if ((entry.id === SKETCHER_ID || entry.tool !== undefined) && entry.enabled) {
      setOpen(false);
      setSub(null);
      startSketch(entry.tool);
      return;
    }
    if (entry.op !== undefined) {
      setOpen(false);
      setSub(null);
      useStore.getState().openOperation(entry.op, entry.params);
    }
  }, []);

  /** Opens the list, or one group by id; a group the pick does not fit says why instead. */
  const show = useCallback((id: string | null, button?: HTMLElement | null) => {
    if (button) buttonRef.current = button as HTMLButtonElement;
    const entry = id === null ? null : (entriesNow().find((item) => item.id === id) ?? null);
    if (entry !== null && !entry.enabled) {
      useStore.getState().pushToast("info", entry.why ?? "The pick does not fit this operation");
      return;
    }
    setSub(entry?.id ?? null);
    setOpen(true);
  }, []);

  useEffect(() => {
    if (!registerActions) return;
    const stops = [
      registerAction(CREATE_SKETCH_ACTION, () => show("sketch", buttonRef.current)),
      registerAction(CREATE_SHAPE_ACTION, () => show("shape", buttonRef.current)),
      registerAction(CREATE_TRANSFORM_ACTION, () => show("transform", buttonRef.current)),
      registerAction(CREATE_COMBINE_ACTION, () => show("combine", buttonRef.current)),
      registerAction(CREATE_PATTERN_ACTION, () => show("pattern", buttonRef.current)),
      registerAction(CREATE_MODIFY_ACTION, () => show("modify", buttonRef.current)),
    ];
    return () => stops.forEach((stop) => stop());
  }, [show, registerActions]);

  const items = useMemo((): MenuItemSpec[] => {
    const group = sub === null ? null : entries.find((entry) => entry.id === sub);
    const list = group?.entries ?? entries;
    const out: MenuItemSpec[] = [];
    if (group) out.push({ type: "heading", id: `heading-${group.id}`, label: group.label });
    for (const entry of list) {
      out.push({
        id: entry.id,
        label: entry.entries !== undefined ? `${entry.label} ▸` : entry.label,
        disabled: !entry.enabled,
        hint: entry.enabled ? undefined : entry.why,
        onSelect: () => choose(entry),
      });
    }
    return out;
  }, [entries, sub, choose]);

  const label = sub === null ? "Create" : (entries.find((entry) => entry.id === sub)?.label ?? "Create");
  return {
    open: (button) => show(null, button),
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
        label={label}
        placement="bottom-start"
      />
    ),
  };
}
