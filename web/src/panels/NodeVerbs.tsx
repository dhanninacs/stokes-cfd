import { useEffect, useState } from "react";
import { useStore } from "../state/store";
import { kindState, unsupportedNote } from "./kindAvailability";

/**
 * The verbs a node's own page offers (claude-win2's 0200 item 6, the 0630 go-ahead).
 *
 * **Every entry calls a command that already exists**, which is what the go-ahead required me to check
 * before building: a body's page needs Material and Region, a boundary's page needs the condition
 * kind, and
 *
 * * `region.set {region, material}` assigns a material,
 * * `region.set {region, kind}` changes a region's kind,
 * * `boundary.assign {target, kind}` sets a boundary's condition.
 *
 * **No new server verbs**, so nothing here is a widget in search of an API.
 *
 * ## Why a control rather than a button
 *
 * A picker shows what the node HAS as well as what it could have. A menu that only offers "Assign
 * material…" leaves the current material invisible, which is the state that makes a user open three
 * menus to find out what they already set.
 */
interface Choice {
  id: string;
  name: string;
  /** `fluid` or `solid`; shown beside the name so two materials of different kinds are tellable apart. */
  kind?: string;
}

/** One entry of `material.list`, which returns a LIST rather than a wrapper object. */
interface MaterialRow {
  id: string;
  name: string;
  kind?: string;
}

/** The material picker: what this region uses, and what it could. */
export function MaterialPicker({ regionId, currentId }: { regionId: string; currentId: string | null }) {
  const execute = useStore((s) => s.execute);
  const pushToast = useStore((s) => s.pushToast);
  const [choices, setChoices] = useState<Choice[]>([]);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let live = true;
    void execute("material.list")
      .then((reply) => {
        // **`material.list` returns a LIST, not `{materials: […]}`.** I had guessed the wrapper and
        // the shape check caught it: `AttributeError: 'list' object has no attribute 'keys'` from the
        // command's own reply. Each entry carries `id`, `name`, `kind` and the `regions` using it.
        const raw = (reply as MaterialRow[] | { materials?: MaterialRow[] });
        const rows = Array.isArray(raw) ? raw : (raw.materials ?? []);
        if (live) setChoices(rows.map((m) => ({ id: m.id, name: m.name, kind: m.kind })));
      })
      .catch(() => live && setChoices([]));
    return () => {
      live = false;
    };
  }, [execute]);

  const change = async (id: string) => {
    setBusy(true);
    try {
      await execute("region.set", { region: regionId, material: id });
      pushToast("ok", "Material assigned");
    } catch {
      /* execute logged and toasted it */
    } finally {
      setBusy(false);
    }
  };

  return (
    <label className="flex items-center gap-2 text-[12.5px]" data-ws="material-picker">
      <span className="muted w-[92px] shrink-0">Material</span>
      <select
        className="field"
        value={currentId ?? ""}
        disabled={busy || choices.length === 0}
        aria-label="Material for this region"
        onChange={(e) => void change(e.target.value)}
      >
        <option value="" disabled>
          {choices.length === 0 ? "No materials yet" : "Choose a material"}
        </option>
        {choices.map((c) => (
          <option key={c.id} value={c.id}>
            {c.name}
            {c.kind === undefined ? "" : ` (${c.kind})`}
          </option>
        ))}
      </select>
    </label>
  );
}

/**
 * One entry of `boundary.kinds`, the server's own catalogue.
 *
 * **Read from the server, not listed here.** My first version hardcoded five kinds and **invented
 * their ids** - `velocityInlet`, where the real id is `velocity_inlet` - so every selection would have
 * been refused by `boundary.assign` with an unknown kind. The catalogue exists, carries the labels and
 * the fields each kind needs, and is the thing the command validates against.
 */
interface ConditionKind {
  id: string;
  label: string;
  available: boolean;
  arrives: string | null;
  supported?: boolean;
  unsupported_reason?: string | null;
}

/** The condition picker for a boundary: what it means physically, which is what a mesh needs. */
export function ConditionPicker({ boundaryId, current }: { boundaryId: string; current: string | null }) {
  const execute = useStore((s) => s.execute);
  const pushToast = useStore((s) => s.pushToast);
  const [busy, setBusy] = useState(false);
  const [kinds, setKinds] = useState<ConditionKind[]>([]);

  useEffect(() => {
    let live = true;
    void execute("boundary.kinds")
      .then((reply) => {
        const rows = (Array.isArray(reply) ? reply : ((reply as { kinds?: ConditionKind[] }).kinds ?? [])) as ConditionKind[];
        if (live) setKinds(rows);
      })
      .catch(() => live && setKinds([]));
    return () => {
      live = false;
    };
  }, [execute]);

  const change = async (kind: string) => {
    setBusy(true);
    try {
      await execute("boundary.assign", { target: boundaryId, kind });
      pushToast("ok", "Condition assigned");
    } catch {
      /* execute logged and toasted it */
    } finally {
      setBusy(false);
    }
  };

  const unsupported = unsupportedNote(kinds, current);

  return (
    <label className="flex items-center gap-2 text-[12.5px]" data-ws="condition-picker">
      <span className="muted w-[92px] shrink-0">Condition</span>
      <select
        className="field"
        value={current ?? ""}
        disabled={busy}
        aria-label="Condition for this boundary"
        onChange={(e) => void change(e.target.value)}
      >
        <option value="" disabled>
          {kinds.length === 0 ? "No kinds available" : "Not assigned yet"}
        </option>
        {kinds.map((k) => {
          // A kind the solver cannot write stays listed, greyed, with the reason as its tooltip; the
          // kind the boundary already holds stays pickable so the select shows what the node has.
          const state = kindState(k);
          return (
            <option key={k.id} value={k.id} disabled={!state.enabled && k.id !== current} title={state.reason || undefined}>
              {state.enabled ? k.label : `${k.label} (${state.reason.toLowerCase()})`}
            </option>
          );
        })}
      </select>
      {unsupported !== null && (
        <span className="text-[11.5px]" style={{ color: "var(--warn)" }} role="status">
          {unsupported}
        </span>
      )}
    </label>
  );
}
