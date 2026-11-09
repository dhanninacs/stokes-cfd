/**
 * The pure half of "import a CAD file from the browser" (T-219 A): which files the picker offers, whether
 * a name is one of ours, how the progress and the failures read, and which of a dropped set of files is
 * the one to take. No React, no store, no DOM: the XHR is `upload.ts`, the buttons are `ProjectMenu.tsx`
 * and `ImportDrop.tsx`, and every rule that decides something is here so Node can test it.
 *
 * The contract is the server's `POST /api/upload?name=<file>` with the token in the `X-Stokes-Token`
 * header (never in the URL, where devtools and a HAR file would keep it): the raw file bytes are the body;
 * the reply is `{path, size, sha256}`; 415 is a name whose suffix is not a geometry format, 413 a file
 * over the server's limit, 401 a token the server does not know. The limit is `max_upload_bytes` in the
 * `geometry.formats` reply; a server that does not say takes 1 GiB.
 */

/** The header that carries the token. */
export const TOKEN_HEADER = "X-Stokes-Token";

/** What the server takes when its `geometry.formats` reply names no limit: 1 GiB. */
export const DEFAULT_MAX_UPLOAD_BYTES = 1024 ** 3;

/** The limit a `geometry.formats` reply names (`max_upload_bytes`), or the default for a reply that has none or a bad one. */
export function maxUploadBytes(reply: unknown): number {
  const raw = typeof reply === "object" && reply !== null ? (reply as Record<string, unknown>).max_upload_bytes : undefined;
  return typeof raw === "number" && Number.isFinite(raw) && raw > 0 ? raw : DEFAULT_MAX_UPLOAD_BYTES;
}

/** The part of a `geometry.formats` entry the upload needs: its suffixes, with the dot. */
export interface SuffixFormat {
  suffixes: readonly string[];
}

/** The part of a `File` the rules read, so a test needs no DOM. */
export interface FileLike {
  name: string;
  size: number;
}

/** The suffix of a file name in lower case with its dot, or "" when there is none (".gitignore" has none). */
export function suffixOf(name: string): string {
  const cut = Math.max(name.lastIndexOf("/"), name.lastIndexOf("\\"));
  const base = name.slice(cut + 1);
  const dot = base.lastIndexOf(".");
  return dot > 0 ? base.slice(dot).toLowerCase() : "";
}

/** The picker's `accept` text: every suffix of every format, comma separated, in the formats' order, once each. */
export function acceptList(formats: readonly SuffixFormat[]): string {
  const seen = new Set<string>();
  for (const format of formats) for (const suffix of format.suffixes) seen.add(suffix.toLowerCase());
  return [...seen].join(",");
}

/** True when the file's suffix is one of the formats'; the server checks again, this only spares a round trip. */
export function isSupportedName(name: string, formats: readonly SuffixFormat[]): boolean {
  const suffix = suffixOf(name);
  return suffix !== "" && formats.some((f) => f.suffixes.some((s) => s.toLowerCase() === suffix));
}

/** "Part.STEP" or "dir\\part.step" as the name the server gets: the last segment, nothing else. */
export function baseName(name: string): string {
  const cut = Math.max(name.lastIndexOf("/"), name.lastIndexOf("\\"));
  return name.slice(cut + 1);
}

/** Bytes as the progress line says them: "812 B", "12 kB", "12 MB", "1.5 GB"; one decimal below ten. */
export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return "0 B";
  const units: [number, string][] = [
    [1e9, "GB"],
    [1e6, "MB"],
    [1e3, "kB"],
  ];
  for (const [size, unit] of units) {
    if (bytes >= size) {
      const value = bytes / size;
      return `${value < 10 ? Number(value.toFixed(1)) : Math.round(value)} ${unit}`;
    }
  }
  return `${Math.round(bytes)} B`;
}

/** "Uploading part.step 12 MB / 40 MB"; with no total known yet, the bytes sent so far. */
export function progressText(name: string, loaded: number, total: number): string {
  const head = `Uploading ${baseName(name)}`;
  return total > 0 ? `${head} ${formatBytes(loaded)} / ${formatBytes(total)}` : `${head} ${formatBytes(loaded)}`;
}

/** 0..1, or null when the total is not known. */
export function progressFraction(loaded: number, total: number): number | null {
  return total > 0 ? Math.min(1, Math.max(0, loaded / total)) : null;
}

/** What went wrong with an upload, for the toast: the status the server answered (0 for no answer at all). */
export function uploadErrorText(status: number, name: string): string {
  const file = baseName(name);
  if (status === 413) return `${file} is too large for this server to take.`;
  if (status === 415) return `${file}: this format is not supported. Import takes STEP, IGES, BREP, STL, OBJ or 3MF.`;
  if (status === 401 || status === 403) return "The server did not accept this window's session. Reload the page and try again.";
  if (status === 404) return "This server cannot take uploads. Use Server path to name a file on the server's machine.";
  if (status === 0) return `The upload of ${file} failed: the connection to the server was lost.`;
  return `The upload of ${file} failed (HTTP ${status}).`;
}

/** The URL an upload goes to: the name is data, so it is encoded. The token is not here: see `uploadHeaders`. */
export function uploadUrl(apiBase: string, name: string): string {
  return `${apiBase.replace(/\/$/, "")}/api/upload?name=${encodeURIComponent(baseName(name))}`;
}

/** The headers of an upload: the body is raw bytes, and the token travels in its header. */
export function uploadHeaders(token: string): Record<string, string> {
  return { "Content-Type": "application/octet-stream", [TOKEN_HEADER]: token };
}

/** The toast for an import the person backed out of (the units question closed with Esc or Cancel). */
export function importCancelledText(path: string | null): string {
  return path === null || path === "" ? "Import cancelled." : `Import cancelled: ${baseName(path)}.`;
}

/** The part of a project the "same project" rule reads. */
export interface ProjectKeyLike {
  name: string;
}

/**
 * True when `after` is the project the upload started in. A project the window moved away from while the
 * file went up (New or Open) must not receive the file; `epoch` counts those moves, so a new project with
 * the same name is told apart too. The path is not compared: a first Save gives the same project a path.
 */
export function sameProject(before: { project: ProjectKeyLike | null; epoch: number }, after: { project: ProjectKeyLike | null; epoch: number }): boolean {
  if (before.project === null || after.project === null) return false;
  return before.epoch === after.epoch && before.project.name === after.project.name;
}

/** The reply of a good upload, or null for a body that is not one. */
export interface UploadReply {
  path: string;
  size: number;
  sha256: string;
}

export function readUploadReply(text: string): UploadReply | null {
  let raw: unknown;
  try {
    raw = JSON.parse(text);
  } catch {
    return null;
  }
  if (typeof raw !== "object" || raw === null || Array.isArray(raw)) return null;
  const r = raw as Record<string, unknown>;
  if (typeof r.path !== "string" || r.path === "") return null;
  return {
    path: r.path,
    size: typeof r.size === "number" && Number.isFinite(r.size) ? r.size : 0,
    sha256: typeof r.sha256 === "string" ? r.sha256 : "",
  };
}

/** What a drop or a picker gave: the one file to take, or why none is taken. */
export type Chosen<F extends FileLike> = { ok: true; file: F } | { ok: false; reason: string };

/**
 * One file at a time: a drop of several is refused whole, because taking the first and silently
 * ignoring the rest would import something the person did not mean; a name no format claims is refused
 * with the formats' list in the message.
 */
export function chooseFile<F extends FileLike>(files: readonly F[], formats: readonly SuffixFormat[], maxBytes: number = DEFAULT_MAX_UPLOAD_BYTES): Chosen<F> {
  if (files.length === 0) return { ok: false, reason: "No file was given." };
  if (files.length > 1) return { ok: false, reason: `Drop one file at a time (${files.length} were dropped).` };
  const file = files[0];
  if (!isSupportedName(file.name, formats)) {
    return { ok: false, reason: `${baseName(file.name)}: not a geometry file. Import takes ${acceptList(formats).replace(/,/g, " ")}.` };
  }
  if (file.size === 0) return { ok: false, reason: `${baseName(file.name)} is empty.` };
  // Said here, before a gigabyte goes up and comes back as "connection lost".
  if (file.size > maxBytes) return { ok: false, reason: `${baseName(file.name)} is ${formatBytes(file.size)}; this server takes files up to ${formatBytes(maxBytes)}.` };
  return { ok: true, file };
}

/** True when a drag carries files (and not text or a link): the viewport reacts only to those. */
export function dragHasFiles(types: ArrayLike<string> | null | undefined): boolean {
  if (!types) return false;
  for (let i = 0; i < types.length; i++) if (types[i] === "Files") return true;
  return false;
}

/** The part of a tree node the empty-project hint reads. */
interface NodeLike {
  type: string;
  children?: readonly NodeLike[];
}

/** True when the tree holds at least one node of `type`, at any depth. */
function holds(root: NodeLike | null, type: string): boolean {
  if (root === null) return false;
  if (root.type === type) return true;
  return (root.children ?? []).some((child) => holds(child, type));
}

/** True when the tree holds at least one `part` node, at any depth. */
export function hasParts(root: NodeLike | null): boolean {
  return holds(root, "part");
}

/**
 * The hint in the middle of an empty project: shown with a project open, no part in it and nothing
 * coming (an upload or an import under way), so it goes the moment the first part appears. **An
 * imported OpenFOAM case is not an empty project** (its mesh is its own, there is no CAD to drop):
 * the hint sat over a tutorial's results until this check (2026-11-01).
 */
export function showImportHint(input: { hasProject: boolean; tree: NodeLike | null; uploading: boolean; importing: boolean }): boolean {
  return input.hasProject && input.tree !== null && !input.uploading && !input.importing && !hasParts(input.tree) && !holds(input.tree, "foam_case");
}
