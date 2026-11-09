/**
 * The value panel (ADR-0022 §7.1, T-044): the floating card that gives a picked set of faces a
 * condition, in one undoable step.
 *
 * This is the point of the whole set-up chain — "pick faces, Condition ▾, Velocity inlet, type
 * `2 m/s`, Enter" — so the ADR specifies it precisely and this component follows that spec rather
 * than inventing a layout:
 *
 * * a **360 px glass card** of the properties panel's kind, anchored under the context toolbar and
 *   clamped into the viewport (the toolbar's own clamping rules are reused);
 * * a **left column** of the kinds grouped by family, each with a 12 px dot in the kind's colour; a
 *   kind the catalogue marks unavailable is disabled and shows its `arrives` milestone;
 * * a **right column** with the name (defaulted from the kind, unique), the target line
 *   ("12 faces of 3 bodies · Replace"), and the kind's parameters filtered by `when`, rendered by
 *   `SchemaFields` — the same field loop the Properties panel and the operation card use (§7.3);
 * * **Enter commits, Esc cancels**, and a refused value keeps the server's message under the field.
 *
 * The panel's rules (grouping, default name, target line, which command to send) live in
 * `conditionPanelLogic.ts` as pure functions, tested without a renderer. This file only renders and
 * issues commands.
 *
 * What it does **not** do yet, deliberately: §7.2's later edits (double-click a boundary row,
 * "Replace faces", "Replace tag") belong to T-047/T-048, which do not exist. The panel therefore
 * only ever *assigns* to a pick today; the `boundary.set` path is built and tested but has no caller
 * until a boundary row can open it.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { StokesApiError } from "../api/client";
import type { BoundaryEntry, ConditionKind, ConditionParam, NodeSummary, PropertySchema } from "../api/types";
import { overlapAfterAssign, overlapNotice } from "./coverage";
import { usePickOverlap } from "./usePickOverlap";
import { useStore } from "../state/store";
import { kindState } from "./kindAvailability";
import { Button } from "../components/ui";
import { assignCommand, assignTarget } from "./assignTarget";
import { groupKinds, defaultName, enterMayCommit, nameToSend, targetLine } from "./conditionPanelLogic";
import { defaultValues, formFields, moreFields, submittedValues } from "./conditions";
// The `.tsx` is REQUIRED: `./SchemaFields` would case-match the helpers' `schemaFields.ts` on Windows
// and macOS, where the two names are one. The same trap renamed the logic module above. This comment
// is load-bearing; do not "tidy" it away.
import SchemaFields from "./SchemaFields.tsx";

/** The kinds as `SchemaFields` wants them: one `PropertySchema` per catalogue parameter. */
function propertiesOf(params: readonly ConditionParam[]): Record<string, PropertySchema> {
  const properties: Record<string, PropertySchema> = {};
  for (const param of params) properties[param.name] = param.schema as PropertySchema;
  return properties;
}

/** Every boundary name in the tree, so a new one can default to a unique name (§7.1). */
function boundaryNames(root: NodeSummary | null): string[] {
  const out: string[] = [];
  const stack: NodeSummary[] = root ? [root] : [];
  while (stack.length) {
    const node = stack.pop()!;
    if (node.type === "boundary") out.push(node.name);
    if (node.children) stack.push(...node.children);
  }
  return out;
}

export interface ConditionPanelProps {
  /** The kind chosen in the toolbar's menu, or null while the panel is closed. */
  kind: ConditionKind | null;
  /** Where the card is placed, in viewport pixels; the caller clamps as the toolbar does. */
  anchor: { left: number; top: number } | null;
  onClose(): void;
}

export default function ConditionPanel({ kind, anchor, onClose }: ConditionPanelProps) {
  const kinds = useStore((s) => s.conditionKinds);
  const picked = useStore((s) => s.picked);
  const pickMode = useStore((s) => s.pickMode);
  const selectedPatch = useStore((s) => s.selectedPatch);
  const tree = useStore((s) => s.tree);
  const geometry = useStore((s) => s.geometry);

  const [values, setValues] = useState<Record<string, unknown>>({});
  /**
   * The values as the last edit left them, written in the same breath as the state. Enter in a field commits
   * the field and then, as the same keypress bubbles to the card, the whole panel; the panel's `commit` closure
   * still holds the render before the field's edit, so it reads this instead (without it "type 0.1, Enter"
   * assigned the default 0 and the server refused it).
   */
  const latest = useRef<Record<string, unknown>>({});
  const [name, setName] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const card = useRef<HTMLDivElement>(null);
  /** The name the form started with: left alone, the server names the boundary (see `nameToSend`). */
  const suggestedName = useRef("");

  const families = useMemo(() => groupKinds(kinds), [kinds]);
  const overlap = overlapNotice(usePickOverlap());

  // A new kind resets the form: its own defaults, and a name defaulted from it and made unique
  // against the boundaries the project already has (§7.1).
  useEffect(() => {
    if (kind === null) return;
    const fresh = defaultValues(kind);
    latest.current = fresh;
    setValues(fresh);
    const suggested = defaultName(kind, boundaryNames(useStore.getState().tree));
    suggestedName.current = suggested;
    setName(suggested);
    setError("");
    setBusy(false);
  }, [kind]);

  // Esc cancels, as the rename input and the assign dialog do (§7.1).
  useEffect(() => {
    if (kind === null) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
      }
    };
    document.addEventListener("keydown", onKey, true);
    return () => document.removeEventListener("keydown", onKey, true);
  }, [kind, onClose]);

  if (kind === null) return null;

  const target = assignTarget({ picked, pickMode, selectedPatch, geometry, tree });
  const properties = propertiesOf(formFields(kind, values));
  const fields = Object.keys(properties);
  // The kind's other parameters keep their defaults and sit behind "More...", closed.
  const moreProperties = propertiesOf(moreFields(kind, values));
  const moreNames = Object.keys(moreProperties);

  /** One `boundary.assign`, so the whole panel is one undo step (§7.1). */
  const commit = async () => {
    if (busy) return;
    const state = useStore.getState();
    const now = assignTarget(state);
    if (now.value === null) {
      setError(now.refused ?? "Nothing is picked to assign to.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      // Only the values the kind uses: the defaults of the others (a velocity inlet's three components) are refused.
      const call = assignCommand(now.value, kind, submittedValues(kind, latest.current));
      const sent = nameToSend(name, suggestedName.current);
      if (sent !== null) call.params.name = sent;
      const result = (await state.execute(call.method, call.params, { silent: true })) as { boundary?: BoundaryEntry };
      await useStore.getState().refresh();
      const shared = overlapAfterAssign(result?.boundary);
      if (shared !== null) useStore.getState().pushToast("info", shared);
      // The new boundary is selected, as the Assign menu does: the tree opens the rows on the way to it and Properties shows it.
      const id = result?.boundary?.id;
      if (id) useStore.getState().select(id, { keepPick: true });
      onClose();
    } catch (err) {
      // The server's message stays, with the form open so the value can be corrected (§7.1).
      setError(err instanceof StokesApiError ? err.message : (err as Error).message);
      setBusy(false);
    }
  };

  // Docked (no anchor) is a class, not an inline `right`, because the offset must clear the right
  // slot: an inline style would win over the stylesheet rule that applies `--slot-right`.
  const style = anchor === null ? undefined : { left: `${anchor.left}px`, top: `${anchor.top}px` };

  return (
    <section
      ref={card}
      className={`condition-panel glass ${anchor === null ? "docked" : ""}`}
      style={style}
      role="dialog"
      aria-label={`Assign ${kind.label}`}
      data-ws="condition-panel"
      onKeyDown={(e) => {
        if (e.key === "Enter" && !busy) {
          e.preventDefault();
          // A field that refused its text (data-refused) keeps the card from sending the value from before the edit.
          if (enterMayCommit(e.target as HTMLElement)) void commit();
        }
      }}
    >
      <div className="condition-kinds" role="listbox" aria-label="Condition kinds">
        {families.map((family) => (
          <div key={family.id} className="condition-family">
            <div className="condition-family-title">{family.title}</div>
            {family.kinds.map((item) => {
              const chosen = item.id === kind.id;
              const state = kindState(item);
              return (
                // A kind the catalogue cannot offer is disabled and says which milestone brings it
                // (§7.1: "the last disabled with its `arrives`"); it is not hidden, so the plan is
                // visible instead of the kind appearing to be missing.
                <button
                  key={item.id}
                  type="button"
                  role="option"
                  aria-selected={chosen}
                  disabled={!state.enabled}
                  title={state.enabled ? item.note : state.reason}
                  className={`condition-kind ${chosen ? "active" : ""}`}
                  onClick={() => useStore.setState({ conditionKind: item })}
                >
                  <span className="condition-dot" style={{ background: item.color }} aria-hidden="true" />
                  {item.label}
                  {!state.enabled && (
                    <span className="muted ml-1 text-[11px]">{item.available ? "not in solver" : item.arrives}</span>
                  )}
                </button>
              );
            })}
          </div>
        ))}
      </div>

      <div className="condition-body">
        <label className="mb-2 block text-[12.5px]">
          Name
          <input
            className="field mt-1"
            value={name}
            spellCheck={false}
            autoComplete="off"
            disabled={busy}
            onChange={(e) => setName(e.target.value)}
          />
        </label>
        <p className="muted mb-2 text-[12px]" data-ws="condition-target">
          {targetLine(target.label, "replace")}
        </p>
        {overlap !== null && (
          // Warn at the moment of assigning, naming the boundary that holds the faces (G10: allowed, then
          // refused by the case compiler); Assign stays enabled.
          <p role="status" className="mb-2 text-[12px]" style={{ color: "var(--warning)" }} data-ws="condition-overlap">
            {overlap}
          </p>
        )}
        {fields.length === 0 ? (
          <p className="muted m-0 text-[12px]">This condition needs no values.</p>
        ) : (
          <SchemaFields
            properties={properties}
            values={values}
            onCommit={(field, value) => {
              latest.current = { ...latest.current, [field]: value };
              setValues(latest.current);
            }}
            onEdit={() => setError("")}
            nodes={tree}
            disabled={busy}
            errors={error === "" ? undefined : { [fields[0]]: error }}
          />
        )}
        {moreNames.length > 0 && (
          <details className="mt-2 text-[12.5px]" data-ws="condition-more">
            <summary className="muted cursor-pointer select-none">More…</summary>
            <div className="mt-1.5">
              <SchemaFields
                properties={moreProperties}
                values={values}
                onCommit={(field, value) => {
                  latest.current = { ...latest.current, [field]: value };
                  setValues(latest.current);
                }}
                onEdit={() => setError("")}
                nodes={tree}
                disabled={busy}
              />
            </div>
          </details>
        )}
        {fields.length === 0 && error !== "" && (
          <div role="alert" className="mt-2 text-[12px]" style={{ color: "var(--danger)" }}>
            {error}
          </div>
        )}
        <div className="condition-actions">
          <Button onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button variant="primary" disabled={busy} onClick={() => void commit()}>
            {busy ? "Assigning…" : "Assign"}
          </Button>
        </div>
      </div>
    </section>
  );
}