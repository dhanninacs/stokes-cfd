import { useCallback, useEffect, useMemo, useState } from "react";
import { AlertCircle, CheckCircle2, Cpu, FolderSearch, Loader2, RefreshCw, Server } from "lucide-react";
import { PromptDialog } from "../components/PromptDialog";
import { useStore } from "../state/store";
import { Button, Switch } from "../components/ui";

/**
 * Where OpenFOAM runs, and which install to use - the first section of Settings.
 *
 * **Everything is pre-filled.** The spec's point is that a user should not have to know where their
 * install is: the probe finds it, the newest supported one is selected, and the common case requires
 * no typing at all. What this screen adds is the ability to see the choice and change it.
 *
 * **One list of hosts, with a Default.** A host is "this computer", a WSL distribution, or a remote
 * machine; the row marked Default is what a new mesh or run uses, and "Auto" picks the best available
 * so a user who does not care never opens this screen.
 *
 * **Nothing here runs an install.** Every value comes from `host.probe`, which reads; the chosen
 * install becomes an environment script the agent sources inside the launcher it already has. A scan
 * that finds ten installs changes nothing until one is selected - which is what makes widening the
 * search safe.
 *
 * **A host that cannot mesh says why, in its own words.** `available: false` with a reason is an
 * answer; the alternative - an empty list and no explanation - is what a user reports as "it does not
 * work".
 */
export interface HostRow {
  name: string;
  runner: string;
  available: boolean;
  why: string;
}

export interface InstallRow {
  id: string;
  bashrc: string;
  project: string;
  version: string;
  flavour: string;
  supported: boolean;
  found_by: string;
  why: string;
}

/** What `compute.get` and `compute.set_default` answer: the choice and the effective one. */
interface ComputeChoice {
  host?: string;
  install?: string;
  auto?: boolean;
  effective_host?: string;
  effective_install?: string;
}

interface Probe {
  host: string;
  available: boolean;
  why: string;
  record: {
    hostname?: string;
    system?: string;
    cpu_count?: number;
    openfoam_candidates?: string[];
    installs?: InstallRow[];
    default_install_id?: string | null;
  } | null;
}

/** What `found_by` means, in words a user can act on. */
const FOUND_BY: Record<string, string> = {
  STOKES_OPENFOAM_BASHRC: "set by the environment",
  "standard-prefix": "found in a system location",
  "home-scan": "found in your home folder",
  "user-added": "added by you",
  "": "",
};

/** The version this build drives, so the reason a row is greyed can name it. */
const SUPPORTED_LABEL = "OpenFOAM 14";

export default function ComputeSection() {
  const execute = useStore((s) => s.execute);
  const pushToast = useStore((s) => s.pushToast);
  const connected = useStore((s) => s.connected);
  const [hosts, setHosts] = useState<HostRow[]>([]);
  const [probes, setProbes] = useState<Record<string, Probe>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [chosen, setChosen] = useState<Record<string, string>>({});
  /** The Add-install prompt: open, and what the last attempt was refused for. */
  const [adding, setAdding] = useState(false);
  /**
   * The `compute.get` reply: the choice, and the installs **with the user's own marked**.
   *
   * **Two calls, because they answer two questions.** `host.probe` says what a MACHINE has - its
   * candidates, its cores - and `compute.get` says what is CONFIGURED and marks the install the user
   * chose (`found_by: "user-added"`). Only the server can mark that: a scan sees a machine, not a
   * preference.
   */
  const [configured, setConfigured] = useState<{ install?: string; installs?: InstallRow[] } | null>(null);
  /** The host choice, from `compute.get` and `compute.set_default`: which row is the default. */
  const [choice, setChoice] = useState<ComputeChoice | null>(null);
  const current = useMemo(() => hosts.find((h) => h.available) ?? hosts[0] ?? null, [hosts]);
  const probe = current ? probes[current.name] : undefined;
  // **`compute.get`'s list when it has one**, because it is the one that marks the user's choice;
  // the probe's list is the fallback for the moment before that call returns.
  const installs = configured?.installs ?? probe?.record?.installs ?? [];
  const candidates = probe?.record?.openfoam_candidates ?? [];
  const selected = current ? chosen[current.name] : undefined;
  // **From the server, not from the first available host**, so the radio agrees with the Mesh panel
  // line, which reads the same reply. `effective_host` already applies the "Auto" rule.
  const defaultHost = choice?.effective_host ?? current?.name;


  const probeHost = useCallback(
    async (host: string, refresh = false) => {
      setBusy(host);
      try {
        const answer = (await execute("host.probe", { host, refresh })) as Probe;
        setProbes((was) => ({ ...was, [host]: answer }));
        // **And the configured list, which carries the "added by you" mark.** Refreshed with the
        // probe so the two cannot disagree about which installs exist.
        const config = (await execute("compute.get")) as ComputeChoice & { installs?: InstallRow[] };
        setConfigured(config);
        setChoice(config);
        const found = answer.record?.default_install_id ?? answer.record?.installs?.[0]?.id;
        if (found !== undefined && found !== null) setChosen((was) => ({ ...was, [host]: was[host] ?? found }));
      } catch {
        // `execute` has logged and toasted it; the row shows its own status below.
      } finally {
        setBusy(null);
      }
    },
    [execute],
  );

  /**
   * Make a host the default for meshes and solves that do not name one, through
   * `compute.set_default`.
   *
   * **The reply is re-read rather than assumed**, because `auto` is the server's rule: choosing a
   * host sets `auto: false`, and the radio has to show that rather than the value we hoped for.
   */
  const chooseHost = useCallback(
    async (name: string) => {
      try {
        const reply = (await execute("compute.set_default", { host: name })) as ComputeChoice;
        setChoice(reply);
        await probeHost(name, true);
      } catch {
        // `execute` has logged and toasted it; the radio keeps showing the previous choice.
      }
    },
    [execute, probeHost],
  );

  /**
   * Record a path the user typed, through `compute.add_install`.
   *
   * **The server validates, and its refusal is what the dialog shows** - "that is a folder; give the
   * environment script inside it" is the server's sentence, not a second copy here. A client-side
   * check would be a second rule to keep in step, and the one that matters is on the machine that
   * will source the file.
   *
   * Resolving to a message keeps the dialog open with it; anything else closes it, which is the
   * `PromptDialog` contract.
   */
  const addInstall = useCallback(
    async (path: string): Promise<string | null> => {
      if (current === null) return "No host is chosen yet.";
      try {
        const answer = (await execute("compute.add_install", {
          host: current.name,
          path: path.trim(),
        })) as { install?: string };
        pushToast("ok", `Using ${answer.install ?? path} on ${current.name}`);
        // `probeHost` re-reads `compute.get` too, so the new install arrives marked.
        await probeHost(current.name, true);
        setAdding(false);
        return null;
      } catch (error) {
        return error instanceof Error ? error.message : String(error);
      }
    },
    [current, execute, probeHost, pushToast],
  );

  useEffect(() => {
    // **Waits for the connection.** `execute` throws "not connected" when the client is not up
    // yet, and calling it on mount produced two ERROR entries in the console drawer with a red
    // badge - measured by opening Settings within a second of load, which is what a fast user
    // does. A red dot reads as "broken"; the panel simply has nothing to ask yet.
    if (!connected) return;
    void execute("host.list")
      .then((answer) => {
        const list = ((answer as { hosts?: HostRow[] }).hosts ?? []).filter(Boolean);
        setHosts(list);
        // **Probe the first usable host on open**, so the section arrives filled in rather than
        // asking the user to press a button before it says anything.
        const first = list.find((h) => h.available) ?? list[0];
        if (first) void probeHost(first.name);
      })
      .catch(() => setHosts([]));
  }, [connected, execute, probeHost]);

  const cores = probe?.record?.cpu_count ?? null;
  // **A host that cannot be used says so in words.** `available: false` with a reason is an answer;
  // an empty install list with no explanation is what a user reports as "it does not work".
  const unavailable = unavailableNote(hosts, probes);

  return (
    <div className="flex flex-col gap-4" data-ws="compute-section">
      {/* ---------------------------------------------------------------- hosts */}
      <section>
        <h3 className="flex items-center gap-2 text-[12.5px] font-semibold">
          <Server size={13} aria-hidden="true" /> Where OpenFOAM runs
        </h3>
        <p
          className="muted mt-0.5 text-[11.5px]"
          title="The machine a mesh or a solve runs on when its project does not name one. The one marked Default is used by a new mesh."
        >
          Used when a project names no host.
        </p>
        <ul className="mt-2 list-none p-0" aria-label="Mesh hosts">
          {hosts.map((host) => {
            const state = probes[host.name];
            const live = state?.available ?? host.available;
            return (
              <li key={host.name} className="flex items-center gap-2 py-1 text-[12.5px]" data-ws="host-row">
                {/*
                  **Every host row can be chosen** (claude-win2's 2130 item 2). Only "This computer"
                  carried a "Default" label before, and nothing said HOW a user picked a different
                  one - the row marked Default is what an empty Execution host resolves to, so it has
                  to be a control rather than a decoration.

                  A radio, not a button: exactly one host is the default, which is what a radio
                  means, and `compute.set_default` stores it at user scope.
                */}
                <input
                  type="radio"
                  name="compute-default-host"
                  className="shrink-0"
                  aria-label={`Use ${host.name} as the default host`}
                  checked={defaultHost === host.name}
                  // A host that cannot run must not become the default: picking it would point the
                  // Mesh panel at a machine that refuses.
                  disabled={!live || busy !== null}
                  title={live ? `Use ${host.name} as the default host` : host.why || "not available"}
                  onChange={() => void chooseHost(host.name)}
                />
                {live ? (
                  <CheckCircle2 size={13} style={{ color: "var(--ok)" }} aria-hidden="true" />
                ) : (
                  <AlertCircle size={13} style={{ color: "var(--danger)" }} aria-hidden="true" />
                )}
                <span className="flex-1 truncate">
                  {/*
                    **A WSL distribution reads as "Ubuntu (WSL)"**, not `wsl:Ubuntu` - the prefix is
                    how the list distinguishes it from an SSH host, not something a user should have
                    to decode. The spec asks for the distribution to be identifiable as WSL.
                  */}
                  {host.name === "local"
                    ? "This computer"
                    : host.name.startsWith("wsl:")
                      ? `${host.name.slice(4)} (WSL)`
                      : host.name}
                  {current?.name === host.name && <span className="muted ml-2 text-[11px]">Default</span>}
                </span>
                <span className="muted text-[11px]">
                  {live ? (state?.record?.hostname ?? "") : host.why || state?.why || "not available"}
                </span>
                <Button
                  variant="ghost"
                  className="ws-small"
                  // **Disabled on a host that cannot run**, which is the spec's "never a button that
                  // fails": a WSL row is detected with its reason, and pressing Test would ask a
                  // distribution that has no agent in it. `available: false` from the server is the
                  // authority, so this does not guess from the name.
                  disabled={busy !== null || !live}
                  title={live ? `Ask ${host.name} what it has` : host.why || "not available"}
                  onClick={() => void probeHost(host.name, true)}
                >
                  {busy === host.name ? <Loader2 size={12} className="spin" /> : <RefreshCw size={12} />} Test
                </Button>
              </li>
            );
          })}
          {hosts.length === 0 && <li className="muted text-[12px]">No hosts are configured yet.</li>}
        </ul>
      </section>

      {/* ---------------------------------------------------------------- install */}
      <section>
        <h3 className="flex items-center gap-2 text-[12.5px] font-semibold">
          <FolderSearch size={13} aria-hidden="true" /> OpenFOAM install
        </h3>
        {probe === undefined && <p className="muted mt-0.5 text-[11.5px]">Looking for an install…</p>}
        {probe !== undefined && installs.length === 0 && (
          <p className="mt-0.5 text-[11.5px]" style={{ color: "var(--text-muted)" }}>
            {probe.why || "No OpenFOAM install was found on this host."} Use{" "}
            <strong>Add install location…</strong> to point at one, or install {SUPPORTED_LABEL}.
          </p>
        )}
        {installs.length > 0 && (
          <>
            <p className="muted mt-0.5 text-[11.5px]">
              {installs.length === 1 ? "One install was found" : `${installs.length} installs were found`} — the newest
              supported one is selected.
            </p>
            <ul className="mt-2 list-none p-0" aria-label="OpenFOAM installs">
              {installs.map((install) => (
                <li key={install.id} className="py-1" data-ws="install-row">
                  <label className="flex items-start gap-2 text-[12.5px]">
                    <input
                      type="radio"
                      name="openfoam-install"
                      className="mt-0.5"
                      checked={selected === install.id}
                      // **An unsupported install is listed but not choosable**, with the reason
                      // beside it: hiding it would leave a user with an install wondering why the
                      // list is empty.
                      disabled={!install.supported}
                      onChange={() => current && setChosen((was) => ({ ...was, [current.name]: install.id }))}
                    />
                    <span className="flex-1">
                      <span className={install.supported ? "" : "muted"}>
                        {install.project} {install.version}
                        {install.flavour === "com" ? " (ESI)" : ""}
                      </span>
                      <span className="muted ml-2 text-[11px]">
                        {FOUND_BY[install.found_by] ?? install.found_by}
                      </span>
                      <span className="mono muted block truncate text-[11px]" title={install.bashrc}>
                        {install.bashrc}
                      </span>
                      {!install.supported && (
                        <span className="block text-[11px]" style={{ color: "var(--danger)" }}>
                          {install.why || `needs ${SUPPORTED_LABEL}`}
                        </span>
                      )}
                    </span>
                  </label>
                </li>
              ))}
            </ul>
          </>
        )}
        <div className="mt-2 flex flex-wrap gap-1.5">
          <Button
            variant="ghost"
            className="ws-small"
            disabled={busy !== null || current === null}
            onClick={() => current && void probeHost(current.name, true)}
          >
            <RefreshCw size={12} /> Rescan
          </Button>
          <Button
            variant="ghost"
            className="ws-small"
            disabled={current === null}
            title={
              current === null
                ? "Choose a host first"
                : `Point at an environment script on ${current.name}`
            }
            onClick={() => setAdding(true)}
          >
            Add install location…
          </Button>
        </div>
        {candidates.length > 0 && (
          <p className="muted mt-1 text-[11px]">
            Searched {candidates.length} location{candidates.length === 1 ? "" : "s"} on this host.
          </p>
        )}
      </section>

      {/* ---------------------------------------------------------------- cores */}
      <section>
        <h3 className="flex items-center gap-2 text-[12.5px] font-semibold">
          <Cpu size={13} aria-hidden="true" /> Cores
        </h3>
        <p className="muted mt-0.5 text-[11.5px]">
          {/*
            **What is DETECTED, not what is used.** The spec asks for a cores setting defaulting to
            detected-minus-one, but the mesh drivers run every step as a bare serial command
            (`snappy.py`: `argv: ["snappyHexMesh"]`, no `-parallel` and no `mpirun`), so nothing
            reads a core count today. My first version of this sentence said "a mesh or solve uses 3
            by default", which is the spec's intent and not the code's behaviour - the class of claim
            I should never ship. The stepper arrives with the parallel path; see KNOWN-ISSUES.
          */}
          {cores === null
            ? "Reading the machine's core count…"
            : `${cores} detected on this host.`}
        </p>
      </section>

      <PromptDialog
        open={adding}
        title="Add an OpenFOAM install"
        label={current === null ? "Environment script" : `Environment script on ${current.name}`}
        placeholder="/opt/openfoam14/etc/bashrc"
        help="The file that sets up OpenFOAM, such as etc/bashrc or an env.sh."
        confirmLabel="Use this install"
        onSubmit={addInstall}
        onCancel={() => setAdding(false)}
      />
      {unavailable !== null && (
        <p className="text-[11.5px]" style={{ color: "var(--danger)" }} role="status">
          {unavailable}
        </p>
      )}
    </div>
  );
}

/**
 * Why a host that is not available cannot be probed, as one line under the list.
 *
 * **A rendered value, not announced from the render.** My first version called the toast from inside
 * the render path, which fires on every re-render - a side effect where there must be none.
 */
function unavailableNote(hosts: readonly HostRow[], probes: Record<string, Probe>): string | null {
  const missing = hosts.find((h) => !h.available && probes[h.name] === undefined);
  if (missing === undefined) return null;
  return `${missing.name} cannot be used: ${missing.why || "no answer"}`;
}

export { Switch };
