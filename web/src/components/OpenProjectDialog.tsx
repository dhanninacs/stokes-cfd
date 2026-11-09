import { useEffect, useId, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { ChevronLeft, Clock, Folder, FolderOpen, HardDrive, Package, TriangleAlert } from "lucide-react";
import type { BrowseReply } from "./FolderBrowser";

/**
 * The Open dialog: **Recent**, **Browse** and **Samples**, as the menu spec asks.
 *
 * **Why not the folder browser alone.** Browsing to a project is the slow path; the two things a
 * user actually wants are the short list of what they had open and the worked examples. All three
 * answers come from the server - `project.recent`, `project.browse`, `project.samples` - because
 * each knows something the client does not: which projects are still there, what the roots are, and
 * which samples exist in this build.
 *
 * **A vanished project is shown, greyed, with why.** `project.recent` computes `exists` per call for
 * exactly this: a stored list goes stale the moment a folder is moved, and a row that claims to open
 * something it cannot is worse than one saying it is gone.
 *
 * **A sample opens as a COPY** (`project.sample_open`), and the dialog says so, because a user who
 * believes they are editing the shipped example would be wrong.
 */
export interface RecentProject {
  name: string;
  path: string;
  modified?: string;
  opened?: string;
  exists: boolean;
  has_preview?: boolean;
}

export interface SampleInfo {
  id: string;
  title: string;
  description: string;
}

type Tab = "recent" | "browse" | "samples";

interface OpenProjectDialogProps {
  open: boolean;
  /** The server's base URL, for the thumbnail route. "" leaves the placeholders in place. */
  apiBase?: string;
  /** The session token, which an `<img>` can only carry in the query string. */
  token?: string;
  recent: RecentProject[];
  samples: SampleInfo[];
  load: (path: string | null) => Promise<BrowseReply>;
  /** Forget a project that is no longer on disk. */
  onForget: (path: string) => void;
  onOpen: (path: string) => void;
  onOpenSample: (id: string) => void;
  onCancel: () => void;
}

export function OpenProjectDialog(props: OpenProjectDialogProps) {
  return props.open ? <Dialog {...props} /> : null;
}

/**
 * The thumbnail URL of one project, or "" when the client is not connected yet.
 *
 * **The project's path is a QUERY parameter, and the server checks it against the projects it has
 * already offered** (`_known_project`) before serving anything - so this cannot become an arbitrary
 * file read however the string is written. Nothing here escapes it: the server's own check is the
 * boundary, and a doubled check in the client would only be a second thing to keep in step.
 */
export function previewUrl(apiBase: string, path: string, token = ""): string {
  if (apiBase === "" || path === "") return "";
  // **The token is required, and an `<img>` cannot send a header.** Every `/api/*` route checks the
  // session token; the RPC socket carries it in its URL and the geometry fetches pass it as a header
  // - but an `<img src>` can only put it in the query string. Measured without it: **HTTP 401**, and
  // a blank row where the thumbnail should be, which is exactly what a broken preview looks like.
  const auth = token === "" ? "" : `&token=${encodeURIComponent(token)}`;
  return `${apiBase.replace(/\/$/, "")}/api/project/preview?path=${encodeURIComponent(path)}${auth}`;
}

/** One row's thumbnail: the image when the project has one, a neutral placeholder when it does not. */
function Thumbnail({ url, has }: { url: string; has: boolean }) {
  // **A placeholder, never a broken image** (the spec's own words). A project that has never been
  // saved has no thumbnail and one whose file was removed has a URL that 404s - both show the
  // neutral mark rather than the browser's broken-image glyph.
  if (!has || url === "") {
    return (
      <span
        className="muted flex shrink-0 items-center justify-center rounded"
        style={{ width: 44, height: 28, background: "var(--surface-2, rgba(127,127,127,0.12))" }}
        aria-hidden="true"
      >
        <Package size={12} />
      </span>
    );
  }
  return (
    <img
      src={url}
      alt=""
      width={44}
      height={28}
      className="shrink-0 rounded object-cover"
      style={{ background: "var(--surface-2, rgba(127,127,127,0.12))" }}
      // A thumbnail that fails to load leaves the placeholder's space rather than a broken glyph.
      onError={(e) => {
        e.currentTarget.style.visibility = "hidden";
      }}
    />
  );
}

/** The path's last two components, for a row that has no width for the whole thing. */
export function shortPath(path: string, parts = 2): string {
  const bits = path.split("/").filter(Boolean);
  if (bits.length <= parts) return path;
  return "…/" + bits.slice(-parts).join("/");
}

function Dialog({
  apiBase = "",
  token = "",
  recent,
  samples,
  load,
  onForget,
  onOpen,
  onOpenSample,
  onCancel,
}: OpenProjectDialogProps) {
  const [tab, setTab] = useState<Tab>("recent");
  const [reply, setReply] = useState<BrowseReply | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const cancelRef = useRef(onCancel);
  useEffect(() => {
    cancelRef.current = onCancel;
  });
  const titleId = useId();

  // **Recent first, and the tab is only chosen for the user when Recent is empty.** Jumping to
  // Browse when there is nothing recent is helpful; overriding a full Recent list is not.
  useEffect(() => {
    if (recent.length === 0 && samples.length === 0) setTab("browse");
  }, [recent.length, samples.length]);

  const go = useMemo(
    () => (path: string | null) => {
      setBusy(true);
      setError(null);
      void load(path)
        .then(setReply)
        .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)))
        .finally(() => setBusy(false));
    },
    [load],
  );

  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const onKey = (e: globalThis.KeyboardEvent) => {
      if (e.key !== "Escape") return;
      e.preventDefault();
      e.stopPropagation();
      cancelRef.current();
    };
    document.addEventListener("keydown", onKey, true);
    return () => {
      document.removeEventListener("keydown", onKey, true);
      previous?.focus?.();
    };
  }, []);

  // The listing is fetched the first time Browse is shown, so opening the dialog costs one call.
  useEffect(() => {
    if (tab === "browse" && reply === null && !busy) go(null);
  }, [tab, reply, busy, go]);

  const tabButton = (id: Tab, label: string, icon: React.ReactNode, count?: number) => (
    <button
      key={id}
      type="button"
      role="tab"
      aria-selected={tab === id}
      className="btn no-drag"
      style={
        tab === id
          ? { background: "var(--surface-2, rgba(127,127,127,0.16))", borderColor: "var(--accent, #14b8a6)" }
          : undefined
      }
      onClick={() => setTab(id)}
    >
      {icon}
      <span className="ml-1">{label}</span>
      {count !== undefined && count > 0 && <span className="muted ml-1 text-[11px]">{count}</span>}
    </button>
  );

  return createPortal(
    <div
      className="scrim"
      onPointerDown={(e) => {
        if (e.target === e.currentTarget) onCancel();
      }}
    >
      <div
        className="dialog glass"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        style={{ minWidth: 520 }}
      >
        <h2 id={titleId} className="mb-2 text-[15px] font-semibold">
          Open project
        </h2>

        <div className="mb-2 flex gap-1.5" role="tablist" aria-label="Where to open from">
          {tabButton("recent", "Recent", <Clock size={13} aria-hidden="true" />, recent.length)}
          {tabButton("browse", "Browse", <HardDrive size={13} aria-hidden="true" />)}
          {tabButton("samples", "Samples", <Package size={13} aria-hidden="true" />, samples.length)}
        </div>

        {tab === "recent" && (
          <ul className="m-0 list-none overflow-y-auto p-0" style={{ maxHeight: 260, minHeight: 120 }} aria-label="Recent projects">
            {recent.map((row) => (
              <li key={row.path}>
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    className="flex flex-1 items-center gap-2 px-2 py-1 text-left text-[12.5px]"
                    // **A project that is gone is still LISTED, greyed, with the reason** - the
                    // alternative is a row that silently disappears and a user who thinks they
                    // deleted it.
                    disabled={!row.exists}
                    title={row.path}
                    onClick={() => onOpen(row.path)}
                    data-ws="recent-row"
                  >
                    <Thumbnail
                      url={row.exists && row.has_preview ? previewUrl(apiBase, row.path, token) : ""}
                      has={row.exists && row.has_preview === true}
                    />
                    <span className="flex-1 truncate">
                      {row.name}
                      {/* **The short path UNDER the name** (the spec): a name alone does not say
                          which folder it is in, and that is the question a Recent list answers. */}
                      <span className="muted block text-[11px]">{shortPath(row.path)}</span>
                    </span>
                    {!row.exists && (
                      <span className="text-[11px]" style={{ color: "var(--danger)" }}>
                        Folder not found
                      </span>
                    )}
                  </button>
                  {!row.exists && (
                    <button
                      type="button"
                      className="btn no-drag"
                      onClick={() => onForget(row.path)}
                      data-ws="recent-forget"
                    >
                      Remove
                    </button>
                  )}
                </div>
              </li>
            ))}
            {recent.length === 0 && (
              <li className="muted px-2 py-1 text-[12px]">No projects have been opened yet.</li>
            )}
          </ul>
        )}

        {tab === "browse" && (
          <>
            <div className="mb-2 flex items-center gap-2">
              <button
                type="button"
                className="btn no-drag"
                disabled={reply?.parent == null || busy}
                onClick={() => go(reply?.parent ?? null)}
                aria-label="Go up one folder"
              >
                <ChevronLeft size={14} />
              </button>
              <span className="mono flex-1 truncate text-[12px]" title={reply?.path ?? ""}>
                {reply?.path ?? "…"}
              </span>
            </div>
            {(reply?.roots ?? []).length > 1 && (
              <div className="mb-2 flex flex-wrap gap-1.5">
                {reply?.roots.map((root) => (
                  <button key={root} type="button" className="btn no-drag" onClick={() => go(root)}>
                    <HardDrive size={12} aria-hidden="true" />
                    <span className="ml-1">{root.split("/").filter(Boolean).pop()}</span>
                  </button>
                ))}
              </div>
            )}
            <ul className="m-0 list-none overflow-y-auto p-0" style={{ maxHeight: 240, minHeight: 120 }} aria-label="Folders">
              {(reply?.entries ?? []).map((row) => (
                <li key={row.name}>
                  <button
                    type="button"
                    className="flex w-full items-center gap-2 px-2 py-1 text-left text-[12.5px]"
                    onClick={() =>
                      // **A project opens; a plain folder is walked into.** That is the difference
                      // between the two kinds, and it is why `project.browse` marks them.
                      row.kind === "project"
                        ? onOpen(`${reply?.path ?? ""}/${row.name}`)
                        : go(`${reply?.path ?? ""}/${row.name}`)
                    }
                  >
                    {row.kind === "project" ? <FolderOpen size={13} aria-hidden="true" /> : <Folder size={13} aria-hidden="true" />}
                    <span className="flex-1 truncate">{row.name}</span>
                    {row.kind === "project" && <span className="muted text-[11px]">open</span>}
                  </button>
                </li>
              ))}
              {(reply?.entries ?? []).length === 0 && !busy && (
                <li className="muted px-2 py-1 text-[12px]">No folders here.</li>
              )}
            </ul>
          </>
        )}

        {tab === "samples" && (
          <ul className="m-0 list-none overflow-y-auto p-0" style={{ maxHeight: 260, minHeight: 120 }} aria-label="Samples">
            {samples.map((sample) => (
              <li key={sample.id} className="px-2 py-1.5">
                <button
                  type="button"
                  className="w-full text-left"
                  onClick={() => onOpenSample(sample.id)}
                  data-ws="sample-row"
                >
                  <span className="text-[12.5px]">{sample.title}</span>
                  <span className="muted block text-[11.5px]">{sample.description}</span>
                </button>
              </li>
            ))}
            {samples.length === 0 && (
              <li className="muted px-2 py-1 text-[12px]">This build ships no samples.</li>
            )}
          </ul>
        )}

        <div
          className="mt-2 flex min-h-[18px] items-center gap-1.5 text-[12px]"
          role={error ? "alert" : undefined}
          style={{ color: error ? "var(--danger)" : "var(--text-muted)" }}
        >
          {error ? (
            <>
              <TriangleAlert size={12} aria-hidden="true" />
              {error}
            </>
          ) : tab === "samples" ? (
            "A sample opens as a copy, so you can change anything."
          ) : (
            "Pick a project to open."
          )}
        </div>

        <div className="mt-3 flex justify-end">
          <button type="button" className="btn no-drag" onClick={onCancel}>
            Cancel
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
