import { useEffect, useId, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { ChevronLeft, Folder, FolderOpen, HardDrive } from "lucide-react";

/**
 * The folder browser: `project.browse` in a dialog.
 *
 * **Why this exists rather than a native dialog.** `host.canPickFolders` is true only in the Qt
 * desktop host; in a browser it is false, so the New project dialog had **no Browse button at all**
 * on the machine the owner actually uses. The menu spec asks for "Folder (field + Browse)",
 * and the server already owns the listing - `project.browse` is confined to two roots (home and the
 * projects folder), marks which folders are projects, and refuses anything outside them. This is the
 * UI for the command that was already there.
 *
 * **It only answers a question.** The dialog returns the folder the user chose and nothing else;
 * creating the project stays with `project.new`, so a browse that fails leaves no half-made folder.
 *
 * `new folder` is deliberately NOT here. Making a directory is a write, this dialog is a read, and
 * the field beside it already accepts a typed path - so the owner's "which folder to start in"
 * question is answered without inventing a second way to create something.
 */
export interface BrowseEntry {
  name: string;
  /** "dir" for an ordinary folder, "project" when it holds a project.json. */
  kind: string;
  modified?: string;
}

export interface BrowseReply {
  path: string;
  parent: string | null;
  roots: string[];
  entries: BrowseEntry[];
  truncated?: boolean;
}

interface FolderBrowserProps {
  open: boolean;
  title: string;
  /** Where to start; empty means the server's first root. */
  start?: string;
  /** Read a listing. Rejects with a message to show inside the dialog. */
  load: (path: string | null) => Promise<BrowseReply>;
  /** The chosen folder; the caller closes. */
  onChoose: (path: string) => void;
  onCancel: () => void;
}

export function FolderBrowser(props: FolderBrowserProps) {
  return props.open ? <Browser {...props} /> : null;
}

/** The last path component, for the "up" row; "" at a root. */
export function leafName(path: string): string {
  const clean = path.replace(/\/+$/, "");
  const at = clean.lastIndexOf("/");
  return at < 0 ? clean : clean.slice(at + 1);
}

/** A path shortened for a heading: the last two components, with the rest elided. */
export function shortPath(path: string, parts = 2): string {
  const bits = path.split("/").filter(Boolean);
  if (bits.length <= parts) return path;
  return "…/" + bits.slice(-parts).join("/");
}

function Browser({ title, start = "", load, onChoose, onCancel }: FolderBrowserProps) {
  const [reply, setReply] = useState<BrowseReply | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [highlight, setHighlight] = useState(0);
  const cancelRef = useRef(onCancel);
  useEffect(() => {
    cancelRef.current = onCancel;
  });
  const titleId = useId();

  const go = useMemo(
    () => (path: string | null) => {
      setBusy(true);
      setError(null);
      void load(path)
        .then((r) => {
          setReply(r);
          setHighlight(0);
        })
        .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)))
        .finally(() => setBusy(false));
    },
    [load],
  );

  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    go(start || null);
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
  }, [go, start]);

  const rows = reply?.entries ?? [];
  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setHighlight((h) => Math.min(rows.length - 1, h + 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setHighlight((h) => Math.max(0, h - 1));
    } else if (e.key === "Enter") {
      const row = rows[highlight];
      if (row) {
        e.preventDefault();
        go(`${reply?.path ?? ""}/${row.name}`);
      }
    }
  };

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
        style={{ minWidth: 460 }}
      >
        <h2 id={titleId} className="mb-2 text-[15px] font-semibold">
          {title}
        </h2>

        {/* The current folder, the way up, and the roots as one-click shortcuts. */}
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
          <span className="mono flex-1 truncate text-[12px]" title={reply?.path ?? ""} data-ws="browse-path">
            {reply?.path ?? "…"}
          </span>
        </div>
        {(reply?.roots ?? []).length > 1 && (
          <div className="mb-2 flex flex-wrap gap-1.5">
            {reply?.roots.map((root) => (
              <button key={root} type="button" className="btn no-drag" onClick={() => go(root)}>
                <HardDrive size={12} aria-hidden="true" />
                <span className="ml-1">{leafName(root)}</span>
              </button>
            ))}
          </div>
        )}

        <ul
          className="m-0 list-none overflow-y-auto p-0"
          style={{ maxHeight: 260, minHeight: 120 }}
          onKeyDown={onKeyDown}
          tabIndex={0}
          aria-label="Folders"
        >
          {rows.map((row, i) => (
            <li key={row.name}>
              <button
                type="button"
                className="flex w-full items-center gap-2 px-2 py-1 text-left text-[12.5px]"
                style={{ background: i === highlight ? "var(--surface-2, rgba(127,127,127,0.12))" : "transparent" }}
                onMouseEnter={() => setHighlight(i)}
                // **A single click moves into a folder; the button below chooses the one shown.**
                // Double-click would make "open this project folder" ambiguous, which is the thing
                // the user actually came to do.
                onClick={() => go(`${reply?.path ?? ""}/${row.name}`)}
              >
                {row.kind === "project" ? (
                  <FolderOpen size={13} aria-hidden="true" />
                ) : (
                  <Folder size={13} aria-hidden="true" />
                )}
                <span className="flex-1 truncate">{row.name}</span>
                {row.kind === "project" && (
                  <span className="muted text-[11px]">project</span>
                )}
              </button>
            </li>
          ))}
          {rows.length === 0 && !busy && (
            <li className="muted px-2 py-1 text-[12px]">No folders here.</li>
          )}
          {reply?.truncated && (
            <li className="muted px-2 py-1 text-[12px]">There are more folders than are listed.</li>
          )}
        </ul>

        <div className="mt-2 min-h-[18px] text-[12px]" role={error ? "alert" : undefined} style={{ color: error ? "var(--danger)" : "var(--text-muted)" }}>
          {error ?? "Pick a folder, then choose it."}
        </div>

        <div className="mt-3 flex justify-end gap-2">
          <button type="button" className="btn no-drag" onClick={onCancel}>
            Cancel
          </button>
          <button
            type="button"
            className="btn primary no-drag"
            data-ws="browse-choose"
            disabled={busy || reply == null}
            onClick={() => reply && onChoose(reply.path)}
          >
            Choose this folder
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
