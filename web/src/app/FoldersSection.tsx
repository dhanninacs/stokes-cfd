import { useCallback, useEffect, useState } from "react";
import { FolderOpen, FolderTree, HardDrive, ScrollText } from "lucide-react";
import { host } from "./host";
import { Button } from "../components/ui";
import { FolderBrowser, type BrowseReply } from "../components/FolderBrowser";
import { useStore } from "../state/store";

/**
 * Where the files go - the Settings spec §3.
 *
 * **One editable path and the rest read-only, which is the spec's own split.** The projects folder is
 * the user's decision: it is where new projects appear, and the Start screen and the New dialog both
 * read it. Samples, cache and logs are consequences of that decision and of the open project, so they
 * are shown with a way to open them rather than a field to change - editing the cache folder of a
 * project that already has one would be a way to lose the B-reps that live in it.
 *
 * **Every path shown is the machine's real path**, resolved by the server. `project.defaults` decides
 * the projects folder (the same command the New dialog uses), so what a user reads here is what the
 * next project will use - not a second computation that could disagree with it.
 */
interface FolderRow {
  id: string;
  label: string;
  /** The absolute path, or "" while it is still being asked for. */
  path: string;
  help: string;
  /** Only the projects folder is editable. */
  editable?: boolean;
}

export default function FoldersSection() {
  const execute = useStore((s) => s.execute);
  const project = useStore((s) => s.project);
  const connected = useStore((s) => s.connected);
  const [projectsFolder, setProjectsFolder] = useState("");
  const [samplesFolder, setSamplesFolder] = useState("");
  const [browsing, setBrowsing] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  const load = useCallback(
    async (path: string | null): Promise<BrowseReply> =>
      (await execute("project.browse", path ? { path } : {})) as BrowseReply,
    [execute],
  );

  useEffect(() => {
    // **Waits for the connection.** `execute` throws "not connected" when the client is not up
    // yet, and calling it on mount produced two ERROR entries in the console drawer with a red
    // badge - measured by opening Settings within a second of load, which is what a fast user
    // does. A red dot reads as "broken"; the panel simply has nothing to ask yet.
    if (!connected) return;
    // **The projects folder from the command that decides it**, so this screen and the New dialog
    // cannot name different folders.
    void execute("project.defaults")
      .then((reply) => {
        const answer = reply as { folder?: string };
        setProjectsFolder(answer.folder ?? "");
        // The samples folder is its sibling (`Samples/`), which the schema names; derived from the
        // projects path so a user who moves one sees both move.
        setSamplesFolder((answer.folder ?? "").replace(/\/Projects\/?$/, "/Samples"));
      })
      .catch(() => setNote("The projects folder could not be read."));
  }, [connected, execute]);

  /** The project's own folders, from the open project: cache, runs, imports. */
  const projectFolder = project?.path ?? "";
  const rows: FolderRow[] = [
    {
      id: "projects",
      label: "Projects",
      path: projectsFolder,
      help: "Where New project creates a folder, and where the Start screen looks.",
      editable: true,
    },
    {
      id: "samples",
      label: "Samples",
      path: samplesFolder,
      help: "Sample copies. One folder per copy, so the shipped example is never modified.",
    },
    {
      id: "cache",
      label: "Cache",
      path: projectFolder ? `${projectFolder}/cache` : "",
      help: projectFolder ? "The open project's B-reps and meshes." : "Open a project to see this.",
    },
    {
      id: "logs",
      label: "Runs and logs",
      path: projectFolder ? `${projectFolder}/runs` : "",
      help: projectFolder ? "One folder per run, with its case and its logs." : "Open a project to see this.",
    },
  ];

  const choose = async (path: string) => {
    setBrowsing(false);
    // **No command writes this yet**, and that is deliberate: the projects folder is an app-root
    // setting (`STOKES_USER_HOME`/Documents), so changing it from here needs a decision about whether
    // it moves existing projects. Recorded rather than faked with a field that forgets.
    setNote(
      `Changing the projects folder is not wired yet: it would need to decide what happens to the projects already in the old one. The folder is ${path}.`,
    );
  };

  return (
    <div className="flex flex-col gap-3" data-ws="folders-section">
      <p
        className="muted text-[11.5px]"
        title="Everything a project works with lives inside its own folder. These are the folders on this machine."
      >
        Folders on this machine.
      </p>
      <ul className="m-0 list-none p-0" aria-label="Folders">
        {rows.map((row) => (
          <li key={row.id} className="flex items-start gap-2 py-1.5" data-ws="folder-row">
            {row.editable ? (
              <FolderTree size={13} className="mt-0.5 shrink-0" aria-hidden="true" />
            ) : (
              <HardDrive size={13} className="mt-0.5 shrink-0" aria-hidden="true" />
            )}
            <span className="flex-1">
              <span className="text-[12.5px]">{row.label}</span>
              <span className="mono muted block truncate text-[11.5px]" title={row.path || row.help}>
                {row.path || row.help}
              </span>
            </span>
            {row.editable && (
              <Button variant="ghost" className="ws-small" disabled={projectsFolder === ""} onClick={() => setBrowsing(true)}>
                Browse…
              </Button>
            )}
            {row.path !== "" && (
              <Button
                variant="ghost"
                className="ws-small"
                // **Only where the host can open one.** In a browser there is no file manager to
                // hand a path to, so the button says what it can do: copy the path, which is the
                // part that is useful over an SSH tunnel.
                onClick={() => {
                  void navigator.clipboard?.writeText(row.path);
                  setNote(`${row.path} copied.`);
                }}
                title={host.canPickFolders ? "Open this folder" : "Copy this path"}
              >
                {host.canPickFolders ? <FolderOpen size={12} /> : <ScrollText size={12} />}
                {host.canPickFolders ? "Open" : "Copy"}
              </Button>
            )}
          </li>
        ))}
      </ul>
      {note !== null && (
        <p className="muted text-[11.5px]" role="status">
          {note}
        </p>
      )}
      <FolderBrowser
        open={browsing}
        title="Choose the projects folder"
        start={projectsFolder}
        load={load}
        onChoose={(path) => void choose(path)}
        onCancel={() => setBrowsing(false)}
      />
    </div>
  );
}
