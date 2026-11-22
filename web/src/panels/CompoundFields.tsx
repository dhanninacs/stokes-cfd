/**
 * The compound quantity fields (ADR-0037): a `vector` as three quantity inputs in one row, each
 * taking a number in the display unit or an expression (`R / 2`), and a `quantity_map` (a sketch's
 * dimensions) as a table of named quantities. Each vector component commits on its own, as
 * `<name>.<axis>`; the map commits whole. Values arrive as the model stores them (components under
 * dotted names, `compoundFields.ts`) or, on a new operation's card, as one list or object.
 */
import { Fragment, useEffect, useId, useState, type ReactNode } from "react";
import { Plus, Trash2 } from "lucide-react";
import type { PropertySchema } from "../api/types";
import { useStore } from "../state/store";
import FieldLabel from "./FieldLabel";
import { FieldError } from "./PlainField";
import { formatNumber, prettyDisplay, prettyUnit } from "../util/units";
import { converterFor } from "./explore/format";
import { AXES, entryNameRefusal, geometryText, mapEntries, mapValue, vectorComponent, type Axis, type MapEntry } from "./compoundValues";

const BARE_NUMBER = /^[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?$/;

/** The number part of a display string ("5 mm" → "5"). */
function numberOf(display: string): string {
  const m = /^\s*(\S+)/.exec(display);
  return m ? m[1] : display;
}

interface VectorProps {
  name: string;
  schema: PropertySchema;
  values: Record<string, unknown>;
  displays?: Record<string, string>;
  onCommit: (name: string, value: unknown) => void | Promise<unknown>;
  onEdit?: (name: string) => void;
  disabled?: boolean;
  errors?: Record<string, string>;
  action?: ReactNode;
  /** The unit system shown in place of the project's (the geometry forms pass `mm`). */
  unitSystem?: string;
}

/** A vector: x, y and z, each a number in the display unit or an expression. */
export function VectorField({ name, schema, values, displays, onCommit, onEdit, disabled, errors, action, unitSystem }: VectorProps) {
  const units = useStore((s) => s.units);
  const projectSystem = useStore((s) => s.project?.display_units ?? "si");
  const system = unitSystem ?? projectSystem;
  const id = useId();
  const errorId = useId();
  const dimensionName = schema["x-dimension"] ?? "length";
  const dimension = units?.dimensions[dimensionName];
  const displayUnit = dimension ? (dimension.display[system] ?? dimension.si_unit) : "";
  const siUnit = dimension?.si_unit ?? "";

  const seedOf = (axis: Axis): { text: string; expr: boolean; shows: string | null } => {
    const seed = vectorComponent(values, displays, name, axis);
    if (seed.expr !== null) return { text: seed.expr, expr: !BARE_NUMBER.test(seed.expr), shows: seed.display };
    if (seed.display !== null) {
      // The server's display is in the project's unit: shown in the field's unit when that differs.
      const m = /^\s*(\S+)\s+(\S+)\s*$/.exec(seed.display);
      if (m !== null && m[2] !== displayUnit && seed.si !== null) {
        const to = converterFor(siUnit, displayUnit);
        if (to.converted) return { text: formatNumber(to.toDisplay(seed.si), 6), expr: false, shows: null };
      }
      return { text: numberOf(seed.display), expr: false, shows: null };
    }
    if (seed.si !== null) {
      // A card's SI number: shown in the display unit when the conversion is known, else with its unit.
      const to = converterFor(siUnit, displayUnit);
      if (displayUnit === "" || to.converted) return { text: formatNumber(to.toDisplay(seed.si), 6), expr: false, shows: null };
      return { text: `${seed.si} [${siUnit}]`, expr: false, shows: null };
    }
    return { text: "", expr: false, shows: null };
  };
  const seeds = AXES.map(seedOf);
  const seedKey = JSON.stringify(seeds);
  const [texts, setTexts] = useState(seeds.map((s) => s.text));
  const [localError, setLocalError] = useState("");
  useEffect(() => {
    setTexts(seeds.map((s) => s.text));
    setLocalError("");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [seedKey]);

  const shownError = localError || AXES.map((a) => errors?.[`${name}.${a}`]).find(Boolean) || errors?.[name] || "";

  const commit = async (i: number) => {
    const text = texts[i].trim();
    if (text === seeds[i].text.trim()) return;
    if (text === "") {
      setLocalError(`${AXES[i]} needs a value: a number or an expression`);
      return;
    }
    const payload = BARE_NUMBER.test(text) && displayUnit ? `${text} [${displayUnit}]` : text;
    try {
      await Promise.resolve(onCommit(`${name}.${AXES[i]}`, payload));
    } catch {
      // the refusal shows under the row from `errors`
    }
  };

  return (
    <div>
      <div className="mb-1 flex items-center justify-between gap-2">
        <FieldLabel htmlFor={`${id}-x`} className="text-[12.5px]" description={schema.description}>
          {schema.title ?? name}
        </FieldLabel>
        {action}
      </div>
      <div className="schema-array3">
        {AXES.map((axis, i) => (
          <Fragment key={axis}>
            <span className="muted text-[11px]">{axis}</span>
            <input
              id={`${id}-${axis}`}
              data-prop-field="quantity"
              aria-label={`${schema.title ?? name} ${axis}`}
              aria-invalid={shownError !== ""}
              aria-describedby={shownError !== "" ? errorId : undefined}
              className={`field ${seeds[i].expr ? "mono" : ""} ${shownError !== "" ? "error" : ""}`}
              title={seeds[i].shows ? `= ${prettyDisplay(seeds[i].shows ?? "")}` : undefined}
              value={texts[i]}
              disabled={disabled}
              autoComplete="off"
              spellCheck={false}
              onChange={(e) => {
                const text = e.target.value;
                setTexts((t) => t.map((v, j) => (j === i ? text : v)));
                setLocalError("");
                onEdit?.(`${name}.${axis}`);
              }}
              onBlur={() => void commit(i)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  if (texts[i].trim() !== seeds[i].text.trim()) e.stopPropagation();
                  void commit(i);
                } else if (e.key === "Escape") {
                  e.preventDefault();
                  setTexts(seeds.map((s) => s.text));
                  setLocalError("");
                }
              }}
            />
          </Fragment>
        ))}
        {displayUnit !== "" && <span className="muted text-[11px]">{prettyUnit(displayUnit)}</span>}
      </div>
      {seeds.some((s) => s.expr && s.shows) && (
        <div className="muted mt-1 text-[11.5px]" data-ws="vector-values">
          = {AXES.map((a, i) => (seeds[i].expr && seeds[i].shows ? `${a} ${prettyDisplay(seeds[i].shows ?? "")}` : null)).filter(Boolean).join(" · ")}
        </div>
      )}
      <FieldError id={errorId} message={shownError} />
    </div>
  );
}

interface MapProps {
  name: string;
  schema: PropertySchema;
  values: Record<string, unknown>;
  displays?: Record<string, string>;
  onCommit: (name: string, value: unknown) => void | Promise<unknown>;
  disabled?: boolean;
  error?: string;
  /** The geometry forms pass `mm`: a bare number is then millimetres, as in the sketcher. */
  unitSystem?: string;
}

/** A dimension's text as the model takes it: a bare number in the geometry unit when the form has one. */
export function dimensionText(text: string, unitSystem: string | undefined): string {
  const t = text.trim();
  return unitSystem === "mm" && BARE_NUMBER.test(t) ? `${t} [mm]` : t;
}

/** The row's stored value as the form writes a plain one ("40 [mm]"), or null outside the mm forms. */
function literalOf(row: MapEntry, unitSystem: string | undefined): string | null {
  return row.si === null || row.unit === undefined ? null : geometryText(row.si, row.unit, unitSystem);
}

/** What an expression comes to, in the form's unit ("= 20 mm"), else the server's display. */
function shownValue(row: MapEntry, unitSystem: string | undefined): string {
  const literal = literalOf(row, unitSystem);
  return literal === null ? prettyDisplay(row.display ?? "") : literal.replace(/\[(\w+)\]/, "$1");
}

/** A sketch's dimensions: named quantities (20 mm, 30 deg, W / 2), added, edited and removed here. */
export function DimensionsField({ name, schema, values, displays, onCommit, disabled, error, unitSystem }: MapProps) {
  const entries = mapEntries(values, displays, name, unitSystem);
  const key = JSON.stringify(entries);
  const [rows, setRows] = useState<MapEntry[]>(entries);
  const [draft, setDraft] = useState({ name: "", text: "" });
  const [localError, setLocalError] = useState("");
  const errorId = useId();
  useEffect(() => {
    setRows(entries);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  const commit = async (next: MapEntry[]) => {
    try {
      await Promise.resolve(onCommit(name, mapValue(next.map((row) => ({ ...row, text: dimensionText(row.text, unitSystem) })))));
      setLocalError("");
      return true;
    } catch {
      return false; // the refusal shows from `error`
    }
  };

  const add = async () => {
    const entry = draft.name.trim();
    const refused = entryNameRefusal(entry, rows.map((r) => r.name));
    if (refused !== null) {
      setLocalError(refused);
      return;
    }
    if (draft.text.trim() === "") {
      setLocalError(`${entry} needs a value, such as 20 [mm]`);
      return;
    }
    const next = [...rows, { name: entry, text: draft.text.trim(), display: null, si: null }];
    if (await commit(next)) setDraft({ name: "", text: "" });
  };

  const shown = localError || error || "";
  return (
    <div data-ws="sketch-dimensions-field">
      <div className="mb-1">
        <FieldLabel htmlFor={`${errorId}-new`} className="text-[12.5px]" description={schema.description}>
          {schema.title ?? name}
        </FieldLabel>
      </div>
      <div className="flex flex-col gap-1">
        {rows.map((row, i) => (
          <div key={row.name} className="flex items-center gap-1.5">
            <span className="mono w-[64px] truncate text-[12px]" title={row.name}>
              {row.name}
            </span>
            <input
              className="field mono flex-1"
              aria-label={`Dimension ${row.name}`}
              value={row.text}
              disabled={disabled}
              spellCheck={false}
              onChange={(e) => setRows((r) => r.map((x, j) => (j === i ? { ...x, text: e.target.value } : x)))}
              onBlur={() => {
                if (row.text !== entries.find((e) => e.name === row.name)?.text) void commit(rows);
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  e.stopPropagation();
                  void commit(rows);
                }
              }}
            />
            {row.display && row.text !== row.display && row.text !== literalOf(row, unitSystem) && (
              <span className="muted text-[11px]">= {shownValue(row, unitSystem)}</span>
            )}
            <button
              type="button"
              className="sketch-icon"
              aria-label={`Remove dimension ${row.name}`}
              disabled={disabled}
              onClick={() => void commit(rows.filter((_, j) => j !== i))}
            >
              <Trash2 size={13} />
            </button>
          </div>
        ))}
        <div className="flex items-center gap-1.5">
          <input
            id={`${errorId}-new`}
            className="field mono"
            // Inline: `.field` sets width 100 %, which beat the width utility and squeezed the value field.
            style={{ width: 64, flex: "none" }}
            aria-label="New dimension name"
            placeholder="W"
            value={draft.name}
            disabled={disabled}
            onChange={(e) => {
              setDraft((d) => ({ ...d, name: e.target.value }));
              setLocalError("");
            }}
          />
          <input
            className="field mono min-w-0 flex-1"
            aria-label="New dimension value"
            placeholder={unitSystem === "mm" ? "20 (mm) or W / 2" : "20 [mm]"}
            value={draft.text}
            disabled={disabled}
            onChange={(e) => {
              setDraft((d) => ({ ...d, text: e.target.value }));
              setLocalError("");
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                e.stopPropagation();
                void add();
              }
            }}
          />
          <button type="button" className="sketch-icon" aria-label="Add dimension" disabled={disabled} onClick={() => void add()}>
            <Plus size={14} />
          </button>
        </div>
      </div>
      <FieldError id={errorId} message={shown} />
    </div>
  );
}
