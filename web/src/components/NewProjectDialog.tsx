import { Fragment, useEffect, useId, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { createPortal } from "react-dom";

/**
 * The New project dialog: name, folder, and the full path that will be created.
 *
 * **Why this is not `PromptDialog`.** That takes one field, and the menu spec asks for three things a
 * single field cannot carry: a **Name**, a **Folder** with Browse, and the line *"will be created
 * at &lt;full path&gt;"* - because `project_dir` appends `.stokes` and the server decides the folder, so
 * the path a user is about to create is not a string they typed. A dialog that showed only a name
 * was the owner's complaint ("New project asks only for a name").
 *
 * **Create is disabled until the value is valid**, and the reason is shown: a name that is not a
 * path segment, or a folder holding something already, is refused before the button, not after.
 *
 * The resolved path comes from `project.defaults`, so the front end never re-implements the
 * suffix and home rules the command itself applies.
 */
export interface NewProjectValues {
  name: string;
  /** Empty means "the default projects folder", which the server resolves. */
  folder: string;
}

interface NewProjectDialogProps {
  open: boolean;
  /** The server's projects folder, shown as the default and used to resolve the path. */
  defaultFolder: string;
  /** The full path `name` would occupy, or "" while the server has not answered yet. */
  resolvedPath: string;
  /** True when that path is already a project. */
  collides: boolean;
  /** What `project.defaults` said was wrong, if anything. */
  problem?: string | null;
  /** Ask the host for a folder; absent for a browser, where the field is typed. */
  onBrowse?: () => void;
  /**
   * The name or a chosen folder changed, so the caller can resolve the path it would occupy.
   * `folder` is "" while the field still holds the default (the server's projects folder).
   */
  onNameChange?: (name: string, folder: string) => void;
  /** A folder the host's dialog returned, which replaces whatever the field held. */
  chosenFolder?: string;
  onSubmit: (values: NewProjectValues) => void | Promise<string | null | undefined>;
  onCancel: () => void;
}

/**
 * The project name a typed title becomes. A user names a project the way they name a folder
 * ("mixing elbow"), and refusing the space was the first thing a walkthrough hit; so each run of
 * spaces, hyphens, dots or other characters the model's `NAME_RE` refuses becomes one underscore,
 * and the dialog says what the project will be saved as. A leading digit is still refused rather
 * than invented around.
 */
export function projectName(typed: string): string {
  const text = typed.trim();
  let name = text.replace(/[^A-Za-z0-9_]+/g, "_");
  if (/^[^A-Za-z0-9_]/.test(text)) name = name.slice(1);
  if (/[^A-Za-z0-9_]$/.test(text)) name = name.slice(0, -1);
  return name;
}

/** A name the model accepts: letters, digits, underscores, no leading digit (`NAME_RE`). */
export function nameProblem(name: string): string | null {
  if (!name) return "Enter a name.";
  if (/^[0-9]/.test(name)) return "Start the name with a letter.";
  if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(name)) {
    return "Use letters, digits and underscores, and do not start with a digit.";
  }
  return null;
}

/**
 * A path cut after each separator (`/` or `\`), so the path line can offer a line break there and
 * nowhere else: `D:\` + `work\` + `Stokes\` + `duct.stokes`. Joined, the pieces are the path.
 */
export function pathPieces(path: string): string[] {
  return path.match(/[^/\\]*[/\\]+|[^/\\]+$/g) ?? [];
}

/**
 * Which name problem the dialog shows. The empty-name one waits until the user has typed or tried
 * to create: a dialog that opens already saying "Enter a name." in the error colour reads as a
 * mistake the user made. Create stays disabled either way, so nothing is submitted without a name.
 */
export function shownNameProblem(problem: string | null, name: string, touched: boolean): string | null {
  return problem !== null && name === "" && !touched ? null : problem;
}

export function NewProjectDialog(props: NewProjectDialogProps) {
  return props.open ? <Dialog {...props} /> : null;
}

function Dialog({
  defaultFolder,
  resolvedPath,
  collides,
  problem = null,
  onBrowse,
  onNameChange,
  chosenFolder,
  onSubmit,
  onCancel,
}: NewProjectDialogProps) {
  const [name, setName] = useState("");
  /** True once the name has been typed in or a Create was tried (see `shownNameProblem`). */
  const [nameTouched, setNameTouched] = useState(false);
  const [folder, setFolder] = useState(defaultFolder);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const cancelRef = useRef(onCancel);
  useEffect(() => {
    cancelRef.current = onCancel;
  });
  const titleId = useId();
  const nameId = useId();
  const folderId = useId();
  const noteId = useId();

  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    input.current?.focus();
    const onKey = (e: globalThis.KeyboardEvent) => {
      if (e.key !== "Escape") return;
      e.preventDefault(); // the global handler must not also clear the selection
      e.stopPropagation();
      cancelRef.current();
    };
    document.addEventListener("keydown", onKey, true);
    return () => {
      document.removeEventListener("keydown", onKey, true);
      previous?.focus?.();
    };
  }, []);

  // **The folder field starts from the server's answer**, which may arrive after this dialog opened.
  // Only the DEFAULT is adopted: a folder the user has typed is theirs.
  const [touchedFolder, setTouchedFolder] = useState(false);
  useEffect(() => {
    if (!touchedFolder && defaultFolder) setFolder(defaultFolder);
  }, [defaultFolder, touchedFolder]);

  // A folder chosen in the host's dialog wins, and counts as touched so the default cannot
  // overwrite it when `project.defaults` answers.
  useEffect(() => {
    if (!chosenFolder) return;
    setTouchedFolder(true);
    setFolder(chosenFolder);
  }, [chosenFolder]);

  // **The path line follows the folder too**: a folder typed or browsed to is where `<folder>/<name>`
  // is created, so the "will be created at" line is resolved for it rather than for the default.
  const nameNow = useRef(name);
  const changed = useRef(onNameChange);
  useEffect(() => {
    nameNow.current = name;
    changed.current = onNameChange;
  });
  useEffect(() => {
    if (touchedFolder) changed.current?.(projectName(nameNow.current), folder.trim());
  }, [folder, touchedFolder]);

  const saved = projectName(name);
  const localProblem = nameProblem(saved);
  const blocking = localProblem ?? problem ?? (collides ? "A project with that name is already there." : null);
  const nameShown = shownNameProblem(localProblem, saved, nameTouched);
  const showPath = resolvedPath !== "" && localProblem === null;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (busy || blocking) {
      setNameTouched(true);
      setError(blocking ?? "Fix the name first.");
      return;
    }
    setBusy(true);
    try {
      const message = await onSubmit({ name: saved, folder: folder.trim() });
      // A message blocks the submit: the dialog stays open, saying what the server said.
      if (message) {
        setError(message);
        input.current?.focus();
      }
    } finally {
      setBusy(false);
    }
  };

  const trap = (e: KeyboardEvent<HTMLFormElement>) => {
    if (e.key !== "Tab") return;
    const nodes = Array.from(
      e.currentTarget.querySelectorAll<HTMLElement>("input, button:not([disabled])"),
    );
    const first = nodes[0];
    const last = nodes[nodes.length - 1];
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault();
      last?.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault();
      first?.focus();
    }
  };

  // The blocking reason, except the empty-name one before the user has typed (`shownNameProblem`).
  const note = error ?? (localProblem !== null ? nameShown : blocking);
  const renamed = localProblem === null && saved !== name.trim();
  return createPortal(
    <div
      className="scrim"
      onPointerDown={(e) => {
        if (e.target === e.currentTarget) onCancel();
      }}
    >
      <form
        className="dialog glass"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={noteId}
        onSubmit={submit}
        onKeyDown={trap}
      >
        <h2 id={titleId} className="mb-3 text-[15px] font-semibold">
          New project
        </h2>

        <label htmlFor={nameId} className="mb-1 block text-[12.5px]">
          Name
        </label>
        <input
          ref={input}
          id={nameId}
          className={`field ${nameShown || problem || collides ? "error" : ""}`}
          value={name}
          placeholder="duct"
          spellCheck={false}
          autoComplete="off"
          onChange={(e) => {
            setName(e.target.value);
            setNameTouched(true);
            setError(null);
            onNameChange?.(projectName(e.target.value), touchedFolder ? folder.trim() : "");
          }}
        />

        <label htmlFor={folderId} className="mb-1 mt-3 block text-[12.5px]">
          Folder
        </label>
        <div className="flex gap-2">
          <input
            id={folderId}
            className="field"
            value={folder}
            spellCheck={false}
            autoComplete="off"
            onChange={(e) => {
              setTouchedFolder(true);
              setFolder(e.target.value);
              setError(null);
            }}
          />
          {onBrowse && (
            <button type="button" className="btn no-drag" onClick={onBrowse}>
              Browse…
            </button>
          )}
        </div>

        {/*
          **The resolved path, verbatim from the server.** `.stokes` is appended and `~` is expanded
          by `project_dir`, so this is the only place a user can see what they are about to create -
          and the spec asks for exactly this line.
        */}
        {showPath && (
          <p className="mt-1.5 text-[12px]" style={{ color: "var(--text-muted)" }} data-ws="new-path">
            will be created at{" "}
            {/*
              **Breaks at a separator, never inside a name.** With the default wrapping,
              `stokes-work` broke after its hyphen, so the path read as two folders - and a path is
              the one string in this dialog a user may have to compare character by character.
              The earlier `word-break: break-word` treated the whole path as one word and broke it at
              whatever character met the edge, mid-filename, and a hyphen inside a folder name is a
              break opportunity of its own. So each component (with its trailing separator) is one
              inline-block: it moves to the next line whole, and only a component wider than the
              whole line (`max-width: 100%`) breaks inside, as the last resort that keeps it from
              overflowing the dialog. Measured with Playwright on the built page: breaks fall only
              after separators.
            */}
            <span className="mono" data-ws="new-path-text">
              {pathPieces(resolvedPath).map((piece, index) => (
                <Fragment key={index}>
                  <span style={{ display: "inline-block", maxWidth: "100%", overflowWrap: "anywhere" }}>{piece}</span>
                  <wbr />
                </Fragment>
              ))}
            </span>
          </p>
        )}

        <div
          id={noteId}
          className="mt-1.5 min-h-[18px] text-[12px]"
          role={note ? "alert" : undefined}
          style={{ color: note ? "var(--danger)" : "var(--text-muted)" }}
        >
          {note ??
            (renamed
              ? `Saved as ${saved}: project names use letters, digits and underscores.`
              : "Everything the project works with is created inside this folder.")}
        </div>

        <div className="mt-3 flex justify-end gap-2">
          <button type="button" className="btn no-drag" onClick={onCancel}>
            Cancel
          </button>
          <button type="submit" className="btn primary no-drag" disabled={busy || Boolean(blocking)} data-ws="new-create">
            Create
          </button>
        </div>
      </form>
    </div>,
    document.body,
  );
}
