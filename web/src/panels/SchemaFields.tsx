/**
 * The parameters of any node type as the Properties panel draws them, with what the value panel
 * and the operation cards add on top (ADR-0022 decisions 7.1 and 7.3): an `include` filter, a
 * `ref` parameter as a select over the nodes of its type, and a three-number `array` as three
 * fields. One component renders operations, conditions and properties.
 *
 * The fields are presentational: they show `values` (a quantity parameter as `QuantityValue`)
 * and `displays`, and commit through `onCommit` (Enter or blur on a field, change on a select or
 * switch). A refused value shows under the field from `errors`, the server's message; a new
 * value arrives as changed `values`. The quantity field converts for display the way the panel's
 * old field did: the value in the chip's display unit, a bare number read in that unit, and SI
 * written back.
 *
 * Two callers' additions (T-029): `jsonText` says a `json` value in words ("12 faces of 2 bodies")
 * and the field shows that line instead of its JSON block, and `labelAction` puts a control beside
 * a field's label (the operation card's "Pick in view" on `seed`). Both are optional; the
 * properties panel passes neither.
 *
 * Importers must write `./SchemaFields.tsx` with the extension: on Windows `./SchemaFields` resolves to the helpers' `schemaFields.ts` (T-029 and T-044 import it).
 */
import { Fragment, useEffect, useId, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Check, ChevronsUpDown, TriangleAlert } from "lucide-react";
import type { DisplayValue, NodeStatus, NodeSummary, ParamValue, PropertySchema } from "../api/types";
import { isQuantity } from "../api/types";
import { useStore } from "../state/store";
import { Tooltip } from "../components/Tooltip";
import { Switch } from "../components/ui";
import FieldLabel from "./FieldLabel";
import { FieldError } from "./PlainField";
import { loadConversions } from "./explore/conversions";
import { converterFor, hasConversion } from "./explore/format";
import { formatNumber, prettyDisplay, prettyUnit } from "../util/units";
import { FOCUS_TREE_EVENT } from "./treeKeyboard";
import { enterStaysInField, fieldKindOf, nodesOfType, numberRefusal, quantityRefusal, vectorRefusal } from "./schemaFields";

export interface SchemaFieldsProps {
  properties: Record<string, PropertySchema>;
  values: Record<string, unknown>;
  displays?: Record<string, DisplayValue>;
  /** Commits a value; a refusal (the server's refusal) comes back as a rejected promise. */
  onCommit(name: string, value: unknown): void | Promise<unknown>;
  /** Called on every text change and on an Esc-revert, so the panel can drop the field's error. */
  onEdit?(name: string): void;
  /** The node's status: a `failed` node's expression keeps its last value and says so. */
  status?: NodeStatus;
  include?: (name: string) => boolean;
  nodes?: NodeSummary | null;
  disabled?: boolean;
  errors?: Record<string, string>;
  /**
   * A `json` parameter in words ("12 faces of 2 bodies"), when the caller can say it: the field
   * shows that line instead of its JSON block. Null (or no function) keeps the block. The operation
   * card uses it for a selection's members (T-029).
   */
  jsonText?(name: string, value: unknown): string | null;
  /** A control drawn beside a field's label; the operation card puts "Pick in view" on `seed`. */
  labelAction?(name: string): ReactNode;
  /**
   * Fields whose stored value is NOT the answer, with the reason to show instead.
   *
   * **The owner, via Claude (verdict leftover 3)**: a material property whose method computes its
   * value (`ideal_gas`, `polynomial`, a table…) stores a placeholder, and the page showed it as a
   * measurement — measured as **"density 0 kg/m³"** for air. The caller knows which fields those are,
   * because it knows the method; this renders the reason in place of the number, and the field keeps
   * its editor so a user can still change the method.
   */
  notStored?(name: string): string | null;
}

const BARE_NUMBER = /^[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?$/;
const FLASH_MS = 400;

type Parsed = { ok: true; value: unknown } | { ok: false; error: string };

/** Six significant digits (the server's display precision), no negative zero. */
function fmt(value: number): string {
  const text = formatNumber(value, 6);
  return text === "-0" ? "0" : text;
}

/** "313.15 K" → { num: "313.15", unit: "K" }; a unit-less display keeps its whole text as the number. */
function splitDisplay(display: string): { num: string; unit: string } {
  const m = /^(\S+)\s+(.+)$/.exec(display.trim());
  return m ? { num: m[1], unit: m[2] } : { num: display.trim(), unit: "" };
}

/**
 * The affine conversion from a dimension's SI unit to `unit` (display = si * factor + offset),
 * read off the shared Explore conversion cache; null until the pair has been fetched. Identity
 * when both names agree.
 */
function affine(siUnit: string, unit: string): { factor: number; offset: number } | null {
  if (!siUnit || !unit) return null;
  if (siUnit === unit) return { factor: 1, offset: 0 };
  const c = converterFor(siUnit, unit);
  if (!c.converted) return null;
  const offset = c.toDisplay(0);
  const factor = c.toDisplay(1) - offset;
  return Number.isFinite(factor) && factor !== 0 ? { factor, offset } : null;
}

/**
 * Fetches the SI-to-unit conversions a field shows, once per pair, through the shared Explore
 * cache (`units.convert`). Hands back a tick that changes when a wanted pair arrives, so the
 * field re-seeds its text in display units; a pair the server cannot convert keeps its SI text.
 */
function useConversionTick(siUnit: string, units: string[]): number {
  const client = useStore((s) => s.client);
  const [tick, setTick] = useState(0);
  const key = [siUnit, ...units].join("|");
  useEffect(() => {
    if (!siUnit || !client) return;
    const missing = units.filter((u) => u !== "" && u !== siUnit && !hasConversion(siUnit, u));
    if (missing.length === 0) return;
    let live = true;
    void loadConversions(client, missing.map((u) => ({ si: siUnit, display: u }))).then(() => {
      if (live) setTick((t) => t + 1);
    });
    return () => {
      live = false;
    };
    // The content of `units` is carried in `key`; `missing` is rebuilt from it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, client]);
  return tick;
}

/** A `json` parameter: read-only, formatted text. It is structured data (a selection's members),
 * changed through the commands that own it, not by typing into a field. A string that is itself
 * JSON (the members text a selection stores) is shown parsed, so it reads as the data it is. A
 * caller that can say the value in words (`jsonText`) gets that line instead of the block. */
/**
 * A row for a value the page must not print.
 *
 * **A dash and the reason**, in the shape the fields around it already use, so the page still reads as
 * a list of properties: the label, the dash where the number would be, and the reason where a field's
 * help already goes.
 */
function NotStoredField({ name, schema, reason }: { name: string; schema: PropertySchema; reason: string }) {
  return (
    <label className="field" data-field={name} data-not-stored="true">
      <span className="field-label">{schema.title ?? name}</span>
      <span className="field-value muted" title={reason}>
        —
      </span>
      <span className="field-help muted text-[11.5px]">{reason}</span>
    </label>
  );
}

function JsonField({ name, value, text, action }: { name: string; value: unknown; text?: string | null; action?: ReactNode }) {
  return (
    <div>
      <div className="mb-1 flex items-center justify-between gap-2 text-[12.5px]">
        <span>{name}</span>
        {action}
      </div>
      {text != null ? (
        <div
          data-prop-field="json"
          aria-label={name}
          className="rounded-md px-2 py-1.5 text-[12px]"
          style={{ background: "var(--bg-elev)", border: "1px solid var(--panel-border)" }}
        >
          {text}
        </div>
      ) : (
        <pre
          data-prop-field="json"
          aria-label={name}
          className="max-h-48 overflow-auto rounded-md px-2 py-1.5 text-[11.5px]"
          style={{ background: "var(--bg-elev)", border: "1px solid var(--panel-border)", fontFamily: "var(--font-mono)" }}
        >
          {formatJson(value)}
        </pre>
      )}
    </div>
  );
}

/** JSON text for a `json` parameter's value; a string holding JSON shows as that JSON. */
function formatJson(value: unknown): string {
  let shown: unknown = value ?? null;
  if (typeof shown === "string") {
    try {
      shown = JSON.parse(shown);
    } catch {
      // not JSON text: show the string as it is
    }
  }
  return JSON.stringify(shown, null, 2);
}

/**
 * A quantity parameter, as the Properties panel drew it: the value in a display unit with a unit
 * chip that cycles si → mm → imperial, or the expression in mono with an `ƒ` badge and its
 * evaluated value below. Enter applies, Esc reverts to the last applied text; a bare number is
 * read in the unit the chip shows and written back as that.
 */
function QuantityField({
  name,
  schema,
  raw,
  display,
  dimensionName,
  status,
  onCommit,
  onEdit,
  disabled,
  error,
  action,
}: {
  name: string;
  schema: PropertySchema;
  raw: unknown;
  display: string;
  dimensionName: string;
  status?: NodeStatus;
  onCommit: (name: string, value: unknown) => void | Promise<unknown>;
  onEdit?: (name: string) => void;
  disabled?: boolean;
  error?: string;
  /** A control beside the label (the operation card's "Pick in view"). */
  action?: ReactNode;
}) {
  const units = useStore((s) => s.units);
  const system = useStore((s) => s.project?.display_units ?? "si");
  const inputId = useId();
  const hintId = useId();

  const dimension = units?.dimensions[dimensionName];
  const siUnit = dimension?.si_unit ?? "";

  // The distinct display units of the dimension in unit-system order, e.g. velocity: m/s, ft/s.
  const choices = useMemo(() => {
    if (!units || !dimension) return [];
    const list: string[] = [];
    for (const sys of units.unit_systems) {
      const u = dimension.display[sys];
      if (u !== undefined && !list.includes(u)) list.push(u);
    }
    return list.length ? list : [dimension.si_unit];
  }, [units, dimension]);
  const projectUnit = dimension ? (dimension.display[system] ?? dimension.si_unit) : "";
  const [override, setOverride] = useState<string | null>(null);
  useEffect(() => {
    setOverride(null); // the project's unit system or the dimension changed: follow it again
  }, [system, dimensionName]);
  const unit = override !== null && choices.includes(override) ? override : projectUnit;
  const nextUnit = choices[(choices.indexOf(unit) + 1) % Math.max(choices.length, 1)] ?? unit;

  const rawValue = raw as ParamValue;
  const seedExpr = isQuantity(rawValue) ? (rawValue.expr ?? undefined) : undefined;
  const { num: seedNum, unit: seedUnit } = splitDisplay(display);
  const tick = useConversionTick(siUnit, [seedUnit, unit]);

  // The seed in the chip's unit. The summary's display is in the project's display unit, so
  // convert across the SI unit when the chip shows another; until a pair has been fetched the
  // text stays as it is, the way a column the table never had keeps its SI value.
  let applied = seedExpr ?? seedNum;
  let displayInChip = display;
  if (!seedExpr && unit !== "" && unit !== seedUnit) {
    const from = affine(siUnit, seedUnit);
    const to = affine(siUnit, unit);
    const n = Number(seedNum);
    if (from && to && Number.isFinite(n)) {
      const inChip = ((n - from.offset) / from.factor) * to.factor + to.offset;
      applied = fmt(inChip);
      displayInChip = `${fmt(inChip)} ${unit}`;
    }
  }

  const [f, setF] = useState({ text: applied, applied, display: displayInChip, isExpr: seedExpr !== undefined, flash: false });
  const [localError, setLocalError] = useState("");
  const shownError = localError || error || "";
  const dirty = f.text !== f.applied;

  // The text last committed successfully: a re-seed may replace it, so a value typed with its own
  // units is normalised the way the panel's old field normalised it (typed "5 m/s" → 16.4042).
  const lastSent = useRef<string | null>(null);

  // The model or the chip's unit changed: re-seed the applied text and display, keeping unsaved
  // typing unless the field is clean. The text just committed is clean too: the model echoes the
  // normalised value back.
  const seedKey = JSON.stringify([raw, display, unit, siUnit, seedUnit, tick]);
  useEffect(() => {
    const sent = lastSent.current;
    lastSent.current = null;
    setF((s) => ({ ...s, applied, display: displayInChip, isExpr: seedExpr !== undefined, text: s.text === s.applied || s.text === sent ? applied : s.text }));
    setLocalError("");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [seedKey]);

  const inFlight = useRef(false);
  const submit = async (): Promise<boolean> => {
    if (!dirty) return true;
    if (inFlight.current) return false;
    const value = f.text.trim();
    const refused = quantityRefusal(value);
    if (refused !== null) {
      setLocalError(refused);
      return false;
    }
    // A bare number means "in the unit shown on the chip"; anything else carries its own units.
    const payload = BARE_NUMBER.test(value) && unit ? `${value} [${unit}]` : value;
    inFlight.current = true;
    try {
      await Promise.resolve(onCommit(name, payload));
    } catch {
      // a refusal shows under the field from `error`; the field keeps what it shows
      inFlight.current = false;
      return false;
    }
    inFlight.current = false;
    lastSent.current = value;
    setF((s) => ({ ...s, flash: true }));
    window.setTimeout(() => setF((s) => ({ ...s, flash: false })), FLASH_MS);
    return true;
  };

  // Switching the unit converts what is shown; unsaved typing is applied first, in the unit it was typed in.
  const cycleUnit = async () => {
    if (choices.length < 2) return;
    if (dirty && !(await submit())) return;
    setOverride(nextUnit);
  };

  // The chip sits inside the input's right edge; keep the text clear of it whatever the unit's length.
  const chipRef = useRef<HTMLSpanElement>(null);
  const [chipWidth, setChipWidth] = useState(0);
  useLayoutEffect(() => {
    setChipWidth(chipRef.current?.offsetWidth ?? 0);
  }, [unit, choices.length]);

  const unitText = prettyUnit(unit);
  const cyclable = choices.length > 1;
  const hasHint = f.isExpr || shownError !== "";
  return (
    <div>
      <div className="mb-1 flex items-baseline justify-between gap-2">
        <FieldLabel htmlFor={inputId} className="flex items-baseline gap-1.5 text-[12.5px]" description={schema.description}>
          {schema.title ?? name}
          {f.isExpr && (
            <span className="mono rounded px-1 font-semibold" style={{ background: "var(--accent-soft)", color: "var(--accent-ink)" }}>
              <span className="sr-only">An expression: </span>
              <span aria-hidden="true">ƒ</span>
            </span>
          )}
        </FieldLabel>
        {action}
        <span className="muted text-[11px]">{dimensionName}</span>
      </div>
      <div className="relative">
        <input
          id={inputId}
          data-prop-field="quantity"
          className={`field ${f.isExpr ? "mono" : ""} ${shownError ? "error" : f.flash ? "flash" : ""}`}
          style={{ paddingRight: unitText ? chipWidth + 14 : undefined }}
          value={f.text}
          disabled={disabled}
          autoComplete="off"
          spellCheck={false}
          aria-invalid={shownError !== ""}
          data-refused={localError !== "" ? "true" : undefined}
          aria-describedby={hasHint ? hintId : undefined}
          placeholder={`value in ${unitText || "SI"} or an expression`}
          onChange={(e) => {
            const text = e.target.value;
            setF((s) => ({ ...s, text }));
            setLocalError("");
            onEdit?.(name);
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              // A refused value stays in the field: the same Enter must not reach a card that would commit the old one.
              if (enterStaysInField(dirty, quantityRefusal(f.text))) e.stopPropagation();
              void submit();
            } else if (e.key === "Escape") {
              e.preventDefault();
              if (dirty || shownError !== "") {
                setF((s) => ({ ...s, text: s.applied }));
                setLocalError("");
                onEdit?.(name); // the first Esc reverts and clears; the second hands focus to the tree
              } else {
                e.currentTarget.blur();
                window.dispatchEvent(new CustomEvent(FOCUS_TREE_EVENT));
              }
            }
          }}
          onBlur={() => {
            if (dirty) void submit();
          }}
        />
        {unitText && (
          <span ref={chipRef} className="absolute top-1/2 right-1.5 -translate-y-1/2">
            {cyclable ? (
              <Tooltip label={`Display unit ${unitText}. Click for ${prettyUnit(nextUnit)}.`} side="top" describe={false}>
                <button
                  type="button"
                  className="h-[22px] cursor-pointer rounded-md border px-2 text-[11.5px] transition-colors hover:text-[var(--text)] motion-reduce:transition-none"
                  style={{ background: "var(--hover)", borderColor: "var(--panel-border)", color: "var(--text-muted)" }}
                  aria-label={`Display unit ${unitText}. Switch to ${prettyUnit(nextUnit)}.`}
                  onMouseDown={(e) => e.preventDefault()} // keep the caret in the input
                  onClick={() => void cycleUnit()}
                >
                  {unitText}
                </button>
              </Tooltip>
            ) : (
              <span className="muted pointer-events-none mr-1.5 text-[11.5px]">{unitText}</span>
            )}
          </span>
        )}
      </div>
      {shownError ? (
        <div id={hintId} role="alert" className="mt-1 text-[12px]" style={{ color: "var(--danger)" }}>
          {shownError}
        </div>
      ) : f.isExpr ? (
        <div id={hintId} className="muted mt-1 flex items-center gap-1 text-[12px]">
          {dirty ? (
            <span>Enter to apply · Esc to revert</span>
          ) : (
            <>
              {/* A failed expression keeps its last value: say so rather than show a green tick. */}
              {status === "failed" ? <TriangleAlert size={12} style={{ color: "var(--warn)" }} /> : <Check size={12} style={{ color: "var(--ok)" }} />}
              <span>
                = {prettyDisplay(f.display)}
                {status === "failed" && <span> (last value; the expression fails)</span>}
              </span>
            </>
          )}
        </div>
      ) : null}
    </div>
  );
}

/**
 * The single-line input of a number, integer or string parameter, with the panel's edit
 * contract: Enter applies, Esc reverts to the last applied text (and, when there is nothing to
 * revert, hands focus back to the tree), blur applies pending edits, and a refusal from the
 * server shows under the field from `error`.
 */
function PlainCommit({
  name,
  model,
  parse,
  onCommit,
  onEdit,
  disabled,
  title,
  inputId,
  errorId,
  error,
  className = "",
}: {
  name: string;
  /** The applied text from the model. */
  model: string;
  /** Turns the text into what to send, or an error message. */
  parse?: (text: string) => Parsed;
  onCommit: (name: string, value: unknown) => void | Promise<unknown>;
  onEdit?: (name: string) => void;
  disabled?: boolean;
  title: string;
  inputId: string;
  errorId: string;
  error?: string;
  className?: string;
}) {
  const [text, setText] = useState(model);
  const [localError, setLocalError] = useState("");
  const sent = useRef<string | null>(null); // text already sent, until the model echoes it back
  useEffect(() => {
    setText(model); // the model changed (another client, undo): show it
    setLocalError("");
    sent.current = null;
  }, [model]);
  const shownError = localError || error || "";

  const check = (): Parsed => (parse ? parse(text) : { ok: true, value: text });
  const apply = async () => {
    if (text === model || text === sent.current) return;
    const parsed = check();
    if (!parsed.ok) {
      setLocalError(parsed.error);
      return;
    }
    void Promise.resolve(onCommit(name, parsed.value)).then(
      () => {
        sent.current = text;
      },
      () => {
        // the refusal shows under the field from `error`
      },
    );
  };

  return (
    <>
      <div className="relative">
        <input
          id={inputId}
          data-prop-field="plain"
          aria-label={title}
          aria-invalid={shownError !== ""}
          data-refused={localError !== "" ? "true" : undefined}
          aria-describedby={shownError ? errorId : undefined}
          className={`field ${shownError ? "error" : ""} ${className}`}
          value={text}
          disabled={disabled}
          autoComplete="off"
          spellCheck={false}
          onChange={(e) => {
            setText(e.target.value);
            setLocalError("");
            onEdit?.(name);
          }}
          onBlur={() => void apply()}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              const parsed = check();
              if (enterStaysInField(text !== model && text !== sent.current, parsed.ok ? null : parsed.error)) e.stopPropagation();
              void apply();
            } else if (e.key === "Escape") {
              e.preventDefault();
              if (text !== model || shownError !== "") {
                setText(model);
                setLocalError("");
                onEdit?.(name); // the first Esc reverts and clears; the second hands focus to the tree
              } else {
                e.currentTarget.blur();
                window.dispatchEvent(new CustomEvent(FOCUS_TREE_EVENT));
              }
            }
          }}
        />
      </div>
      <FieldError id={errorId} message={shownError} />
    </>
  );
}

/** A number, integer or string parameter: the label and the commit input above. */
function PlainInput({
  name,
  schema,
  kind,
  value,
  onCommit,
  onEdit,
  disabled,
  error,
  action,
}: {
  name: string;
  schema: PropertySchema;
  kind: "number" | "int" | "string";
  value: unknown;
  onCommit: (name: string, value: unknown) => void | Promise<unknown>;
  onEdit?: (name: string) => void;
  disabled?: boolean;
  error?: string;
  action?: ReactNode;
}) {
  const id = useId();
  const errorId = useId();
  const parse =
    kind === "number"
      ? (text: string): Parsed => {
          const refused = numberRefusal(text, false);
          return refused !== null ? { ok: false, error: refused } : { ok: true, value: Number(text.trim()) };
        }
      : kind === "int"
        ? (text: string): Parsed => {
            const refused = numberRefusal(text, true);
            return refused !== null ? { ok: false, error: refused } : { ok: true, value: Number(text.trim()) };
          }
        : undefined;
  return (
    <div>
      <div className="mb-1 flex items-center justify-between gap-2">
        <FieldLabel htmlFor={id} className="text-[12.5px]" description={schema.description}>
          {schema.title ?? name}
        </FieldLabel>
        {action}
      </div>
      <PlainCommit
        name={name}
        model={value == null ? "" : String(value)}
        parse={parse}
        onCommit={onCommit}
        onEdit={onEdit}
        disabled={disabled}
        title={schema.title ?? name}
        inputId={id}
        errorId={errorId}
        error={error}
      />
    </div>
  );
}

/** An enum parameter: the panel's select. A choice that is not in the schema shows as-is, as the old field did. */
function EnumField({
  name,
  schema,
  value,
  onCommit,
  disabled,
  error,
  action,
}: {
  name: string;
  schema: PropertySchema;
  value: unknown;
  onCommit: (name: string, value: unknown) => void | Promise<unknown>;
  disabled?: boolean;
  error?: string;
  action?: ReactNode;
}) {
  const id = useId();
  const errorId = useId();
  const shown = String(value ?? "");
  // The choice is shown at once while the command runs, and restored if the server refuses it.
  const [choice, setChoice] = useState(shown);
  useEffect(() => setChoice(shown), [shown]);
  const listed = (schema.enum ?? []).some((option) => option === choice);
  return (
    <div>
      <div className="mb-1 flex items-center justify-between gap-2">
        <FieldLabel htmlFor={id} className="text-[12.5px]" description={schema.description}>
          {schema.title ?? name}
        </FieldLabel>
        {action}
      </div>
      <div className="select-wrap">
        <select
          id={id}
          data-prop-field="plain"
          className={`field ${error ? "error" : ""}`}
          value={choice}
          disabled={disabled}
          aria-invalid={Boolean(error)}
          aria-describedby={error ? errorId : undefined}
          onChange={(e) => {
            const next = e.target.value;
            setChoice(next);
            void Promise.resolve(onCommit(name, next)).catch(() => setChoice(shown));
          }}
        >
          {(schema.enum ?? []).map((option) => (
            <option key={option} value={option}>
              {option}
            </option>
          ))}
          {choice !== "" && !listed && <option value={choice} disabled>{choice}</option>}
        </select>
        <ChevronsUpDown size={13} className="select-chevron" aria-hidden="true" />
      </div>
      <FieldError id={errorId} message={error ?? ""} />
    </div>
  );
}

/** A boolean parameter: the shared switch. */
function BoolField({
  name,
  schema,
  value,
  onCommit,
  disabled,
  error,
  action,
}: {
  name: string;
  schema: PropertySchema;
  value: unknown;
  onCommit: (name: string, value: unknown) => void | Promise<unknown>;
  disabled?: boolean;
  error?: string;
  action?: ReactNode;
}) {
  const errorId = useId();
  const shown = Boolean(value);
  // The switch is thrown at once while the command runs, and restored if the server refuses it.
  const [checked, setChecked] = useState(shown);
  useEffect(() => setChecked(shown), [shown]);
  return (
    <div data-prop-switch>
      {action && <div className="mb-1 flex justify-end">{action}</div>}
      <Switch
        checked={checked}
        label={schema.title ?? name}
        description={schema.description}
        disabled={disabled}
        onChange={(next) => {
          if (disabled) return;
          setChecked(next);
          void Promise.resolve(onCommit(name, next)).catch(() => setChecked(shown));
        }}
      />
      <FieldError id={errorId} message={error ?? ""} />
    </div>
  );
}

/**
 * A `ref` parameter: a select over the nodes of its type, by name with the path as the tooltip,
 * plus an empty "none" that writes null — except on a required ref that already holds a value,
 * which the server would refuse to clear.
 */
function RefField({
  name,
  schema,
  value,
  nodes,
  onCommit,
  disabled,
  error,
  action,
}: {
  name: string;
  schema: PropertySchema;
  value: unknown;
  nodes: NodeSummary | null;
  onCommit: (name: string, value: unknown) => void | Promise<unknown>;
  disabled?: boolean;
  error?: string;
  action?: ReactNode;
}) {
  const id = useId();
  const errorId = useId();
  const options = useMemo(() => nodesOfType(nodes, schema["x-ref-type"] ?? ""), [nodes, schema]);
  const raw = typeof value === "string" ? value : "";
  const listed = options.some((o) => o.id === raw);
  // An empty required ref still offers "none": with no option selected the select would show the
  // first node as if it were chosen.
  const canClear = !schema["x-required"] || raw === "";
  return (
    <div>
      <div className="mb-1 flex items-center justify-between gap-2">
        <FieldLabel htmlFor={id} className="text-[12.5px]" description={schema.description}>
          {schema.title ?? name}
        </FieldLabel>
        {action}
      </div>
      <div className="select-wrap">
        <select
          id={id}
          data-prop-field="plain"
          className={`field ${error ? "error" : ""}`}
          value={raw}
          disabled={disabled}
          aria-invalid={Boolean(error)}
          aria-describedby={error ? errorId : undefined}
          onChange={(e) => {
            const next = e.target.value === "" ? null : e.target.value;
            void Promise.resolve(onCommit(name, next)).catch(() => {
              // the refusal shows under the select from `error`
            });
          }}
        >
          {canClear && <option value="">None</option>}
          {options.map((o) => (
            <option key={o.id} value={o.id} title={o.path}>
              {o.name}
            </option>
          ))}
          {raw !== "" && !listed && <option value={raw} disabled>{raw}</option>}
        </select>
        <ChevronsUpDown size={13} className="select-chevron" aria-hidden="true" />
      </div>
      <FieldError id={errorId} message={error ?? ""} />
    </div>
  );
}

/**
 * A three-number `array` (the `array3` kind): x, y, z as three fields in one row, the
 * dimension's display unit after the row when it has one. The numbers show in the display unit
 * and write back in SI, converted through `units.convert` (the shared Explore cache).
 */
function Array3Field({
  name,
  schema,
  value,
  onCommit,
  onEdit,
  disabled,
  error,
  action,
}: {
  name: string;
  schema: PropertySchema;
  value: unknown;
  onCommit: (name: string, value: unknown) => void | Promise<unknown>;
  onEdit?: (name: string) => void;
  disabled?: boolean;
  error?: string;
  /** A control beside the label (the operation card's "Pick in view" on `seed`). */
  action?: ReactNode;
}) {
  const units = useStore((s) => s.units);
  const system = useStore((s) => s.project?.display_units ?? "si");
  const id = useId();
  const errorId = useId();
  const [localError, setLocalError] = useState("");

  const dimensionName = schema["x-dimension"] ?? "";
  const dimension = dimensionName !== "" ? units?.dimensions[dimensionName] : undefined;
  const siUnit = dimension?.si_unit ?? "";
  const displayUnit = dimension ? (dimension.display[system] ?? dimension.si_unit) : "";
  // Re-renders when a wanted conversion arrives; the re-seeded `seed` carries it on.
  useConversionTick(siUnit, displayUnit !== "" ? [displayUnit] : []);

  const siOf = (i: number): number | undefined => {
    const v = Array.isArray(value) ? value[i] : undefined;
    return typeof v === "number" && Number.isFinite(v) ? v : undefined;
  };

  const toText = (si: number | undefined): string => {
    if (si === undefined) return "";
    const aff = displayUnit !== "" ? affine(siUnit, displayUnit) : null;
    const n = aff !== null ? si * aff.factor + aff.offset : si;
    return fmt(n);
  };

  const seed = [toText(siOf(0)), toText(siOf(1)), toText(siOf(2))];
  const [texts, setTexts] = useState<string[]>(seed);
  const seedKey = JSON.stringify(seed);
  const sent = useRef<string | null>(null); // text already sent, until the model echoes it back
  useEffect(() => {
    setTexts(seed);
    setLocalError("");
    sent.current = null;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [seedKey]);

  const shownError = localError || error || "";

  const apply = async () => {
    const key = JSON.stringify(texts);
    if (key === seedKey || key === sent.current) return; // nothing changed
    const nums = texts.map((t) => Number(t.trim()));
    const refused = vectorRefusal(texts);
    if (refused !== null) {
      setLocalError(refused);
      return;
    }
    const si = nums.map((n) => {
      const aff = displayUnit !== "" ? affine(siUnit, displayUnit) : null;
      return aff !== null ? (n - aff.offset) / aff.factor : n;
    });
    // No commit when every component reads back to the model's value.
    const same = [siOf(0), siOf(1), siOf(2)].every((m, i) => m !== undefined && Math.abs(m - si[i]) <= 1e-9 * Math.max(1, Math.abs(si[i])));
    if (same) return;
    void Promise.resolve(onCommit(name, si)).then(
      () => {
        sent.current = key; // the model's new value arrives as changed `values`
      },
      () => {
        // the refusal shows under the row from `error`
      },
    );
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
        {(["x", "y", "z"] as const).map((axis, i) => (
          <Fragment key={axis}>
            <span className="muted text-[11px]">{axis}</span>
            <input
              id={`${id}-${axis}`}
              data-prop-field="plain"
              aria-label={`${schema.title ?? name} ${axis}`}
              aria-invalid={shownError !== ""}
              data-refused={localError !== "" ? "true" : undefined}
              aria-describedby={shownError !== "" ? errorId : undefined}
              className={`field ${shownError !== "" ? "error" : ""}`}
              value={texts[i]}
              disabled={disabled}
              autoComplete="off"
              spellCheck={false}
              onChange={(e) => {
                setTexts((t) => t.map((v, j) => (j === i ? e.target.value : v)));
                setLocalError("");
                onEdit?.(name);
              }}
              onBlur={() => void apply()}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  const key = JSON.stringify(texts);
                  if (enterStaysInField(key !== seedKey && key !== sent.current, vectorRefusal(texts))) e.stopPropagation();
                  void apply();
                } else if (e.key === "Escape") {
                  e.preventDefault();
                  if (JSON.stringify(texts) !== seedKey || shownError !== "") {
                    setTexts(seed);
                    setLocalError("");
                    onEdit?.(name); // the first Esc reverts and clears; the second hands focus to the tree
                  } else {
                    e.currentTarget.blur();
                    window.dispatchEvent(new CustomEvent(FOCUS_TREE_EVENT));
                  }
                }
              }}
            />
          </Fragment>
        ))}
        {displayUnit !== "" && <span className="muted text-[11px]">{prettyUnit(displayUnit)}</span>}
      </div>
      <FieldError id={errorId} message={shownError} />
    </div>
  );
}

export default function SchemaFields({
  properties,
  values,
  displays,
  onCommit,
  onEdit,
  status,
  include,
  nodes,
  disabled,
  errors,
  jsonText,
  labelAction,
  notStored,
}: SchemaFieldsProps) {
  // The tree root for the ref selects: the store's tree when the caller does not pass one.
  const tree = useStore((s) => s.tree);
  const root = nodes !== undefined ? nodes : tree;

  return (
    <>
      {Object.entries(properties)
        // **`x-hidden` properties are not drawn** (claude-win2's 2000 item 2): the model keeps the
        // parameter and the API keeps accepting it, but the UI expresses it somewhere else - a part's
        // `visible` is the tree row's eye, which is a view filter, so a second toggle here would save
        // what the eye deliberately does not.
        .filter(([, pschema]) => pschema["x-hidden"] !== true)
        .filter(([name]) => (include ? include(name) : true))
        .map(([name, pschema]) => {
          const kind = fieldKindOf(pschema);
          const error = errors?.[name];
          const action = labelAction?.(name) ?? undefined;
          // **A value the caller says is NOT the answer** (verdict leftover 3): the field shows the
          // reason instead of the number, and offers no editor - typing over a placeholder would
          // change nothing the model reads. The parameter stays in the model and in the API.
          const reason = notStored?.(name) ?? null;
          if (reason !== null) return <NotStoredField key={name} name={name} schema={pschema} reason={reason} />;
          switch (kind) {
            case "quantity": {
              const dimensionName =
                pschema["x-dimension"] === "dynamic" ? String(values["dimension"] ?? "dimensionless") : pschema["x-dimension"] ?? "dimensionless";
              return (
                <QuantityField
                  key={name}
                  name={name}
                  schema={pschema}
                  raw={values[name]}
                  display={displays?.[name] ?? ""}
                  dimensionName={dimensionName}
                  status={status}
                  onCommit={onCommit}
                  onEdit={onEdit}
                  disabled={disabled}
                  error={error}
                  action={action}
                />
              );
            }
            case "ref":
              return <RefField key={name} name={name} schema={pschema} value={values[name]} nodes={root} onCommit={onCommit} disabled={disabled} error={error} action={action} />;
            case "array3":
              return <Array3Field key={name} name={name} schema={pschema} value={values[name]} onCommit={onCommit} onEdit={onEdit} disabled={disabled} error={error} action={action} />;
            case "enum":
              return <EnumField key={name} name={name} schema={pschema} value={values[name]} onCommit={onCommit} disabled={disabled} error={error} action={action} />;
            case "bool":
              return <BoolField key={name} name={name} schema={pschema} value={values[name]} onCommit={onCommit} disabled={disabled} error={error} action={action} />;
            case "array":
            case "json":
              return <JsonField key={name} name={name} value={values[name]} text={jsonText?.(name, values[name]) ?? null} action={action} />;
            default:
              return (
                <PlainInput
                  key={name}
                  name={name}
                  schema={pschema}
                  kind={kind}
                  value={values[name]}
                  onCommit={onCommit}
                  onEdit={onEdit}
                  disabled={disabled}
                  error={error}
                  action={action}
                />
              );
          }
        })}
    </>
  );
}
