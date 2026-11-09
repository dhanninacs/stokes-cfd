/**
 * "Material ▾" on the context toolbar (ADR-0022 §4.4, T-045): give the picked bodies a material.
 *
 * The menu §4.4 describes, in order: the project's own materials in a fluid and a solid section, the
 * five most recent names, and **Browse library…** — a search over `material.library_search` with
 * provider chips and an Add. Adding from the library is the only two-step path (`add_from_library`
 * then `region.assign`), and its toast says so; an existing material is one `region.assign`.
 *
 * The rules (sections, recents, the commands, the message) live in `materialPick.ts` and are tested
 * without a renderer. This file issues the calls and draws the menu.
 */

import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import type { LibraryRecord, MaterialProvider } from "../api/types";
import { StokesApiError } from "../api/client";
import { useStore } from "../state/store";
import { Menu, type MenuItemSpec } from "../components/Menu";
import { Dialog } from "../components/Dialog";
import { Button } from "../components/ui";
import {
  addedMessage,
  assignMaterial,
  domainPartOf,
  libraryCommands,
  librarySearchParams,
  providerChips,
  readRecents,
  roleAfterAssign,
  splitByKind,
  withRecent,
  writeRecents,
  type MaterialChoice,
} from "./materialPick";

export interface MaterialMenu {
  open(button: HTMLElement): void;
  /** True when there is something to assign a material to. */
  ready: boolean;
  menu: ReactNode;
  /** The "Browse library…" dialog; render it beside the menu. */
  dialog: ReactNode;
}

/** The picked bodies as `region.assign` takes them: whole bodies, never faces. */
function pickedBodies(): { part: string; faces: "all" }[] {
  const picked = useStore.getState().picked;
  const bodies = picked.bodies.filter((part) => part.length > 0);
  // With only faces picked there is no body to give a material to; the row is disabled instead.
  return bodies.map((part) => ({ part, faces: "all" as const }));
}

/** The `localStorage` the recents live in, or null when the browser refuses it. */
function storage(): Storage | null {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

export function useMaterialMenu(): MaterialMenu {
  const anchor = useRef<HTMLElement | null>(null);
  const [open, setOpen] = useState(false);
  const [libraryOpen, setLibraryOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  // The project's materials and the provider list are asked for when the menu opens, not per render.
  const [materials, setMaterials] = useState<MaterialChoice[]>([]);
  const [providers, setProviders] = useState<MaterialProvider[]>([]);
  const recents = useMemo(() => readRecents(storage()), []);

  const refreshMaterials = useCallback(async () => {
    const state = useStore.getState();
    if (state.client === null) return;
    try {
      const list = await state.execute("material.list", {}, { silent: true });
      const choices: MaterialChoice[] = (list as { id: string; name: string; kind?: string }[]).map(
        (item) => ({ id: item.id, name: item.name, kind: item.kind ?? "" }),
      );
      setMaterials(choices);
    } catch {
      setMaterials([]);
    }
  }, []);

  useEffect(() => {
    if (!open) return;
    void refreshMaterials();
    const state = useStore.getState();
    if (state.client === null) return;
    void state
      .execute("material.providers", {}, { silent: true })
      .then((answer) => setProviders(providerChips(answer as MaterialProvider[])))
      .catch(() => setProviders([]));
  }, [open, refreshMaterials]);

  /** Assign an existing project material: one command, one undo step (§4.4). */
  const assign = async (choice: MaterialChoice) => {
    const bodies = pickedBodies();
    if (bodies.length === 0) {
      useStore.getState().pushToast("info", "Pick bodies first, then choose them a material.");
      return;
    }
    setBusy(true);
    try {
      const state = useStore.getState();
      const outcome = await assignMaterial(
        state.execute,
        choice.name,
        bodies,
        domainPartOf(state.tree),
        choice.kind,
      );
      writeRecents(storage(), withRecent(recents, choice.name));
      state.pushToast("ok", `${choice.name} assigned to ${bodies.length === 1 ? "1 body" : `${bodies.length} bodies`}${outcome.madeFluid ? "; it is the fluid to mesh" : ""}`);
      if (outcome.roleError !== "") state.pushToast("error", `The material is assigned, but the body could not be made the fluid: ${outcome.roleError}`);
      setOpen(false);
    } catch (err) {
      useStore.getState().pushToast("error", err instanceof StokesApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  /** The library path: add the record, then assign it — two commands, two undo steps (§4.4). */
  const addFromLibrary = async (record: LibraryRecord) => {
    const bodies = pickedBodies();
    if (bodies.length === 0) {
      useStore.getState().pushToast("info", "Pick bodies first, then choose them a material.");
      return;
    }
    setBusy(true);
    try {
      const calls = libraryCommands(record, bodies);
      const added = (await useStore.getState().execute(calls.add.method, calls.add.params, {
        silent: true,
      })) as { name?: unknown };
      if (typeof added.name !== "string" || added.name === "") {
        throw new Error("The material was added, but the server did not return its project name.");
      }
      const state = useStore.getState();
      const assigned = await state.execute(
        calls.assign.method,
        { ...calls.assign.params, material: added.name },
        { silent: true },
      );
      const outcome = await roleAfterAssign(state.execute, assigned, bodies, domainPartOf(state.tree), record.kind);
      writeRecents(storage(), withRecent(recents, added.name));
      state.pushToast("ok", `${addedMessage(record, added.name, bodies.length)}${outcome.madeFluid ? "; it is the fluid to mesh" : ""}`);
      if (outcome.roleError !== "") state.pushToast("error", `The material is assigned, but the body could not be made the fluid: ${outcome.roleError}`);
      setLibraryOpen(false);
      setOpen(false);
    } catch (err) {
      useStore.getState().pushToast("error", err instanceof StokesApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const { fluids, solids } = splitByKind(materials);
  const recentChoices: MaterialChoice[] = recents.map((name) => {
    const known = materials.find((m) => m.name === name);
    return known ?? { id: name, name, kind: "" };
  });

  const items: MenuItemSpec[] = [];
  if (fluids.length > 0) {
    items.push({ type: "heading", id: "h-fluid", label: "Fluids" });
    for (const choice of fluids) {
      items.push({ id: `fluid:${choice.id}`, label: choice.name, disabled: busy, onSelect: () => void assign(choice) });
    }
  }
  if (solids.length > 0) {
    items.push({ type: "heading", id: "h-solid", label: "Solids" });
    for (const choice of solids) {
      items.push({ id: `solid:${choice.id}`, label: choice.name, disabled: busy, onSelect: () => void assign(choice) });
    }
  }
  if (recentChoices.length > 0) {
    items.push({ type: "heading", id: "h-recent", label: "Recent" });
    for (const choice of recentChoices) {
      items.push({ id: `recent:${choice.name}`, label: choice.name, disabled: busy, onSelect: () => void assign(choice) });
    }
  }
  if (items.length === 0) {
    items.push({ id: "none", label: "No materials in this project", disabled: true });
  }
  items.push({ type: "separator", id: "sep" });
  items.push({
    id: "browse",
    label: "Browse library…",
    disabled: busy,
    onSelect: () => setLibraryOpen(true),
  });

  return {
    open: (button: HTMLElement) => {
      anchor.current = button;
      if (pickedBodies().length === 0) {
        useStore.getState().pushToast("info", "Pick bodies first, then choose them a material.");
        return;
      }
      setOpen(true);
    },
    ready: true,
    menu: (
      <Menu
        open={open}
        anchor={anchor}
        onClose={() => setOpen(false)}
        items={items}
        label="Assign a material"
        placement="bottom-start"
      />
    ),
    dialog: (
      <LibraryBrowser
        open={libraryOpen}
        providers={providers}
        busy={busy}
        onClose={() => setLibraryOpen(false)}
        onAdd={(record) => void addFromLibrary(record)}
      />
    ),
  };
}

/** "Browse library…": a search field, provider chips and the matching records with an Add each. */
function LibraryBrowser({
  open,
  providers,
  busy,
  onClose,
  onAdd,
}: {
  open: boolean;
  providers: readonly MaterialProvider[];
  busy: boolean;
  onClose(): void;
  onAdd(record: LibraryRecord): void;
}) {
  const [query, setQuery] = useState("");
  const [provider, setProvider] = useState<string | null>(null);
  const [records, setRecords] = useState<LibraryRecord[]>([]);
  const [error, setError] = useState("");
  // The search is the server's; the field's own debounce keeps a keypress from a round trip.
  useEffect(() => {
    if (!open) return;
    const state = useStore.getState();
    if (state.client === null) return;
    let cancelled = false;
    const timer = setTimeout(() => {
      void state
        .execute(
          "material.library_search",
          librarySearchParams(query, provider),
          { silent: true },
        )
        .then((answer) => {
          if (cancelled) return;
          const found = (answer as { records?: LibraryRecord[] }).records ?? [];
          setRecords(found);
          setError("");
        })
        .catch((err: unknown) => {
          if (cancelled) return;
          setRecords([]);
          setError(err instanceof StokesApiError ? err.message : String(err));
        });
    }, 180);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [open, query, provider]);

  return (
    <Dialog
      open={open}
      title="Browse material library"
      onClose={onClose}
      actions={
        <Button onClick={onClose} disabled={busy}>
          Close
        </Button>
      }
    >
      <input
        className="field mb-2"
        placeholder="Search by name, formula or alias…"
        value={query}
        autoComplete="off"
        spellCheck={false}
        onChange={(e) => setQuery(e.target.value)}
      />
      {providers.length > 0 && (
        <div className="mb-2 flex flex-wrap gap-1">
          {providers.map((item) => (
            <Button
              key={item.name}
              variant={provider === item.name ? "primary" : undefined}
              onClick={() => setProvider(provider === item.name ? null : item.name)}
            >
              {item.name}
            </Button>
          ))}
        </div>
      )}
      {error !== "" && (
        <div role="alert" className="mb-2 text-[12px]" style={{ color: "var(--danger)" }}>
          {error}
        </div>
      )}
      <ul className="m-0 max-h-[320px] list-none overflow-y-auto p-0">
        {records.map((record) => (
          <li key={`${record.provider}:${record.fluid}`} className="flex items-center gap-2 py-1">
            <span className="min-w-0 flex-1 truncate text-[12.5px]">
              {record.name} <span className="muted">{record.formula}</span>
            </span>
            <span className="muted text-[11px]">{record.provider}</span>
            <Button disabled={busy} onClick={() => onAdd(record)}>
              Add
            </Button>
          </li>
        ))}
        {records.length === 0 && error === "" && <li className="muted text-[12px]">No matches.</li>}
      </ul>
    </Dialog>
  );
}
