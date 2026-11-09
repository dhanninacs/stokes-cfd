/**
 * The Start screen's Tutorials card, as data: what `tutorial.catalogue` said and what the card shows.
 *
 * **The catalogue is the server's** (ADR-0029): it walks the user's tutorial folder and judges each
 * case against the install's probe. This module only reads the reply and sorts it into the card's
 * three states - no folder, a failure, a list - so the card never decides what a case can do.
 */

/** One row of `tutorial.catalogue`; only the fields the card shows are typed. */
export interface TutorialCase {
  id: string;
  category: string;
  application: string | null;
  status: string;
  why: string;
}

export interface CatalogueReply {
  root: string;
  probed: boolean;
  /** Every case in the folder, whatever the filter: the header's figures. */
  totals: Record<string, number>;
  /** The cases the filter kept: the count under the filter. */
  counts: Record<string, number>;
  cases: TutorialCase[];
}

export type CatalogueState =
  | { kind: "loading" }
  | { kind: "none" }
  | { kind: "error"; message: string }
  | { kind: "listed"; reply: CatalogueReply };

/** The card shows this many rows; the filter narrows the rest. */
export const SHOWN = 50;

export type Execute = (method: string, params?: Record<string, unknown>, options?: { silent?: boolean }) => Promise<unknown>;

function codeOf(err: unknown): string | null {
  const code = (err as { code?: unknown } | null)?.code;
  return typeof code === "string" ? code : null;
}

function messageOf(err: unknown): string {
  const message = (err as { message?: unknown } | null)?.message;
  return typeof message === "string" && message ? message : "The tutorial list could not be read.";
}

function countsOf(value: unknown): Record<string, number> {
  const out: Record<string, number> = {};
  if (value && typeof value === "object") {
    for (const [key, n] of Object.entries(value as Record<string, unknown>)) {
      if (typeof n === "number") out[key] = n;
    }
  }
  return out;
}

/** The reply narrowed to what the card relies on; anything malformed is dropped, not guessed. */
export function parseCatalogue(raw: unknown): CatalogueReply {
  const r = (raw ?? {}) as Record<string, unknown>;
  const counts = countsOf(r.counts);
  const totals = countsOf(r.totals);
  const cases = Array.isArray(r.cases)
    ? (r.cases as Record<string, unknown>[])
        .filter((c) => c && typeof c.id === "string" && typeof c.status === "string")
        .map((c) => ({
          id: c.id as string,
          category: typeof c.category === "string" ? c.category : ".",
          application: typeof c.application === "string" ? c.application : null,
          status: c.status as string,
          why: typeof c.why === "string" ? c.why : "",
        }))
    : [];
  return { root: typeof r.root === "string" ? r.root : "", probed: r.probed === true, totals, counts, cases };
}

/**
 * Ask the server for the catalogue of the configured folder. **Silent**, because "no folder yet" is
 * the normal first state and not worth a toast; the card says it inline instead.
 */
export async function loadCatalogue(execute: Execute, filter: { category?: string; text?: string } = {}): Promise<CatalogueState> {
  const params: Record<string, unknown> = {};
  if (filter.category) params.category = filter.category;
  if (filter.text && filter.text.trim()) params.text = filter.text.trim();
  try {
    return { kind: "listed", reply: parseCatalogue(await execute("tutorial.catalogue", params, { silent: true })) };
  } catch (err) {
    if (codeOf(err) === "tutorial.no_root") return { kind: "none" };
    return { kind: "error", message: messageOf(err) };
  }
}

/** Store the folder the native picker returned; null on success, else the server's reason. */
export async function setRoot(execute: Execute, path: string): Promise<string | null> {
  try {
    await execute("tutorial.set_root", { path }, { silent: true });
    return null;
  } catch (err) {
    return messageOf(err);
  }
}

/** The categories in a reply, sorted, for the select. */
export function categoriesOf(reply: CatalogueReply): string[] {
  return [...new Set(reply.cases.map((c) => c.category))].sort((a, b) => a.localeCompare(b));
}

/** Total cases in a count table. */
export function totalOf(counts: Record<string, number>): number {
  return Object.values(counts).reduce((sum, n) => sum + n, 0);
}

/** The status as words, unchanged in meaning: `missing_module` reads "missing module". */
export function statusWord(status: string): string {
  return status.replace(/_/g, " ");
}

/** The dot colour: ready is fine, unverified is unknown, everything else cannot run as is. */
export function statusTone(status: string): "ok" | "muted" | "warn" {
  if (status === "ready") return "ok";
  if (status === "unverified") return "muted";
  return "warn";
}

/** What opening a tutorial came to, as the card reports it. */
export interface OpenOutcome {
  ok: boolean;
  text: string;
}

interface OpenReply {
  project?: { name?: unknown } | null;
  case?: { status?: unknown; refusals?: unknown };
}

/**
 * Open one tutorial in a new project of its own (`tutorial.open`). **Silent**, so the card words
 * the outcome itself: a case the lint refused still opens its (empty) project, and that is said
 * with the first file, line and reason rather than as a bare failure.
 */
export async function openTutorial(execute: Execute, id: string): Promise<OpenOutcome> {
  const leaf = id.split("/").pop() ?? id;
  try {
    const reply = ((await execute("tutorial.open", { tutorial: id }, { silent: true })) ?? {}) as OpenReply;
    const project = typeof reply.project?.name === "string" ? reply.project.name : leaf;
    if (reply.case?.status === "refused") {
      const first = Array.isArray(reply.case.refusals) ? (reply.case.refusals[0] as Record<string, unknown> | undefined) : undefined;
      const where = first && typeof first.file === "string" ? `${first.file}${typeof first.line === "number" && first.line ? `:${first.line}` : ""}` : "";
      const why = first && typeof first.why === "string" ? first.why : "it did not pass the safety check";
      // **The empty project is closed again**, so the start screen stays with the reason on it. Left
      // open, the window showed a blank project and the reason only as a toast, which read as
      // "nothing loaded" (the ballValve report). The server now closes it itself (and removes its
      // folder and Recent row), replying `project: null`; an older server still needs the close.
      if (reply.project !== null) {
        try {
          await execute("project.close", {}, { silent: true });
        } catch {
          /* the reason below is what matters; a project that will not close stays open */
        }
      }
      return { ok: false, text: `${leaf} cannot be opened${where ? ` (${where})` : ""}: ${why}` };
    }
    return { ok: true, text: `Opened ${leaf} in project ${project}` };
  } catch (err) {
    return { ok: false, text: messageOf(err) };
  }
}
