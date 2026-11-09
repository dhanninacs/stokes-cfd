/** The command palette's parameter form: values per command, validation, and the field for each parameter type. */

import { useEffect, useId, useRef, type ReactNode } from "react";
import type { CommandParamSchema, CommandSchema } from "../api/types";
import { isSensitive, readJson, writeJson } from "./storage";

const ARGS_KEY = "palette.args";

export type FormValue = string | boolean;
export type FormValues = Record<string, FormValue>;

/** Last-used form values per command. Whatever is in storage is checked: it is untrusted text. */
function readRemembered(command: string): FormValues {
  const all = readJson<unknown>(ARGS_KEY, {});
  if (typeof all !== "object" || all === null) return {};
  const mine = (all as Record<string, unknown>)[command];
  if (typeof mine !== "object" || mine === null) return {};
  const out: FormValues = {};
  for (const [key, value] of Object.entries(mine)) if ((typeof value === "string" || typeof value === "boolean") && !isSensitive(key)) out[key] = value;
  return out;
}

export function rememberValues(command: string, values: FormValues): void {
  const all = readJson<unknown>(ARGS_KEY, {});
  const base = typeof all === "object" && all !== null && !Array.isArray(all) ? (all as Record<string, unknown>) : {};
  // A secret must not outlive the dialog it was typed into.
  const kept = Object.fromEntries(Object.entries(values).filter(([name]) => !isSensitive(name)));
  writeJson(ARGS_KEY, { ...base, [command]: kept });
}

// ---------------------------------------------------------------------------------------------- form
export function initialValues(command: CommandSchema): FormValues {
  const remembered = readRemembered(command.name);
  const values: FormValues = {};
  for (const [name, schema] of Object.entries(command.input_schema.properties)) {
    const last = remembered[name];
    if (schema.type === "boolean") values[name] = typeof last === "boolean" ? last : Boolean(schema.default);
    else values[name] = typeof last === "string" ? last : "";
  }
  return values;
}

/** Turn what was typed into the command's parameters, or say what is wrong. Optional blanks are left out. */
export function buildParams(command: CommandSchema, values: FormValues, touched: Set<string>): { params: Record<string, unknown> } | { errors: Record<string, string> } {
  const params: Record<string, unknown> = {};
  const errors: Record<string, string> = {};
  const required = new Set(command.input_schema.required);
  for (const [name, schema] of Object.entries(command.input_schema.properties)) {
    const value = values[name];
    if (schema.type === "boolean") {
      if (touched.has(name) || required.has(name)) params[name] = value === true;
      continue;
    }
    const text = typeof value === "string" ? value : "";
    if (text.trim() === "") {
      if (required.has(name)) errors[name] = "Required.";
      continue;
    }
    switch (schema.type) {
      case "string":
        params[name] = text;
        break;
      case "integer":
      case "number": {
        const n = Number(text);
        if (!Number.isFinite(n)) errors[name] = "Enter a number.";
        else if (schema.type === "integer" && !Number.isInteger(n)) errors[name] = "Enter a whole number.";
        else params[name] = n;
        break;
      }
      default:
        // object, array or any: JSON when it parses, otherwise the text itself (an expression, a quantity).
        try {
          params[name] = JSON.parse(text);
        } catch {
          params[name] = text;
        }
    }
  }
  return Object.keys(errors).length > 0 ? { errors } : { params };
}

/**
 * A boolean parameter as the shared switch (the `.switch` rules of styles.css), not a native checkbox.
 * It is built here rather than with the `Switch` component because the label carries the required
 * marker, which that component's plain-text label cannot.
 */
function BooleanParam({
  label,
  description,
  checked,
  autoFocus,
  onChange,
}: {
  label: ReactNode;
  description?: string;
  checked: boolean;
  autoFocus: boolean;
  onChange: (value: boolean) => void;
}) {
  const labelId = useId();
  const button = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (autoFocus) button.current?.focus();
  }, [autoFocus]);
  return (
    <div>
      <div className="flex items-center justify-between gap-4 text-[12.5px]">
        <span id={labelId}>{label}</span>
        <button ref={button} type="button" role="switch" aria-checked={checked} aria-labelledby={labelId} className="switch no-drag" onClick={() => onChange(!checked)} />
      </div>
      {description && <div className="muted text-[11.5px]">{description}</div>}
    </div>
  );
}

export function ParamField({
  name,
  schema,
  required,
  value,
  error,
  autoFocus,
  onChange,
}: {
  name: string;
  schema: CommandParamSchema;
  required: boolean;
  value: FormValue;
  error?: string;
  autoFocus: boolean;
  onChange: (value: FormValue) => void;
}) {
  const label = (
    <span>
      {name}
      {required && (
        <span style={{ color: "var(--danger)" }}>
          {" "}
          <span aria-hidden="true">*</span>
          <span className="sr-only">(required)</span>
        </span>
      )}
    </span>
  );
  if (schema.type === "boolean") {
    return <BooleanParam label={label} description={schema.description} checked={value === true} autoFocus={autoFocus} onChange={onChange} />;
  }
  const numeric = schema.type === "integer" || schema.type === "number";
  return (
    <label className="block">
      <div className="mb-1 flex justify-between text-[12.5px]">
        {label}
        <span className="muted">{schema.type ?? "any"}</span>
      </div>
      <input
        className={`field ${error ? "error" : ""} ${schema.type === "string" || numeric ? "" : "mono"}`}
        type={numeric ? "number" : isSensitive(name) ? "password" : "text"}
        step={schema.type === "integer" ? 1 : numeric ? "any" : undefined}
        placeholder={schema.default !== undefined && schema.default !== null ? `default: ${String(schema.default)}` : undefined}
        value={typeof value === "string" ? value : ""}
        autoFocus={autoFocus}
        autoComplete="off"
        spellCheck={false}
        aria-invalid={error !== undefined}
        onChange={(e) => onChange(e.target.value)}
      />
      {error ? (
        <div role="alert" className="mt-1 text-[11.5px]" style={{ color: "var(--danger)" }}>
          {error}
        </div>
      ) : (
        schema.description && <div className="muted mt-1 text-[11.5px]">{schema.description}</div>
      )}
    </label>
  );
}

