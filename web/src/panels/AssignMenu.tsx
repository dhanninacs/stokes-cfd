/**
 * "Assign a condition": the menu of condition kinds from `boundary.kinds` and, for a kind that
 * needs values, a small form of the catalogue's parameters with their defaults. One hook serves
 * the places that offer the action (context toolbar, patch card, ribbon), so all three assign to
 * the same thing: what `assignTarget.ts` reads off the live pick and the selected patch.
 *
 * An assign is one `boundary.assign`, so it is one undo step; what it works on and the message for
 * a pick that cannot be assigned are that module's. A refusal shows its message in the form when it
 * is open, or as a toast when it is not, and the form stays open so the value can be corrected.
 */

import { useId, useMemo, useRef, useState, type ReactNode } from "react";
import { ChevronsUpDown } from "lucide-react";
import { StokesApiError } from "../api/client";
import type { ConditionKind, ConditionParam, NodeSummary, ParamSchema } from "../api/types";
import { useStore } from "../state/store";
import { Button } from "../components/ui";
import { Dialog } from "../components/Dialog";
import { Menu, type MenuItemSpec } from "../components/Menu";
import { assignCommand, assignTarget } from "./assignTarget";
import { defaultValues, formFields } from "./conditions";
import { overlapAfterAssign, overlapShort } from "./coverage";
import { usePickOverlap } from "./usePickOverlap";
import { kindState } from "./kindAvailability";
import { Note } from "../components/Note";

/** A bare number is read in SI, as the `values` parameter reads it; anything else carries its units. */
const BARE_NUMBER = /^[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?$/;

/** What `boundary.assign` answered: the boundary it made or changed. */
interface AssignResult {
  boundary?: { id?: string; name?: string; path?: string; warnings?: string[] };
}

/** Every `boundary` node of the tree, for the parameters that take one (a cyclic's partner). */
function boundaryNodes(root: NodeSummary | null): NodeSummary[] {
  const out: NodeSummary[] = [];
  const stack: NodeSummary[] = root ? [root] : [];
  while (stack.length) {
    const node = stack.pop()!;
    if (node.type === "boundary") out.push(node);
    if (node.children) stack.push(...node.children);
  }
  return out;
}

/** One field of the value form: the catalogue's parameter rendered with the panel's own widgets. */
function KindField({
  param,
  value,
  options,
  disabled,
  onChange,
}: {
  param: ConditionParam;
  value: unknown;
  /** Choices of a reference parameter: the nodes it may name, empty when there are none. */
  options: NodeSummary[];
  disabled: boolean;
  onChange: (value: unknown) => void;
}) {
  const schema: ParamSchema = param.schema;
  const id = useId();
  const title = schema.title ?? param.name;
  const text = value === undefined || value === null ? "" : String(value);
  const label = (
    <label htmlFor={id} className="mb-1 block text-[12.5px]">
      {title}
      {param.required && (
        <span title="The kind needs a value here" style={{ color: "var(--danger)" }}>
          *
        </span>
      )}
    </label>
  );

  if (schema.enum) {
    return (
      <div className="mb-2">
        {label}
        <div className="select-wrap">
          <select id={id} className="field" value={text} disabled={disabled} onChange={(e) => onChange(e.target.value)}>
            {schema.enum.map((option) => (
              <option key={option} value={option}>
                {option}
              </option>
            ))}
          </select>
          <ChevronsUpDown size={13} className="select-chevron" aria-hidden="true" />
        </div>
      </div>
    );
  }

  if (schema["x-kind"] === "ref") {
    return (
      <div className="mb-2">
        {label}
        <div className="select-wrap">
          <select id={id} className="field" value={text} disabled={disabled} onChange={(e) => onChange(e.target.value)}>
            <option value="">none</option>
            {options.map((node) => (
              <option key={node.id} value={node.id}>
                {node.name}
              </option>
            ))}
          </select>
          <ChevronsUpDown size={13} className="select-chevron" aria-hidden="true" />
        </div>
      </div>
    );
  }

  const numeric = schema.type === "number" || schema.type === "integer";
  const array = schema.type === "array";
  return (
    <div className="mb-2">
      {label}
      <input
        id={id}
        className="field"
        type={numeric ? "number" : "text"}
        inputMode={numeric || array ? "decimal" : undefined}
        autoComplete="off"
        spellCheck={false}
        value={text}
        disabled={disabled}
        placeholder={array ? "1, 0, 0" : undefined}
        onChange={(e) => onChange(e.target.value)}
      />
      {schema["x-kind"] === "quantity" && <Note className="text-[11.5px]" tip="A number with a unit, e.g. 2 [m/s], or an expression.">Number and unit</Note>}
    </div>
  );
}

/** The form's text as the command takes it: a bare quantity number in SI, a vector as numbers. */
function submittedValue(param: ConditionParam, raw: unknown): unknown {
  if (raw === undefined || raw === null) return undefined;
  const schema = param.schema;
  const text = String(raw).trim();
  if (text === "") return undefined;
  if (schema.type === "array") {
    const parts = text.replace(/[[\]]/g, "").split(/[\s,]+/).filter(Boolean);
    const numbers = parts.map(Number);
    return numbers.some((n) => !Number.isFinite(n)) ? undefined : numbers;
  }
  if (schema.type === "number" || schema.type === "integer") {
    const n = Number(text);
    return Number.isFinite(n) ? n : undefined;
  }
  if (schema["x-kind"] === "quantity") return BARE_NUMBER.test(text) ? Number(text) : text;
  return text;
}

interface FormState {
  kind: ConditionKind;
  values: Record<string, unknown>;
  error: string;
  busy: boolean;
}

export interface AssignMenu {
  /** Open the list of condition kinds from a press on `button`. */
  open(button: HTMLElement): void;
  /**
   * True when there is something to assign to: render the button enabled. False for a pick the
   * server cannot take (the sample part has no node), which is never widened to the patch.
   */
  ready: boolean;
  /** True when the pick holds something, empty or not. */
  hasPick: boolean;
  /** The pick as one line ("3 faces on 2 parts"), or null while it is empty. */
  pickLabel: string | null;
  /** The kinds menu: render it next to the button. */
  menu: ReactNode;
  /** The value form of the kind being assigned: render it beside the menu. */
  dialog: ReactNode;
}

export function useAssignMenu(): AssignMenu {
  const kinds = useStore((s) => s.conditionKinds);
  const picked = useStore((s) => s.picked);
  const pickMode = useStore((s) => s.pickMode);
  const selectedPatch = useStore((s) => s.selectedPatch);
  const geometry = useStore((s) => s.geometry);
  // The tree decides whether the selected patch already has a boundary; it changes rarely, so this
  // selector does not walk it on every camera frame.
  const tree = useStore((s) => s.tree);

  const anchor = useRef<HTMLElement | null>(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const [form, setForm] = useState<FormState | null>(null);

  const target = useMemo(() => assignTarget({ picked, pickMode, selectedPatch, geometry, tree }), [picked, pickMode, selectedPatch, geometry, tree]);
  const pickLabel = target.label === "" ? null : target.label;
  // Faces of the pick some boundary already holds (G10: allowed, then refused by the case compiler):
  // the menu says so at its head, so the overlap is seen at the moment of choosing a kind.
  const overlap = overlapShort(usePickOverlap());

  const open = (button: HTMLElement) => {
    anchor.current = button;
    const now = assignTarget(useStore.getState());
    if (now.value === null) {
      useStore.getState().pushToast("info", now.refused ?? "Pick faces or bodies first, or select a patch.");
      return;
    }
    setMenuOpen(true);
  };

  /** One assign: one `boundary.assign`, which is one undo step. */
  const run = async (kind: ConditionKind, values: Record<string, unknown>): Promise<string | null> => {
    const state = useStore.getState();
    const now = assignTarget(state);
    if (now.value === null) return now.refused ?? "Pick faces or bodies first, or select a patch.";
    try {
      const call = assignCommand(now.value, kind, values);
      const result = (await state.execute(call.method, call.params, { silent: true })) as AssignResult;
      await useStore.getState().refresh();
      chosen(kind, result);
      return null;
    } catch (err) {
      return err instanceof StokesApiError ? err.message : (err as Error).message;
    }
  };

  /** The new boundary is selected in the tree, its pick left where it is. */
  const chosen = (kind: ConditionKind, result: AssignResult): void => {
    const state = useStore.getState();
    const id = result.boundary?.id;
    if (id) state.select(id, { keepPick: true });
    const name = result.boundary?.name;
    state.pushToast("ok", name ? `${kind.label} assigned to ${name}` : `${kind.label} assigned`);
    // G10: the faces may now be in two boundaries; say so at the moment it happens.
    const shared = name ? overlapAfterAssign({ name, warnings: result.boundary?.warnings }) : null;
    if (shared !== null) state.pushToast("info", shared);
  };

  const choose = (kind: ConditionKind) => {
    // T-044: the value panel is the one form for assigning a condition (ADR-0022 §7.1), so choosing
    // a kind opens it rather than this hook's own dialog. That is deliberate for every caller — the
    // context toolbar, the patch card and the ribbon — so all three lead to the same panel and there
    // is only one form to keep correct. A kind with no values still assigns at once, because there is
    // nothing for a panel to ask.
    const values = defaultValues(kind);
    if (formFields(kind, values).length === 0) {
      setMenuOpen(false);
      void run(kind, {}).then((error) => {
        if (error) useStore.getState().pushToast("error", error);
      });
      return;
    }
    setMenuOpen(false);
    useStore.setState({ conditionKind: kind });
  };

  const submit = async () => {
    if (form === null || form.busy) return;
    const values: Record<string, unknown> = {};
    let missing = "";
    for (const param of formFields(form.kind, form.values)) {
      const value = submittedValue(param, form.values[param.name]);
      if (value !== undefined) values[param.name] = value;
      else if (param.required && missing === "") missing = `Give a value for ${param.schema.title ?? param.name}.`;
    }
    if (missing !== "") {
      setForm({ ...form, error: missing });
      return;
    }
    setForm({ ...form, busy: true, error: "" });
    const error = await run(form.kind, values);
    if (error === null) {
      setForm(null);
      return;
    }
    setForm({ ...form, busy: false, error }); // the server's message, with the form to correct it
  };

  const kindItems: MenuItemSpec[] =
    kinds.length === 0
      ? [{ id: "none", label: "No condition kinds", disabled: true }]
      : kinds.map((kind) => {
          const state = kindState(kind);
          return {
            id: kind.id,
            label: kind.label,
            disabled: !state.enabled,
            hint: state.enabled ? undefined : state.reason,
            onSelect: () => choose(kind),
          };
        });
  const items: MenuItemSpec[] =
    overlap === null ? kindItems : [{ type: "heading", id: "overlap", label: `${overlap}: assigning overlaps it` }, ...kindItems];

  // The form asks for the kind's required values and the switches over them; both follow the
  // values as they are typed (direction components appear when direction becomes components).
  const fields = form === null ? [] : formFields(form.kind, form.values);
  const partners = useMemo(() => boundaryNodes(tree), [tree]);

  return {
    open,
    ready: target.value !== null,
    hasPick: pickLabel !== null,
    pickLabel,
    menu: <Menu open={menuOpen} anchor={anchor} onClose={() => setMenuOpen(false)} items={items} label="Assign a condition" placement="bottom-start" />,
    dialog: (
      <Dialog
        open={form !== null}
        title={form === null ? "Assign" : `Assign ${form.kind.label}`}
        onClose={() => {
          if (form !== null && !form.busy) setForm(null);
        }}
        actions={
          <>
            <Button
              onClick={() => {
                if (form !== null && !form.busy) setForm(null);
              }}
            >
              Cancel
            </Button>
            <Button variant="primary" disabled={form?.busy ?? false} onClick={() => void submit()}>
              {form?.busy ? "Assigning…" : "Assign"}
            </Button>
          </>
        }
      >
        {form !== null && (
          // Enter assigns, as the value panel does (ADR-0022 7.1); Escape closes the dialog.
          <div onKeyDown={(e) => {
            if (e.key !== "Enter" || form.busy) return;
            e.preventDefault();
            void submit();
          }}>
            <p className="muted mb-3 text-[12.5px]">{target.label ? `Applies to ${target.label}` : "Nothing is picked"}</p>
            {fields.map((param) => (
              <KindField
                key={param.name}
                param={param}
                value={form.values[param.name]}
                options={partners}
                disabled={form.busy}
                onChange={(value) => setForm((current) => (current === null ? current : { ...current, values: { ...current.values, [param.name]: value }, error: "" }))}
              />
            ))}
            {form.error && (
              <div role="alert" className="text-[12px]" style={{ color: "var(--danger)" }}>
                {form.error}
              </div>
            )}
          </div>
        )}
      </Dialog>
    ),
  };
}
