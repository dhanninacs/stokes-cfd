import { useCallback, useEffect, useRef, useState } from "react";
import { BookOpen, Box, FilePlus, FolderOpen, GraduationCap, Settings } from "lucide-react";
import { useStore } from "../state/store";
import { NewProjectDialog, type NewProjectValues } from "../components/NewProjectDialog";
import { OpenProjectDialog, previewUrl, type RecentProject, type SampleInfo } from "../components/OpenProjectDialog";
import { FolderBrowser, type BrowseReply } from "../components/FolderBrowser";
import { host } from "./host";
import TutorialsCard from "./TutorialsCard";
import { runAction } from "./actions";
import { ago, folderOf, monogram, recentCards } from "./startScreenModel";
import { FlowThumb } from "./FlowThumb";
import "./StartScreen.css";

/**
 * The Start screen: **New**, **Samples**, **Recent** and **Tutorials**, shown when no project is
 * open. The server opens no project on launch, so this is the first thing a user sees.
 *
 * **Why it is a screen and not just a menu.** A menu answers "what shall I do with the project I have
 * open"; with nothing open there is no such project, and the three things a user wants - start
 * something, open something they had, try an example - deserve to be visible rather than behind a
 * click. The spec asks for it both ways: this screen when nothing is open, and a menu entry to come
 * back to it.
 *
 * New and Open are the primary paths. Examples are explicit choices in Explore and learn;
 * the tutorial catalogue stays collapsed until requested. No example is loaded in the background.
 *
 * **A sample opens as a COPY** and this screen says so, because a user who believes they are editing
 * the shipped example would be wrong.
 *
 * **Laid out as a tool's home, not a landing page** (owner, 2026-11-03: "I don't like the generic, vibe coded
 * look"). The hero headline, tagline and arrow-chained step names went; the actions sit in a rail at the left and
 * the page leads with the user's own projects, each card carrying the model's saved thumbnail, its folder and how
 * long ago it changed: the recent list held two `pipe3` rows that only their folders tell apart. A card with no
 * thumbnail shows the viewport's own grid, not a stock icon. Words on the page are a few each; sentences are tooltips.
 */
export default function StartScreen() {
  const connected = useStore((s) => s.connected);
  /** The server's base URL, for the thumbnail route - "" until the client exists, which
   *  leaves the placeholders in place rather than a broken image. */
  const apiBase = useStore((s) => s.client?.apiBase ?? "");
  /** The session token, which an `<img>` can only carry in the query string. */
  const token = useStore((s) => s.client?.token ?? "");

  const execute = useStore((s) => s.execute);
  const pushToast = useStore((s) => s.pushToast);

  const [recent, setRecent] = useState<RecentProject[]>([]);
  const [tutorialsOpen, setTutorialsOpen] = useState(false);
  const [samples, setSamples] = useState<SampleInfo[]>([]);
  const [dialog, setDialog] = useState<"new" | "open" | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const examplesRef = useRef<HTMLElement>(null);
  const tutorialsRef = useRef<HTMLDetailsElement>(null);

  const [newDefaults, setNewDefaults] = useState({ folder: "", path: "", exists: false, problem: null as string | null });
  const [chosenFolder, setChosenFolder] = useState("");
  /** The in-app folder browser over `project.browse`, for a host with no native folder dialog. */
  const [browsing, setBrowsing] = useState(false);

  const refresh = useCallback(() => {
    void execute("project.recent")
      .then((r) => setRecent((r as { projects?: RecentProject[] }).projects ?? []))
      .catch(() => setRecent([]));
    void execute("project.samples")
      .then((r) => setSamples((r as { samples?: SampleInfo[] }).samples ?? []))
      .catch(() => setSamples([]));
  }, [execute]);

  useEffect(() => {
    if (connected) refresh();
  }, [connected, refresh]);

  const loadFolder = useCallback(
    async (path: string | null): Promise<BrowseReply> => (await execute("project.browse", path ? { path } : {})) as BrowseReply,
    [execute],
  );

  /** The same resolution the menu uses: the server decides the folder and the full path. */
  const resolveDefaults = useCallback(
    async (name: string, folder = "") => {
      try {
        const reply = (await execute("project.defaults", name ? (folder ? { name, folder } : { name }) : {})) as {
          folder?: string;
          path?: string;
          exists?: boolean;
        };
        setNewDefaults({ folder: reply.folder ?? "", path: reply.path ?? "", exists: reply.exists === true, problem: null });
      } catch {
        setNewDefaults({ folder: "", path: "", exists: false, problem: null });
      }
    },
    [execute],
  );

  const create = async (values: NewProjectValues) => {
    const folder = values.folder.trim();
    const params: Record<string, unknown> = { name: values.name };
    if (folder) params.path = `${folder.replace(/\/+$/, "")}/${values.name}`;
    try {
      const info = (await execute("project.new", params)) as { name: string };
      pushToast("ok", `Created project ${info.name}`);
      setDialog(null);
    } catch {
      // `execute` has already logged and toasted the reason; the dialog stays open.
    }
    return null;
  };

  const open = async (path: string) => {
    try {
      const info = (await execute("project.open", { path })) as { name: string };
      pushToast("ok", `Opened ${info.name}`);
      setDialog(null);
    } catch {
      /* reported by execute */
    }
  };

  const openSample = async (id: string) => {
    try {
      const info = (await execute("project.sample_open", { sample: id })) as { name: string };
      pushToast("ok", `Opened a copy: ${info.name}`);
      setDialog(null);
    } catch {
      /* reported by execute */
    }
  };

  const cards = recentCards(recent);
  const now = Date.now();
  const startNew = () => {
    setDialog("new");
    setChosenFolder("");
    void resolveDefaults("");
  };
  const showTutorials = () => {
    const fold = tutorialsRef.current;
    if (fold === null) return;
    fold.open = true;
    fold.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  return (
    <div className="start-screen" data-ws="start-screen">
      <aside className="start-rail" aria-label="Start">
        <div className="start-brand">
          <img src="/favicon.png" alt="" width={30} height={30} draggable={false} />
          <span>
            <strong>Stokes</strong>
            <small>OpenFOAM workspace</small>
          </span>
        </div>
        <nav className="start-actions" aria-label="Start a project">
          <button type="button" className="start-action start-action--primary no-drag" disabled={!connected} data-ws="start-empty" title="Bring your CAD and set up a simulation from scratch" onClick={startNew}>
            <FilePlus size={16} aria-hidden="true" /> New project
          </button>
          <button type="button" className="start-action no-drag" disabled={!connected} title="Continue an existing Stokes project" onClick={() => setDialog("open")}>
            <FolderOpen size={16} aria-hidden="true" /> Open...
          </button>
          <button type="button" className="start-action no-drag" title="Open a copy of a ready-made example" onClick={() => examplesRef.current?.scrollIntoView({ behavior: "smooth", block: "start" })}>
            <Box size={16} aria-hidden="true" /> Examples
          </button>
          <button type="button" className="start-action no-drag" title="Import a case from the OpenFOAM tutorials" onClick={showTutorials}>
            <GraduationCap size={16} aria-hidden="true" /> Tutorials
          </button>
        </nav>
        <div className="start-rail-foot">
          <span className="start-status" data-connected={connected} role="status" title={connected ? "The Stokes server is running" : "Waiting for the Stokes server"}>
            <span aria-hidden="true" /> {connected ? "Connected" : "Connecting..."}
          </span>
          <button type="button" className="start-action start-action--quiet no-drag" onClick={() => runAction("app.settings")}>
            <Settings size={15} aria-hidden="true" /> Settings
          </button>
        </div>
      </aside>

      <main className="start-main">
        <section className="start-section" aria-labelledby="start-recent-title">
          <header className="start-section-head">
            <h2 id="start-recent-title">Recent</h2>
            {recent.length > 0 && <span className="start-count">{recent.length}</span>}
            <button type="button" className="link no-drag" disabled={!connected} onClick={() => setDialog("open")}>Show all</button>
          </header>
          {cards.length === 0 ? (
            <div className="start-empty" title="Projects you create or open appear here">
              <p>No recent projects</p>
              <button type="button" className="start-action start-action--primary no-drag" disabled={!connected} onClick={startNew}>
                <FilePlus size={16} aria-hidden="true" /> New project
              </button>
            </div>
          ) : (
            <ul className="start-grid">
              {cards.map((row) => {
                const when = ago(row.modified ?? row.opened, now);
                const folder = folderOf(row.path);
                const url = row.exists && row.has_preview === true ? previewUrl(apiBase, row.path, token) : "";
                return (
                  <li key={row.path}>
                    <button type="button" className="start-card no-drag" disabled={!connected || !row.exists} data-missing={!row.exists || undefined} title={row.exists ? row.path : `${row.path} (folder not found)`} data-ws="start-recent" onClick={() => void open(row.path)}>
                      <span className="start-thumb" aria-hidden="true">
                        <FlowThumb seed={row.path}>{monogram(row.name)}</FlowThumb>
                        {url !== "" && <img src={url} alt="" draggable={false} onError={(e) => { e.currentTarget.style.display = "none"; }} />}
                      </span>
                      <span className="start-card-body">
                        <strong>{row.name}</strong>
                        <small>{row.exists ? [folder, when].filter(Boolean).join(" · ") : "Not found"}</small>
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </section>

        <section ref={examplesRef} className="start-section" aria-labelledby="start-examples-title">
          <header className="start-section-head">
            <h2 id="start-examples-title">Examples</h2>
            <span className="start-hint" title="An example opens as a separate copy; the original stays as shipped">Opens a copy</span>
          </header>
          {samples.length === 0 ? (
            <p className="start-hint">None installed</p>
          ) : (
            <ul className="start-grid">
              {samples.map((sample) => (
                <li key={sample.id}>
                  <button type="button" className="start-card no-drag" disabled={!connected} title={sample.description || sample.title} data-ws="start-sample" onClick={() => void openSample(sample.id)}>
                    <span className="start-thumb start-thumb--sample" aria-hidden="true"><FlowThumb seed={sample.id}><Box size={18} /></FlowThumb></span>
                    <span className="start-card-body">
                      <strong>{sample.title}</strong>
                      <small>Example</small>
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>

        {/*
          **The card mounts only once the fold is open.** Mounted closed, it asked `tutorial.catalogue`
          on connect, and classifying a 260-case tutorial tree is tens of seconds on the server's loop:
          measured, the window sat on this screen with a project already open until the walk ended.
        */}
        <details ref={tutorialsRef} className="start-section start-tutorials" onToggle={(e) => setTutorialsOpen((e.currentTarget as HTMLDetailsElement).open)}>
          <summary className="start-section-head">
            <h2><BookOpen size={14} aria-hidden="true" /> OpenFOAM tutorials</h2>
            <span className="start-hint" title="Import any case of the OpenFOAM 14 tutorial tree as a project">Browse</span>
          </summary>
          {tutorialsOpen && <TutorialsCard execute={execute} connected={connected} className="start-tutorial-card" onOpened={(outcome) => pushToast(outcome.ok ? "ok" : "error", outcome.text)} />}
        </details>
        {problem && <p className="start-problem" role="alert">{problem}</p>}
      </main>

      <NewProjectDialog
        open={dialog === "new"}
        defaultFolder={newDefaults.folder}
        resolvedPath={newDefaults.path}
        collides={newDefaults.exists}
        problem={newDefaults.problem}
        chosenFolder={chosenFolder}
        // **Browse, as the project menu's New dialog has it**: the host's native folder dialog when
        // there is one (the same `host.pickFolder` the menu uses), else the in-app browser over
        // `project.browse`. Without this prop the Start screen's dialog had no Browse button at all.
        onBrowse={() => {
          if (!host.canPickFolders) {
            setBrowsing(true);
            return;
          }
          void host.pickFolder("Where to create the project", chosenFolder || newDefaults.folder).then((picked) => {
            if (picked) setChosenFolder(picked);
          });
        }}
        onNameChange={(name, folder) => void resolveDefaults(name, folder)}
        onSubmit={create}
        onCancel={() => setDialog(null)}
      />
      <FolderBrowser
        open={browsing}
        title="Where to create the project"
        start={chosenFolder || newDefaults.folder}
        load={loadFolder}
        onChoose={(path) => {
          setChosenFolder(path);
          setBrowsing(false);
        }}
        onCancel={() => setBrowsing(false)}
      />
      <OpenProjectDialog
        apiBase={apiBase}
        token={token}
        open={dialog === "open"}
        recent={recent}
        samples={samples}
        load={loadFolder}
        onForget={(path) => {
          void execute("project.recent.remove", { path }).then(refresh).catch(() => setProblem("That entry could not be removed."));
        }}
        onOpen={(path) => void open(path)}
        onOpenSample={(id) => void openSample(id)}
        onCancel={() => setDialog(null)}
      />
    </div>
  );
}
