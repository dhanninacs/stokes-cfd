import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ChevronDown,
  Clock,
  Copy,
  Download,
  FilePlus,
  FolderOpen,
  Import,
  Save,
  SaveAll,
  Server,
  Settings2,
  X,
} from "lucide-react";
import type { ProjectInfo } from "../api/types";
import { useStore } from "../state/store";
import { Menu, type MenuItemSpec } from "../components/Menu";
import { FolderBrowser, type BrowseReply } from "../components/FolderBrowser";
import { NewProjectDialog, type NewProjectValues } from "../components/NewProjectDialog";
import { OpenProjectDialog, type RecentProject, type SampleInfo } from "../components/OpenProjectDialog";
import { PromptDialog } from "../components/PromptDialog";
import { useShortcut } from "../workspace/keymap";
import { useWorkspace } from "../workspace/store";
import { findMeshNodes, readResult } from "../panels/meshPanelModel";
import { currentScene } from "../viewport/scene";
import { registerAction, runAction } from "./actions";
import { host } from "./host";
import { setImportHandler } from "./importBridge";
import { afterCreate } from "./newProjectOutcome";
import { uploadForImport, useUpload } from "./upload";
import { acceptList, DEFAULT_MAX_UPLOAD_BYTES, importCancelledText, maxUploadBytes, sameProject } from "./uploadModel";

type Prompt = "new" | "open" | "save-as" | "save-copy" | "close" | "import-geometry" | "units";

/** One entry of `geometry.formats`: a format, its suffixes and where its units come from. */
interface FormatSpec {
  id: string;
  label: string;
  suffixes: string[];
  /** "brep" (STEP, IGES, BREP) or "mesh" (STL, OBJ, 3MF). */
  family: string;
  /** "file" when the file declares its units, "param" when the import's `units` names them. */
  units: string;
}

/** The reply of `geometry.formats`: the formats and the picker's name filter. */
interface FormatsReply {
  formats?: FormatSpec[];
  filter?: string;
  /** The largest upload the server takes; absent from an older server, which takes 1 GiB. */
  max_upload_bytes?: number;
}

const FALLBACK_FORMATS: FormatSpec[] = [
  // The six formats of `geometry.formats` (`stokes/geometry/formats.py`), for a server that has no
  // such command: the filter and the units question are built from these instead.
  { id: "step", label: "STEP", suffixes: [".step", ".stp"], family: "brep", units: "file" },
  { id: "iges", label: "IGES", suffixes: [".iges", ".igs"], family: "brep", units: "file" },
  { id: "brep", label: "BREP", suffixes: [".brep", ".brp"], family: "brep", units: "param" },
  { id: "stl", label: "STL", suffixes: [".stl"], family: "mesh", units: "param" },
  { id: "obj", label: "OBJ", suffixes: [".obj"], family: "mesh", units: "param" },
  { id: "3mf", label: "3MF", suffixes: [".3mf"], family: "mesh", units: "file" },
];

const DEFAULT_UNITS = "mm";
/** The length unit an import of a file that declares none starts from. */

/** Name filter of the native file dialog (Qt syntax), in the shape `geometry.formats` returns it:
 *  every format, then one entry per format, then all files. */
function nameFilter(formats: FormatSpec[]): string {
  const globs = (list: FormatSpec[]) => list.flatMap((f) => f.suffixes.map((s) => `*${s}`)).join(" ");
  return [`Geometry files (${globs(formats)})`, ...formats.map((f) => `${f.label} (${globs([f])})`), "All files (*)"].join(";;");
}

const FALLBACK_FILTER = nameFilter(FALLBACK_FORMATS);

/** The format of a chosen file by its suffix, case-insensitive; null when the file carries a
 *  suffix none of the formats claim (All files (*) lets any file through). */
function formatOf(formats: FormatSpec[], path: string): FormatSpec | null {
  const cut = Math.max(path.lastIndexOf("/"), path.lastIndexOf("\\"));
  const dot = path.lastIndexOf(".");
  const suffix = dot > cut ? path.slice(dot).toLowerCase() : "";
  return formats.find((f) => f.suffixes.some((s) => s.toLowerCase() === suffix)) ?? null;
}

/** The `PromptDialog` rows. `new`, `close` and `save-copy` have their own dialogs and are absent. */
const PROMPTS: Partial<Record<Prompt, { title: string; label: string; placeholder: string; help: string; confirm: string }>> = {
  new: {
    title: "New project",
    label: "Project name",
    placeholder: "e.g. pipe_flow",
    help: "Starts an empty project. Save the open one first if you need it.",
    confirm: "Create",
  },
  open: {
    title: "Open project",
    label: "Project folder",
    placeholder: "C:\\projects\\pipe_flow.stokes",
    help: "The path of a .stokes folder.",
    confirm: "Open",
  },
  "save-as": {
    title: "Save project as",
    label: "Save to folder",
    placeholder: "C:\\projects\\pipe_flow.stokes",
    help: "A folder path; the .stokes suffix is added when missing.",
    confirm: "Save",
  },
  "import-geometry": {
    title: "Import geometry",
    label: "Geometry file",
    placeholder: "C:\\parts\\bracket.step",
    help: "The path of a STEP, IGES, BREP, STL, OBJ or 3MF file on the machine the server runs on.",
    confirm: "Import",
  },
  units: {
    title: "Units of the file",
    label: "Length unit (mm, cm, m, in, ft)",
    placeholder: DEFAULT_UNITS,
    help: "This format does not say its unit, so the import takes it from here.",
    confirm: "Import",
  },
};

/** The last segment of a path, whichever separator the host's OS uses. */

/** The folder that holds a file or folder path, with its trailing separator; "" when it has none. */
function parentFolder(path: string | null | undefined): string {
  const cut = path ? Math.max(path.lastIndexOf("/"), path.lastIndexOf("\\")) : -1;
  return path && cut >= 0 ? path.slice(0, cut + 1) : "";
}

/** Explorer's "Copy as path" wraps the path in quotes. */
function unquote(text: string): string {
  return text.replace(/^"(.*)"$/, "$1");
}

/**
 * The project name in the top bar, as a menu: New, Open, Import geometry, Save, Save as. Every
 * entry is an API command (project.new, project.open, geometry.import, project.save), so the
 * console shows it and the journal records it. A project without a path yet turns Save into Save
 * as. The menu registers the four project actions on the action bus while it is mounted; their
 * chords belong to the keymap (workspace/keymap.ts), whose bindings the menu's hints show.
 */
export default function ProjectMenu() {
  const project = useStore((s) => s.project);
  /** The server's base URL, for the thumbnail route - "" until the client exists, which
   *  leaves the placeholders in place rather than a broken image. */
  const apiBase = useStore((s) => s.client?.apiBase ?? "");
  /**
   * **What Export can act on, so its entries can say why they are off** (the menu spec: "entries are
   * disabled with a reason when there is nothing to export"). `geometry.export_stl` takes tags or
   * parts and needs at least one; the mesh and case entries need a mesh result.
   */
  const parts = useStore((s) => s.parts);
  const partCount = Object.keys(parts).length;
  const tree = useStore((s) => s.tree);
  // `findMeshNodes` is the panel's own accessor for the mesh settings and its latest result, so the
  // menu and the Mesh panel cannot disagree about whether a mesh exists.
  const meshView = useMemo(() => readResult(findMeshNodes(tree).result), [tree]);
  const hasMesh = meshView !== null && meshView.phase === "done" && !meshView.error;
  /** The session token, which an `<img>` can only carry in the query string. */
  const token = useStore((s) => s.client?.token ?? "");

  /**
   * **`undefined` counts as dirty.** An older server does not send the field, and the safe reading
   * of "I cannot tell you" is to ask before discarding rather than to discard silently.
   */
  const dirty = project?.dirty !== false;
  const connected = useStore((s) => s.connected);
  const execute = useStore((s) => s.execute);
  const select = useStore((s) => s.select);
  const pushToast = useStore((s) => s.pushToast);
  const [open, setOpen] = useState(false);
  const [prompt, setPrompt] = useState<Prompt | null>(null);
  // **The New dialog's data comes from the server**: the projects folder it defaults to, and the
  // full path the typed name would occupy. Resolved here rather than in the dialog so the front end
  // never re-implements the `.stokes` suffix and `$STOKES_HOME` rules that `project.new` applies.
  /** A folder the host's picker returned, which the New dialog shows instead of the default. */
  const [chosenFolder, setChosenFolder] = useState("");
  /**
   * **The in-app folder browser**, used whenever the host has no native dialog.
   *
   * `host.canPickFolders` is false in a browser - which is where the owner works - so the New
   * dialog had NO Browse button at all. `project.browse` was already there, confined to two roots
   * and marking which folders are projects; this is its UI.
   */
  const [browsing, setBrowsing] = useState(false);
  /**
   * **Recent and Samples are fetched when the menu opens**, not held: `project.recent` recomputes
   * whether each folder still exists on every call, so a cached list would show a project as
   * openable after it had been moved.
   */
  const [recent, setRecent] = useState<RecentProject[]>([]);
  const [samples, setSamples] = useState<SampleInfo[]>([]);
  const refreshLists = useCallback(() => {
    void execute("project.recent")
      .then((r) => setRecent(((r as { projects?: RecentProject[] }).projects ?? [])))
      .catch(() => setRecent([]));
    void execute("project.samples")
      .then((r) => setSamples(((r as { samples?: SampleInfo[] }).samples ?? [])))
      .catch(() => setSamples([]));
  }, [execute]);
  const loadFolder = useCallback(
    async (path: string | null): Promise<BrowseReply> => {
      const reply = (await execute("project.browse", path ? { path } : {})) as BrowseReply;
      return reply;
    },
    [execute],
  );
  const [newDefaults, setNewDefaults] = useState<{ folder: string; path: string; exists: boolean; problem: string | null }>({
    folder: "",
    path: "",
    exists: false,
    problem: null,
  });
  /** The formats `geometry.formats` reports, and the picker's filter built from them. */
  const [formats, setFormats] = useState(FALLBACK_FORMATS);
  const [filter, setFilter] = useState(FALLBACK_FILTER);
  const [maxBytes, setMaxBytes] = useState(DEFAULT_MAX_UPLOAD_BYTES);
  /** Counts the projects this window has moved to (New, Open): an upload that started in an earlier one is not imported. */
  const switches = useRef(0);
  const askedFormats = useRef(false);
  const trigger = useRef<HTMLButtonElement>(null);
  /** The hidden file input of the browser import (T-219 A): "Import geometry…" presses it when the host has no native picker. */
  const fileInput = useRef<HTMLInputElement>(null);
  /** Folder of the last file imported in this session: where the next picker opens. */
  const importFolder = useRef("");
  /** True while the native picker is up, so a repeated import chord cannot queue a second dialog. */
  const picking = useRef(false);
  /** The chosen file waiting for its units: a format that declares none (BREP, STL, OBJ, 3MF). */
  const awaitingUnits = useRef<string | null>(null);

  /** Runs a project command; `execute` has already logged a failure and raised its toast. */
  const run = useCallback(
    async (method: string, params: Record<string, unknown>, done: (info: ProjectInfo) => string): Promise<boolean> => {
      try {
        const info = (await execute(method, params)) as ProjectInfo;
        pushToast("ok", done(info));
        if (info.path_warning) pushToast("info", info.path_warning);
        return true;
      } catch {
        return false;
      }
    },
    [execute, pushToast],
  );

  /** A project switch: an upload under way was meant for the project that has just gone, so it is stopped. */
  const switched = useCallback(() => {
    switches.current += 1;
    useUpload.getState().cancel?.();
  }, []);
  /**
   * Ask the server where a name would go, for the "will be created at" line.
   *
   * **Debounced**, because the user types a name a character at a time and each call touches the
   * disk; and it runs when the dialog OPENS (with no name) so the empty dialog already shows the
   * folder it will use.
   */
  const resolveDefaults = useCallback(
    async (name: string, folder = "") => {
      try {
        const reply = (await execute("project.defaults", name ? (folder ? { name, folder } : { name }) : {})) as {
          folder?: string;
          path?: string;
          exists?: boolean;
        };
        setNewDefaults({
          folder: reply.folder ?? "",
          path: reply.path ?? "",
          exists: reply.exists === true,
          problem: null,
        });
      } catch (error) {
        // A failure here must not block the dialog: the path line is help, and the command that
        // creates the project is `project.new`, which reports its own problems.
        setNewDefaults({ folder: "", path: "", exists: false, problem: error instanceof Error ? error.message : null });
      }
    },
    [execute],
  );

  /**
   * The New dialog asks for its path line as the name changes.
   *
   * **Debounced**, because each call touches the disk and a name arrives a character at a time. The
   * timer is cleared on the next keystroke, so one call lands per pause.
   */
  const nameTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const resolveName = useCallback(
    (name: string, folder = "") => {
      if (nameTimer.current) clearTimeout(nameTimer.current);
      nameTimer.current = setTimeout(() => void resolveDefaults(name, folder), 120);
    },
    [resolveDefaults],
  );
  useEffect(() => () => {
    if (nameTimer.current) clearTimeout(nameTimer.current);
  }, []);

  /** The New dialog's Create: **name AND folder**, which is what the spec asks for. */
  const create = async (values: NewProjectValues) => {
    const folder = values.folder.trim();
    const params: Record<string, unknown> = { name: values.name };
    if (folder) params.path = `${folder.replace(/\/+$/, "")}/${values.name}`;
    const created = Boolean(await run("project.new", params, (info) => `Created project ${info.name}`));
    if (afterCreate(created).closeDialog) {
      switched();
      select(null);
      setPrompt(null);
    }
    // When nothing was created the dialog stays open; `execute` has already shown the reason.
    return null;
  };
  const openProject = useCallback(
    async (path: string) => {
      if (await run("project.open", { path }, (info) => `Opened ${info.name}`)) {
        switched();
        select(null);
        setPrompt(null);
      }
    },
    [run, select, switched],
  );

  /**
   * **A sample opens as a copy**, which `project.sample_open` makes for us - so two experiments on
   * the same sample cannot collide and the shipped one cannot be lost. The toast says "copy" for the
   * same reason the dialog does.
   */
  const openSample = useCallback(
    async (id: string) => {
      if (await run("project.sample_open", { sample: id }, (info) => `Opened a copy: ${info.name}`)) {
        switched();
        select(null);
        setPrompt(null);
      }
    },
    [run, select, switched],
  );

  /** Forget a project whose folder is gone. It is not deleted - only the list entry is. */
  const forgetRecent = useCallback(
    (path: string) => {
      void execute("project.recent.remove", { path })
        .then(() => refreshLists())
        .catch(() => undefined);
    },
    [execute, refreshLists],
  );
  const saveAs = useCallback((path: string) => run("project.save", { path }, (info) => `Saved to ${info.path ?? path}`), [run]);

  /**
   * **`Save a copy` stays where it is** (the menu spec): `project.save_copy` copies the whole
   * project - imports, cache, runs, cases - and does NOT switch the session to it.
   *
   * This is also the entry the busy refusal names: `saveAs` is refused while a run is in flight,
   * and "use Save a copy" is only actionable if the entry exists beside it.
   */
  const saveCopy = useCallback(
    (folder: string, name: string) =>
      run("project.save_copy", { path: folder, name }, () => `Copy saved to ${folder}`),
    [run],
  );

  /**
   * **Close asks about unsaved work first** (menu spec rule 2), because a project with model changes
   * that simply vanished is the one loss a user cannot undo.
   *
   * The dirty test is the store's own `dirty` flag - the same one Save's disabled state uses - so
   * the prompt and the button cannot disagree about whether there is anything to lose.
   */
  const closeProject = useCallback(() => {
    if (!dirty) {
      void run("project.close", {}, () => "Project closed");
      return;
    }
    setPrompt("close");
  }, [dirty, run]);
  const hasProject = project !== null;
  const hasPath = Boolean(project?.path);

  /** Asks for a folder: the host's native dialog when there is one, else the in-app prompt. */
  const askPath = useCallback(
    (which: "open" | "save-as" | "save-copy") => {
      if (!host.canPickFolders) {
        setPrompt(which);
        return;
      }
      const start = project?.path ?? "";
      if (which === "save-copy") {
        // **A copy is not a switch**, so the dialog asks where to put it and nothing about the
        // session changes. `project.save_copy` names the destination; the name stays the project's.
        const where = host.pickSaveFolder(
          "Save a copy in",
          `${project?.name ?? "project"}-copy`,
        );
        void where.then((folder) => {
          if (folder) void saveCopy(folder, project?.name ?? "project");
        });
        return;
      }
      const picked = which === "open" ? host.pickFolder("Open project", start) : host.pickSaveFolder("Save project as", start || `${project?.name ?? "project"}.stokes`);
      void picked.then((path) => {
        if (!path) return;
        if (which === "open") void openProject(path);
        else void saveAs(path);
      });
    },
    [project, openProject, saveAs],
  );
  /**
   * Take the project's thumbnail and store it, **after a successful save**.
   *
   * The spec asks for a preview on the Recent rows and the Samples tile, and the routes existed with
   * nothing calling the writer: `project.preview.set` and `GET /api/project/preview` were both
   * built, so every project had `has_preview: false` for ever.
   *
   * **One-shot and silent.** A capture is a nicety: a canvas that cannot give a PNG, or a store that
   * refuses it, must not fail a save the user asked for - so nothing here raises and nothing is
   * reported. The next successful save overwrites whatever is there.
   */
  const capturePreview = useCallback(() => {
    const scene = currentScene();
    if (scene === null) return;
    const png = scene.captureThumbnail();
    if (png === "") return;
    const base64 = png.slice(png.indexOf(",") + 1);
    // **Silent in the CONSOLE, but not in the log.** `silent` keeps the base64 out of the command
    // log - a 320x200 PNG is tens of kilobytes of noise per save - while a REFUSAL is still worth
    // one line, because a thumbnail that never appears is otherwise impossible to diagnose: the
    // first version of this swallowed `project.preview_invalid` and left no trace at all.
    void execute("project.preview.set", { png_base64: base64 }, { silent: true }).catch((error: unknown) => {
      useStore.getState().log("info", `the project thumbnail was not stored: ${String(error).slice(0, 120)}`);
    });
  }, [execute]);

  /**
   * Export one STL per part, into the project's `exports/` folder.
   *
   * `geometry.export_stl` takes tags or parts and writes INTO the project - the spec's "the OpenFOAM
   * case folder" and the CSV/data entries are not built yet, so they are listed disabled with a
   * reason rather than left out: an entry that vanishes teaches a user nothing, and one that says
   * why teaches them what to do.
   */
  const exportParts = useCallback(() => {
    const ids = Object.keys(parts);
    void run(
      "geometry.export_stl",
      { path: "parts", parts: ids },
      (info) => {
        const files = (info as unknown as { files?: unknown[] }).files ?? [];
        return `Exported ${files.length} STL file${files.length === 1 ? "" : "s"}`;
      },
    );
  }, [parts, run]);

  const save = useCallback(() => {
    if (!hasProject) return;
    if (hasPath) {
      void run("project.save", {}, (info) => `Saved to ${info.path ?? "the project folder"}`).then(
        (ok) => {
          // **After the save, not before**: the thumbnail describes what was written, and a capture
          // taken first would not match the file it belongs to.
          if (ok) capturePreview();
        },
      );
    } else askPath("save-as");
  }, [hasProject, hasPath, run, askPath, capturePreview]);

  /**
   * The picker builds itself from `geometry.formats`, which needs no project (ADR-0021 decision
   * 1.1): asked once, when the menu is mounted and the connection is up. A server without the
   * command, or a call that fails, leaves the six fallback formats and their filter in place.
   */
  useEffect(() => {
    if (!connected || askedFormats.current) return;
    askedFormats.current = true;
    const client = useStore.getState().client;
    if (!client) return;
    void client
      .call<FormatsReply>("geometry.formats")
      .then((reply) => {
        const list = Array.isArray(reply?.formats) ? reply.formats : [];
        if (list.length === 0) return;
        setFormats(list);
        setFilter(typeof reply?.filter === "string" ? reply.filter : nameFilter(list));
        setMaxBytes(maxUploadBytes(reply));
      })
      .catch(() => undefined);
  }, [connected]);

  /**
   * Starts the import of a chosen file. The command returns at once; the part appears and fills
   * in through the geometry.* events the viewport follows, so there is no `wait`.
   */
  const importFile = useCallback(
    async (path: string, units?: string) => {
      try {
        await execute("geometry.import", units ? { path, units } : { path });
      } catch {
        // `execute` has already logged the failure and raised its toast
      }
    },
    [execute],
  );

  /**
   * Starts the import of a path. A format the file does not declare the units of (BREP and the
   * mesh formats) asks for them first, since only the command's `units` can name them.
   */
  const startImport = useCallback(
    (path: string) => {
      if (formatOf(formats, path)?.units === "param") {
        awaitingUnits.current = path;
        setPrompt("units");
        return;
      }
      void importFile(path);
    },
    [formats, importFile],
  );

  /**
   * A file from this browser (the picker's or a drop's): sent to the server, which answers with the
   * path it kept it at, and then imported like a typed path (so BREP and the mesh formats still ask
   * for their units). A refused name, a cancel or a failure is said by `uploadForImport` and ends here.
   */
  const importUploaded = useCallback(
    async (file: File) => {
      if (!hasProject) {
        pushToast("info", "Create or open a project first.");
        return;
      }
      void execute("geometry.prepare", {}, { silent: true }).catch(() => undefined);
      const started = { project: useStore.getState().project, epoch: switches.current };
      const path = await uploadForImport(file, formats, maxBytes);
      if (path === null) return;
      // The file went up while the window moved to another project: it must not land in that one.
      if (!sameProject(started, { project: useStore.getState().project, epoch: switches.current })) {
        pushToast("info", `The project changed while ${file.name} was uploading, so it was not imported. Import it again in the project you want.`);
        return;
      }
      startImport(path);
    },
    [hasProject, execute, pushToast, formats, maxBytes, startImport],
  );

  /** Import geometry…: warms the import worker, then asks for the file (native picker, the browser's file input). */
  const importGeometry = useCallback(() => {
    if (!hasProject || picking.current) return;
    // The worker loads the CAD kernel while the user browses. Not awaited; an error is ignored
    // here because geometry.import reports it (a missing CAD extra, say) with its own toast.
    void execute("geometry.prepare", {}, { silent: true }).catch(() => undefined);
    if (!host.canPickFiles) {
      // A browser has no path to give the server: the file itself goes up (T-219 A). The typed path
      // stays one entry away ("Server path…") for a file that is already on the server's machine.
      fileInput.current?.click();
      return;
    }
    picking.current = true;
    void host
      .pickFile("Import geometry", importFolder.current || parentFolder(project?.path), filter)
      .then((path) => {
        if (!path) return;
        importFolder.current = parentFolder(path);
        startImport(path);
      })
      .finally(() => {
        picking.current = false;
      });
  }, [hasProject, project, execute, filter, startImport]);

  /**
   * **The tree's "Import geometry…" entry, heard here.**
   *
   * The picker, the browser fallback, the remembered folder and the units question all live in this
   * component, because they are one flow. The tree announcing what was asked for keeps ONE flow
   * rather than a second picker that would skip the warm-up and the staging a large CAD file needs -
   * which is the same reason the folder card announces its Add rather than owning a form.
   */
  useEffect(() => {
    const heard = () => importGeometry();
    document.addEventListener("stokes:import-geometry", heard);
    return () => document.removeEventListener("stokes:import-geometry", heard);
  }, [importGeometry]);


  // A file dropped on the viewport comes through the same upload as the picker's.
  useEffect(() => setImportHandler((file) => void importUploaded(file)), [importUploaded]);

  // The keymap (and the ribbon and the palette) run these by action id; which chord does it is the
  // workspace's `shortcuts`, so a rebinding needs nothing here. Without a project they do nothing.
  useEffect(() => {
    const stops = [
      registerAction("project.save", save),
      registerAction("project.saveAs", () => {
        if (hasProject) askPath("save-as");
      }),
      registerAction("project.open", () => askPath("open")),
      registerAction("project.importStep", importGeometry),
    ];
    return () => stops.forEach((stop) => stop());
  }, [save, hasProject, askPath, importGeometry]);

  const openKeys = useShortcut("project.open");
  const importKeys = useShortcut("project.importStep");
  const saveKeys = useShortcut("project.save");
  const saveAsKeys = useShortcut("project.saveAs");
  const saveCopyKeys = useShortcut("project.saveCopy");
  const closeKeys = useShortcut("project.close");
  const settingsKeys = useShortcut("app.settings");
  /** The unit the units question starts from: the user's Default import unit (Settings → Units). */
  const importUnit = useWorkspace((s) => s.document.preferences.import.units);
  const items: MenuItemSpec[] = [
    { type: "heading", id: "where", label: project ? (project.path ?? "Not saved yet") : "No project open" },
    {
      id: "new",
      label: "New project…",
      icon: <FilePlus size={14} />,
      // **Resolve the folder before the dialog is shown**, so the empty dialog already says where
      // the project will go rather than showing the line a moment later.
      onSelect: () => {
        setPrompt("new");
        void resolveDefaults("");
      },
    },
    {
      id: "open",
      label: "Open…",
      icon: <FolderOpen size={14} />,
      hint: openKeys.hint,
      // **The dialog, not the native folder picker**: it carries Recent, Browse and Samples, and the
      // spec asks for a project browser rather than "a typed path". The lists are refreshed on the
      // way in, because `exists` is computed per call.
      onSelect: () => {
        refreshLists();
        setPrompt("open");
      },
    },
    { id: "import-geometry", label: "Import geometry…", icon: <Import size={14} />, hint: importKeys.hint, disabled: !hasProject, onSelect: importGeometry },
    // The browser's secondary entry: a file that already lives on the machine the server runs on.
    ...(host.canPickFiles ? [] : [{ id: "import-server-path", label: "Server path…", icon: <Server size={14} />, disabled: !hasProject, onSelect: () => setPrompt("import-geometry") } satisfies MenuItemSpec]),
    { type: "separator", id: "sep" },
    { id: "save", label: "Save", icon: <Save size={14} />, hint: saveKeys.hint, disabled: !hasProject, onSelect: save },
    { id: "save-as", label: "Save as…", icon: <SaveAll size={14} />, hint: saveAsKeys.hint, disabled: !hasProject, onSelect: () => askPath("save-as") },
    {
      id: "open-recent",
      label: "Open recent",
      icon: <Clock size={14} />,
      // **Last 10, name and short path, from the same fetch the Open dialog uses.** A vanished
      // project is greyed here with its reason rather than dropped, matching the dialog's rule.
      description: recent.length === 0 ? "No projects have been opened yet." : "The projects you had open last.",
      disabled: recent.length === 0,
      items: recent.slice(0, 10).map((row) => ({
        id: `recent-${row.path}`,
        label: `${row.name}  ·  ${row.path.split("/").filter(Boolean).slice(-2).join("/")}`,
        description: row.exists ? row.path : `${row.path} — folder not found`,
        disabled: !row.exists,
        onSelect: () => void openProject(row.path),
      })),
    },
    {
      id: "save-copy",
      label: "Save a copy…",
      icon: <Copy size={14} />,
      hint: saveCopyKeys.hint,
      // **The entry the busy refusal names.** `Save as` is refused while a run is in flight, and the
      // refusal says "use Save a copy" - so this must be reachable for that sentence to mean
      // anything.
      disabled: !hasProject,
      onSelect: () => askPath("save-copy"),
    },
    { type: "separator", id: "sep-close" },
    {
      id: "close",
      label: "Close project",
      icon: <X size={14} />,
      hint: closeKeys.hint,
      disabled: !hasProject,
      // The reason lives in `description`, which the menu shows on hover and gives to a screen
      // reader - `disabled` itself is only the boolean.
      description: hasProject ? "Asks about unsaved changes first." : "No project is open.",
      onSelect: closeProject,
    },
    // Settings needs no project: it is the user's, not the project's.
    { type: "separator", id: "sep-export" },
    {
      id: "export",
      label: "Export",
      icon: <Download size={14} />,
      description: "Write files into the project folder.",
      items: [
        {
          id: "export-stl",
          label: "Part STL…",
          // **Disabled with a reason, not hidden** (the spec): an entry that disappears when it
          // cannot run leaves a user wondering whether they imagined it.
          disabled: partCount === 0,
          description:
            partCount === 0
              ? "There are no parts to export yet."
              : `One STL for each of the ${partCount} parts, in exports/parts/.`,
          onSelect: exportParts,
        },
        {
          id: "export-mesh",
          label: "Mesh (OpenFOAM polyMesh)…",
          disabled: true,
          description: hasMesh
            ? "Writing the case folder is not built yet."
            : "Generate a mesh first.",
        },
        {
          id: "export-case",
          label: "OpenFOAM case folder…",
          disabled: true,
          description: hasMesh
            ? "Writing the case folder is not built yet."
            : "Generate a mesh and a case first.",
        },
        {
          id: "export-results",
          label: "Results as CSV…",
          disabled: true,
          description: "Exporting result data is not built yet; the Reports drawer shows the numbers.",
        },
      ],
    },
    { type: "separator", id: "sep-settings" },
    { id: "settings", label: "Settings…", icon: <Settings2 size={14} />, hint: settingsKeys.hint, onSelect: () => runAction("app.settings") },
  ];

  // **The dialogs that have their own component are excluded here**, or their entry renders twice:
  // `new` (New project), `open` (the project browser) and `close` (the dirty prompt). Measured with
  // `open` missing from this list: the file-prompt dialog drew ON TOP of the browser and swallowed
  // every click, so the Samples tab could not be reached at all.
  const spec = prompt && !["new", "open", "close"].includes(prompt) ? PROMPTS[prompt] : null;
  const initial = prompt === "units" ? importUnit : prompt === "save-as" && project ? (project.path ?? `${project.name}.stokes`) : "";
  const cancel = () => {
    // The units question is the one prompt with a file waiting behind it: backing out of it ends an import, and says so.
    const waiting = awaitingUnits.current;
    awaitingUnits.current = null;
    setPrompt(null);
    if (prompt === "units") pushToast("info", importCancelledText(waiting));
  };
  const submit = (value: string) => {
    const which = prompt;
    setPrompt(null);
    // "new" is NOT here: the New dialog needs two fields and its own resolved path, so it calls
    // `create` directly and `PromptDialog` never sees it.
    if (which === "open") void openProject(value);
    else if (which === "save-as") void saveAs(value);
    else if (which === "import-geometry") startImport(unquote(value));
    else if (which === "units") {
      const path = awaitingUnits.current;
      awaitingUnits.current = null;
      if (path) void importFile(path, value);
    }
  };

  return (
    <>
      <button
        ref={trigger}
        type="button"
        className="project-btn no-drag"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={`Project menu, ${project?.name ?? "no project"}`}
        onClick={() => {
          // **The lists are refreshed as the menu OPENS.** `Open recent` shows them inline, and
          // `project.recent` recomputes `exists` on every call - so a list fetched once at mount
          // would offer a project whose folder had since been moved.
          if (!open) refreshLists();
          setOpen((was) => !was);
        }}
      >
        <span className="truncate">{project?.name ?? "No project"}</span>
        <ChevronDown size={14} className="muted shrink-0" />
      </button>
      <input
        ref={fileInput}
        type="file"
        hidden
        tabIndex={-1}
        aria-hidden="true"
        accept={acceptList(formats)}
        data-ws="import-file-input"
        onChange={(e) => {
          const file = e.currentTarget.files?.[0];
          e.currentTarget.value = ""; // the same file again must fire a change
          if (file) void importUploaded(file);
        }}
      />
      <Menu open={open} anchor={trigger} onClose={() => setOpen(false)} items={items} label="Project" placement="bottom-start" />
      {/*
        **The dirty-project prompt** (menu spec rule 2): Save / Don't save / Cancel. It is a small
        dialog of its own rather than a `PromptDialog` because there is no field - the three answers
        ARE the content, and Cancel must be reachable by Escape.
      */}
      {prompt === "close" && (
        <div
          className="scrim"
          onPointerDown={(e) => {
            if (e.target === e.currentTarget) setPrompt(null);
          }}
        >
          <div className="dialog glass" role="dialog" aria-modal="true" aria-labelledby="close-title">
            <h2 id="close-title" className="mb-3 text-[15px] font-semibold">
              Save changes to {project?.name ?? "this project"}?
            </h2>
            <p className="mb-3 text-[12.5px]" style={{ color: "var(--text-muted)" }}>
              Closing without saving loses your changes.
            </p>
            <div className="flex justify-end gap-2">
              <button type="button" className="btn no-drag" onClick={() => setPrompt(null)}>
                Cancel
              </button>
              <button
                type="button"
                className="btn no-drag"
                data-ws="close-discard"
                onClick={() => {
                  setPrompt(null);
                  void run("project.close", {}, () => "Project closed");
                }}
              >
                Don't save
              </button>
              <button
                type="button"
                className="btn primary no-drag"
                data-ws="close-save"
                onClick={() => {
                  setPrompt(null);
                  if (hasPath) {
                    void run("project.save", {}, () => "Saved").then(() => {
                      void run("project.close", {}, () => "Project closed");
                    });
                  } else {
                    askPath("save-as");
                  }
                }}
              >
                Save
              </button>
            </div>
          </div>
        </div>
      )}
      <OpenProjectDialog
        apiBase={apiBase}
        token={token}
        open={prompt === "open"}
        recent={recent}
        samples={samples}
        load={loadFolder}
        onForget={forgetRecent}
        onOpen={(path) => void openProject(path)}
        onOpenSample={(id) => void openSample(id)}
        onCancel={cancel}
      />
      <FolderBrowser
        open={browsing}
        title="Where to create the project"
        start={newDefaults.folder}
        load={loadFolder}
        onChoose={(path) => {
          setChosenFolder(path);
          setBrowsing(false);
        }}
        onCancel={() => setBrowsing(false)}
      />
      <NewProjectDialog
        open={prompt === "new"}
        defaultFolder={newDefaults.folder}
        resolvedPath={newDefaults.path}
        collides={newDefaults.exists}
        problem={newDefaults.problem}
        // **Browse is always offered.** With a native host dialog it opens that; without one - the
        // browser, which is where the owner works - it opens the in-app browser over
        // `project.browse`. The first version passed `undefined` in a browser, so the button the
        // spec asks for was simply absent.
        onBrowse={() => {
          if (!host.canPickFolders) {
            setBrowsing(true);
            return;
          }
          // **The host's answer is informational**: `project.new` takes one `path`, and the dialog
          // composes it from the folder plus the name. Asking the server to resolve a folder as if
          // it were a name produced a path under `/projects/<folder>/` - measured.
          void host.pickFolder("Where to create the project", newDefaults.folder).then((picked) => {
            if (picked) setChosenFolder(picked);
          });
        }}
        chosenFolder={chosenFolder}
        onNameChange={resolveName}
        onSubmit={create}
        onCancel={cancel}
      />
      <PromptDialog
        key={prompt ?? "none"}
        open={spec !== null}
        title={spec?.title ?? ""}
        label={spec?.label ?? ""}
        placeholder={spec?.placeholder}
        help={spec?.help}
        confirmLabel={spec?.confirm}
        initialValue={initial}
        onSubmit={submit}
        onCancel={cancel}
      />
    </>
  );
}
