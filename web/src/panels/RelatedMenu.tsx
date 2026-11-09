/**
 * "Related ▾" on the context toolbar (ADR-0022 §6.4, T-051): find faces like the ones picked.
 *
 * §6.4's interaction: **a click adds the matches to the pick and Shift-click replaces them. The
 * hover preview is NOT built** (board row T-051 — the overlay takes no provisional set), so this
 * comment does not claim it. The query is `tag.related`, which needs the picked faces as members, so
 * a menu with nothing picked is disabled rather than offering a relation that would be refused.
 *
 * The rules (which relations exist, what a click does, how the answer becomes pick items) live in
 * `related.ts` as pure functions and are tested without a renderer; this hook only issues the call
 * and previews the answer.
 */

import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import type { TagRegionMember } from "../api/types";
import { useStore } from "../state/store";
import { Menu, type MenuItemSpec } from "../components/Menu";
import {
  RELATED_RELATIONS,
  relatedHow,
  relatedItems,
  relatedSummary,
  type RelatedRelation,
} from "./related";

export interface RelatedMenu {
  /** Open the menu from a press on `button`. */
  open(button: HTMLElement): void;
  /** True when there is something to relate: render the button enabled. */
  ready: boolean;
  /** The menu: render it next to the button. */
  menu: ReactNode;
}

/** What `tag.related` answers. */
interface RelatedAnswer {
  members: TagRegionMember[];
  count: number;
  relation: string;
}

/** The members of the live pick, which is what `tag.related` takes. */
function pickedMembers(): TagRegionMember[] {
  const picked = useStore.getState().picked;
  const members: TagRegionMember[] = [];
  for (const [part, faces] of Object.entries(picked.faces)) {
    if (faces.length === 0) continue;
    // Contiguous runs become ranges, so a 500-face pick is a few numbers rather than a long list.
    const sorted = [...faces].sort((a, b) => a - b);
    const ranges: [number, number][] = [];
    let start = sorted[0];
    let previous = sorted[0];
    for (const id of sorted.slice(1)) {
      if (id === previous + 1) {
        previous = id;
        continue;
      }
      ranges.push([start, previous]);
      start = id;
      previous = id;
    }
    ranges.push([start, previous]);
    members.push({ part, faces: ranges });
  }
  return members;
}

export function useRelatedMenu(): RelatedMenu {
  const anchor = useRef<HTMLElement | null>(null);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  // Whether Shift is held when an entry is chosen. §6.4 says a Shift-click replaces while a plain
  // click adds, and the shared `Menu` calls `onSelect()` without an event — so the modifier is read
  // from the keyboard while the menu is open rather than by changing a component every menu uses.
  const shiftHeld = useRef(false);
  useEffect(() => {
    if (!open) return;
    const down = (e: KeyboardEvent) => {
      if (e.key === "Shift") shiftHeld.current = true;
    };
    const up = (e: KeyboardEvent) => {
      if (e.key === "Shift") shiftHeld.current = false;
    };
    const reset = () => {
      shiftHeld.current = false;
    };
    document.addEventListener("keydown", down, true);
    document.addEventListener("keyup", up, true);
    // A pointer press is the other way Shift arrives (a Shift-click inside the menu); the event
    // carries the modifier even when no key event fired first.
    const down2 = (e: PointerEvent) => {
      shiftHeld.current = e.shiftKey;
    };
    document.addEventListener("pointerdown", down2, true);
    window.addEventListener("blur", reset);
    return () => {
      document.removeEventListener("keydown", down, true);
      document.removeEventListener("keyup", up, true);
      document.removeEventListener("pointerdown", down2, true);
      window.removeEventListener("blur", reset);
    };
  }, [open]);

  const openMenu = (button: HTMLElement) => {
    anchor.current = button;
    if (pickedMembers().length === 0) {
      useStore.getState().pushToast("info", "Pick faces first, then ask what is related.");
      return;
    }
    shiftHeld.current = false;
    setOpen(true);
  };

  /** The matches for a relation, or null when the server refused or nothing matched. */
  const find = useCallback(async (relation: RelatedRelation): Promise<TagRegionMember[] | null> => {
    const state = useStore.getState();
    if (state.client === null) return null;
    const members = pickedMembers();
    if (members.length === 0) return null;
    try {
      const answer = await state.execute(
        "tag.related",
        { members, relation: relation.id, parts: null, tolerance: null },
        { silent: true },
      );
      const found = (answer as RelatedAnswer).members ?? [];
      return found.length > 0 ? found : null;
    } catch (err) {
      return null;
    }
  }, []);

  /** Commit a relation's matches to the pick: add on a click, replace on a Shift-click (§6.4). */
  const commit = async (relation: RelatedRelation, shift: boolean) => {
    if (busy) return;
    setBusy(true);
    try {
      const found = await find(relation);
      if (found === null) {
        useStore.getState().pushToast("info", `Nothing related by ${relation.label.toLowerCase()}.`);
        return;
      }
      const items = relatedItems(found);
      if (items.length === 0) return;
      useStore.getState().pickMany(items, relatedHow(shift));
      useStore.getState().pushToast("ok", relatedSummary(items));
    } finally {
      setBusy(false);
      setOpen(false);
    }
  };

  const items: MenuItemSpec[] = RELATED_RELATIONS.map((relation) => ({
    id: relation.id,
    label: relation.label,
    hint: relation.detail,
    disabled: busy,
    onSelect: () => void commit(relation, shiftHeld.current),
  }));

  return {
    open: openMenu,
    ready: pickedMembers().length > 0,
    menu: (
      <Menu
        open={open}
        anchor={anchor}
        onClose={() => setOpen(false)}
        items={items}
        label="Find related faces"
        placement="bottom-start"
      />
    ),
  };
}
