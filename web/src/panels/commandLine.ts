/** The console's command line: `method key=value "quoted value" positional …`, and Tab completion. */

import type { CommandSchema } from "../api/types";

export interface ParsedCommand {
  method: string;
  params: Record<string, unknown>;
}

/** Split on whitespace outside quotes and JSON brackets. Quotes stay in the tokens. */
export function tokenize(line: string): string[] {
  const tokens: string[] = [];
  let current = "";
  let quote: string | null = null;
  let depth = 0;
  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if (quote) {
      current += ch;
      if (ch === "\\" && quote === '"' && i + 1 < line.length) current += line[++i];
      else if (ch === quote) quote = null;
      continue;
    }
    if (ch === '"' || ch === "'") {
      quote = ch;
      current += ch;
      continue;
    }
    if (ch === "{" || ch === "[") depth++;
    else if ((ch === "}" || ch === "]") && depth > 0) depth--;
    if (/\s/.test(ch) && depth === 0) {
      if (current) tokens.push(current);
      current = "";
      continue;
    }
    current += ch;
  }
  if (current) tokens.push(current);
  return tokens;
}

const KEY_VALUE = /^([A-Za-z_][A-Za-z0-9_]*)=([\s\S]*)$/;

function unquote(text: string): string {
  if (text.length >= 2 && text.startsWith('"') && text.endsWith('"')) {
    try {
      const parsed: unknown = JSON.parse(text);
      if (typeof parsed === "string") return parsed;
    } catch {
      /* not valid JSON escapes: take it literally below */
    }
    return text.slice(1, -1);
  }
  if (text.length >= 2 && text.startsWith("'") && text.endsWith("'")) return text.slice(1, -1);
  return text;
}

/** Read a value according to the command's declared parameter type. The server has the last word. */
function coerce(raw: string, type: string | undefined): unknown {
  const text = raw.trim();
  switch (type) {
    case "string":
      return unquote(text);
    case "number":
    case "integer": {
      const n = Number(unquote(text));
      return unquote(text) !== "" && Number.isFinite(n) ? n : unquote(text);
    }
    case "boolean": {
      const word = unquote(text).toLowerCase();
      return word === "true" ? true : word === "false" ? false : unquote(text);
    }
    default:
      try {
        return JSON.parse(text);
      } catch {
        return unquote(text);
      }
  }
}

/**
 * `parameter.set node=/physics/parameters/U_in name=value value="3 [m/s]"`. Tokens without `key=` fill
 * the command's parameters in their declared order, so `node.get /physics/parameters/U_in` works too.
 */
export function parseCommandLine(line: string, commands: readonly CommandSchema[]): ParsedCommand | { error: string } | null {
  const [method, ...rest] = tokenize(line.trim());
  if (!method) return null;
  const props = commands.find((c) => c.name === method)?.input_schema.properties ?? {};
  const order = Object.keys(props);
  const params: Record<string, unknown> = {};
  const positional: string[] = [];
  for (const token of rest) {
    const match = KEY_VALUE.exec(token);
    if (match) params[match[1]] = coerce(match[2], props[match[1]]?.type);
    else positional.push(token);
  }
  let next = 0;
  for (const token of positional) {
    while (next < order.length && order[next] in params) next++;
    const key = order[next++];
    if (key === undefined) {
      return { error: `${method} takes ${order.length ? `${order.join(", ")}` : "no parameters"}; nothing to give ${token} to.` };
    }
    params[key] = coerce(token, props[key]?.type);
  }
  return { method, params };
}

export interface Completion {
  /** What replaces the token being completed. */
  value: string;
  label: string;
  detail?: string;
}

export interface Completions {
  /** The line up to the token being completed. */
  head: string;
  /** The token being completed. */
  token: string;
  items: Completion[];
}

/** Completions for the last token of `line`: command names first, then the command's parameter names. */
export function completionsFor(line: string, commands: readonly CommandSchema[]): Completions {
  const split = /^([\s\S]*?)(\S*)$/.exec(line);
  const head = split?.[1] ?? "";
  const token = split?.[2] ?? "";
  if (head.trim() === "") {
    const q = token.toLowerCase();
    const byPrefix = commands.filter((c) => c.name.toLowerCase().startsWith(q));
    const found = byPrefix.length > 0 || q === "" ? byPrefix : commands.filter((c) => c.name.toLowerCase().includes(q));
    return { head, token, items: found.map((c) => ({ value: c.name, label: c.name, detail: c.description })) };
  }
  const [method, ...done] = tokenize(head.trim());
  const schema = commands.find((c) => c.name === method);
  if (!schema || token.includes("=")) return { head, token, items: [] };
  const used = new Set(done.map((t) => KEY_VALUE.exec(t)?.[1]).filter((k): k is string => k !== undefined));
  const items = Object.entries(schema.input_schema.properties)
    .filter(([name]) => !used.has(name) && name.startsWith(token))
    .map(([name, p]) => ({
      value: `${name}=`,
      label: name,
      detail: `${schema.input_schema.required.includes(name) ? "required · " : ""}${p.type ?? "any"}${p.description ? ` · ${p.description}` : ""}`,
    }));
  return { head, token, items };
}

/** The longest prefix shared by every candidate (compared without regard to case). */
export function commonPrefix(values: readonly string[]): string {
  if (values.length === 0) return "";
  let prefix = values[0];
  for (const value of values.slice(1)) {
    let i = 0;
    while (i < prefix.length && i < value.length && prefix[i].toLowerCase() === value[i].toLowerCase()) i++;
    prefix = prefix.slice(0, i);
  }
  return prefix;
}
