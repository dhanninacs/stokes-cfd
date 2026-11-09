/**
 * JSON helpers over the shared preference store (app/prefs.ts: "stokes." prefix, memory fallback when
 * localStorage is blocked). Whatever comes back from storage is untrusted text, so callers check shapes.
 */

import { readPref, writePref } from "../app/prefs";

export function readJson<T>(key: string, fallback: T): T {
  const raw = readPref(key);
  if (raw === null) return fallback;
  try {
    return JSON.parse(raw) as T;
  } catch {
    return fallback;
  }
}

export function writeJson(key: string, value: unknown): void {
  writePref(key, JSON.stringify(value));
}

/** A string array from storage, every entry checked. */
export function readStrings(key: string, limit: number): string[] {
  const value = readJson<unknown>(key, []);
  if (!Array.isArray(value)) return [];
  return value.filter((v): v is string => typeof v === "string").slice(0, limit);
}

/** Names that suggest a secret (an API key, a token, a password). Such values are never persisted or shown in clear. */
const SENSITIVE = /(key|token|secret|password|passwd|credential)/i;
export const isSensitive = (name: string): boolean => SENSITIVE.test(name);
