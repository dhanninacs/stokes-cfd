import { useCallback, useEffect, useMemo, useState } from "react";
import { Loader2, Plus, Server, Trash2 } from "lucide-react";
import { Button, Switch } from "../components/ui";
import { confirms, CONFIRM_CHARS } from "./fingerprint";
import { isRemoteHost, type SshHostRow } from "./hostRows";
import { useStore } from "../state/store";

/**
 * The mesh hosts: this computer, the SSH machines in `hosts.json`, and WSL on Windows.
 *
 * **Adding a host and trusting it are two steps, and this screen keeps them apart.** `host.add`
 * records an address; the key is confirmed by a person reading a fingerprint and typing part of it
 * back. One flow that did both would have to trust whatever the network answered, which is the whole
 * thing the confirmation exists to prevent - so the fingerprint is *shown* and the user's typing is
 * what `host.trust` compares against.
 *
 * **A host that cannot be used says why, in its own words.** `available: false` with a reason is an
 * answer; an empty list with no explanation is what a user reports as "it does not work".
 */
/** One field of the Add-host form, with the value the user typed. */
interface Draft {
  name: string;
  host: string;
  user: string;
  port: string;
  key: string;
}

const EMPTY: Draft = { name: "", host: "", user: "", port: "22", key: "" };

export default function HostsSection() {
  const execute = useStore((s) => s.execute);
  const pushToast = useStore((s) => s.pushToast);
  const [hosts, setHosts] = useState<SshHostRow[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [fingerprint, setFingerprint] = useState<{ host: string; value: string } | null>(null);
  const [typed, setTyped] = useState("");
  const [replace, setReplace] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  const connected = useStore((s) => s.connected);
  const refresh = useCallback(() => {
    // **Waits for the connection.** `execute` throws "not connected" when the client is not up
    // yet, and calling it on mount produced two ERROR entries in the console drawer with a red
    // badge - measured by opening Settings within a second of load, which is what a fast user
    // does. A red dot reads as "broken"; the panel simply has nothing to ask yet.
    if (!connected) return;
    void execute("host.list")
      .then((reply) => setHosts(((reply as { hosts?: SshHostRow[] }).hosts ?? []).filter(Boolean)))
      .catch(() => setHosts([]));
  }, [connected, execute]);

  // The refresh itself checks `connected`, so this effect re-runs when the socket comes up.
  useEffect(refresh, [refresh]);

  /** The remote hosts only: `local` is not removable and WSL is not an ssh record. */
  const remote = useMemo(() => hosts.filter(isRemoteHost), [hosts]);

  const add = async () => {
    if (draft === null) return;
    setProblem(null);
    try {
      await execute("host.add", {
        name: draft.name.trim(),
        host: draft.host.trim(),
        user: draft.user.trim(),
        port: Number(draft.port) || 22,
        key: draft.key.trim(),
      });
      // **Then the fingerprint, never trusting on the user's behalf.** The next step SHOWS the key
      // and asks them to confirm it; `host.trust` refuses anything that does not match.
      const shown = (await execute("host.fingerprint", { host: draft.name.trim() })) as {
        fingerprint?: string;
      };
      setFingerprint({ host: draft.name.trim(), value: shown.fingerprint ?? "" });
      setDraft(null);
      setTyped("");
      setReplace(false);
      refresh();
    } catch (error) {
      setProblem(error instanceof Error ? error.message : String(error));
    }
  };

  const trust = async () => {
    if (fingerprint === null) return;
    try {
      await execute("host.trust", {
        host: fingerprint.host,
        fingerprint: fingerprint.value,
        replace,
      });
      pushToast("ok", `${fingerprint.host} trusted`);
      setFingerprint(null);
      refresh();
    } catch (error) {
      setProblem(error instanceof Error ? error.message : String(error));
    }
  };

  const forget = async (name: string) => {
    setBusy(name);
    try {
      await execute("host.remove", { host: name });
      pushToast("ok", `${name} removed`);
      refresh();
    } catch (error) {
      setProblem(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="flex flex-col gap-3" data-ws="hosts-section">
      {/*
        **No "This computer" row here** (claude-win2's 2130 item 1). The Compute section's host list
        is directly above this one and already shows it with its status and its "use for new meshes"
        radio; a second copy said nothing new and read as a duplicate - which is what it was.
      */}

      {/* ------------------------------------------------------------- remote hosts */}
      <section>
        <h3 className="flex items-center gap-2 text-[12.5px] font-semibold">
          <Server size={13} aria-hidden="true" /> Remote hosts
        </h3>
        <p
          className="muted mt-0.5 text-[11.5px]"
          title="Machines reached over SSH. A host is added first and trusted separately, by checking its key."
        >
          Machines reached over SSH.
        </p>
        <ul className="mt-2 list-none p-0" aria-label="Remote hosts">
          {remote.map((row) => (
            <li key={row.name} className="flex items-center gap-2 py-1 text-[12.5px]" data-ws="host-row">
              <span className="flex-1 truncate">
                {row.name}
                <span className="mono muted ml-2 text-[11px]">
                  {row.user}@{row.host}
                  {row.port && row.port !== 22 ? `:${row.port}` : ""}
                </span>
              </span>
              <Button
                variant="ghost"
                className="ws-small"
                disabled={busy !== null}
                onClick={() => void forget(row.name)}
              >
                <Trash2 size={12} /> Remove
              </Button>
            </li>
          ))}
          {remote.length === 0 && (
            <li className="muted text-[12px]">No remote hosts yet. Add one below.</li>
          )}
        </ul>
      </section>

      {/* ------------------------------------------------------------- add */}
      {fingerprint === null && draft === null && (
        <Button variant="ghost" className="ws-small self-start" onClick={() => setDraft({ ...EMPTY })} data-ws="host-add">
          <Plus size={12} /> Add host…
        </Button>
      )}

      {draft !== null && (
        <div className="flex flex-col gap-1.5" data-ws="host-draft">
          <p className="text-[12.5px] font-semibold">Add a remote host</p>
          {(
            [
              ["name", "Name", "buildbox"],
              ["host", "Address", "10.0.0.5 or buildbox.example.com"],
              ["user", "User", "cfd"],
              ["port", "SSH port", "22"],
              ["key", "Identity file (optional)", "~/.ssh/id_ed25519"],
            ] as const
          ).map(([field, label, placeholder]) => (
            <label key={field} className="flex items-center gap-2 text-[12px]">
              <span className="muted w-[150px] shrink-0">{label}</span>
              <input
                className="field"
                value={draft[field]}
                placeholder={placeholder}
                spellCheck={false}
                onChange={(e) => setDraft({ ...draft, [field]: e.target.value })}
              />
            </label>
          ))}
          <div className="flex gap-1.5">
            <Button variant="primary" className="ws-small" onClick={() => void add()}>
              Next: check its key
            </Button>
            <Button variant="ghost" className="ws-small" onClick={() => setDraft(null)}>
              Cancel
            </Button>
          </div>
        </div>
      )}

      {/* ------------------------------------------------------------- the fingerprint */}
      {fingerprint !== null && (
        <div className="flex flex-col gap-1.5" data-ws="host-fingerprint">
          <p className="text-[12.5px] font-semibold">Check {fingerprint.host}&apos;s key</p>
          <p
            className="muted text-[11.5px]"
            title="Compare this with what whoever runs the machine told you, then type its first characters. Nothing is trusted until it matches."
          >
            Type its first {CONFIRM_CHARS} characters to trust it.
          </p>
          <code className="mono block truncate text-[11.5px]" title={fingerprint.value}>
            {fingerprint.value || "(no key was returned)"}
          </code>
          <input
            className="field"
            value={typed}
            placeholder={`the first ${CONFIRM_CHARS} characters`}
            spellCheck={false}
            aria-label="The first characters of the fingerprint"
            onChange={(e) => setTyped(e.target.value)}
          />
          <label className="flex items-center gap-2 text-[11.5px]">
            <Switch checked={replace} onChange={setReplace} label="The key changed; replace it" />
          </label>
          <div className="flex gap-1.5">
            <Button
              variant="primary"
              className="ws-small"
              // **Disabled until it matches**, so the button cannot be pressed past the check and the
              // user sees the rule rather than a refusal after the fact.
              disabled={!confirms(fingerprint.value, typed)}
              onClick={() => void trust()}
            >
              Trust this host
            </Button>
            <Button
              variant="ghost"
              className="ws-small"
              onClick={() => {
                setFingerprint(null);
                setProblem(null);
              }}
            >
              Cancel
            </Button>
          </div>
        </div>
      )}

      {busy !== null && (
        <p className="muted flex items-center gap-2 text-[11.5px]">
          <Loader2 size={12} className="spin" /> {busy}…
        </p>
      )}
      {problem !== null && (
        <p className="text-[11.5px]" style={{ color: "var(--danger)" }} role="alert">
          {problem}
        </p>
      )}
    </div>
  );
}
