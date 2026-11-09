/**
 * The Geometry check page (brief M-502 §UI 5.1): the precheck's findings, the blocking ones
 * first, each with its Fix, or a link to the node it names.
 *
 * The findings are the backend's (`mesh.precheck`), and a Fix is whatever the finding carries:
 * `fix.command` with its params when the backend knows the repair, else the row links to the
 * first node it names. **The page never invents a repair**: a finding without either shows only
 * its message, which is honest about what the backend can and cannot fix.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { CircleAlert, CircleCheck, Wrench } from "lucide-react";
import { findNode, useStore } from "../../state/store";
import { Button } from "../../components/ui";
import { canShowClear, canStartCheck, type CheckStatus } from "./geometryCheckModel";
import { Note } from "../../components/Note";

interface Finding {
  code: string;
  severity: string;
  message: string;
  nodes?: string[];
  fix?: { command: string; params?: Record<string, unknown> };
}

interface Precheck {
  findings: Finding[];
}

/** The findings in the order the page shows them: blocking first, then warnings, then info. */
export function orderedFindings(findings: Finding[]): Finding[] {
  const rank = (severity: string): number => (severity === "block" ? 0 : severity === "warn" ? 1 : 2);
  return [...findings].sort((a, b) => rank(a.severity) - rank(b.severity));
}

export default function GeometryCheckPage() {
  const execute = useStore((s) => s.execute);
  const tree = useStore((s) => s.tree);
  const connected = useStore((s) => s.connected);
  const [precheck, setPrecheck] = useState<Precheck | null>(null);
  const [status, setStatus] = useState<CheckStatus>("disconnected");
  const [fixing, setFixing] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);
  const requestId = useRef(0);

  const findings = useMemo(() => orderedFindings(precheck?.findings ?? []), [precheck]);

  // Re-asked whenever the tree object changes, even when its root group count stays the same.
  // The request id prevents an older reply from replacing the check for the current model.
  useEffect(() => {
    const id = ++requestId.current;
    let live = true;
    if (!connected) {
      setStatus("disconnected");
      setError("");
      return () => {
        live = false;
        if (requestId.current === id) requestId.current++;
      };
    }

    setStatus("loading");
    setError("");
    void execute("mesh.precheck")
      .then((answer) => {
        if (!live || id !== requestId.current) return;
        setPrecheck(answer as Precheck);
        setStatus("ready");
      })
      .catch((err: Error) => {
        if (!live || id !== requestId.current) return;
        setError(err.message);
        setStatus("error");
      });
    return () => {
      live = false;
      if (requestId.current === id) requestId.current++;
    };
  }, [tree, connected, execute, refresh]);

  const runFix = (finding: Finding) => {
    if (finding.fix === undefined || fixing !== null || status !== "ready") return;
    setFixing(finding.code);
    setError("");
    void execute(finding.fix.command, finding.fix.params ?? {})
      .then(() => setRefresh((value) => value + 1))
      .catch((err: Error) => setError(err.message))
      .finally(() => setFixing(null));
  };

  const showClear = canShowClear(status, findings.length);

  return (
    <div className="task-page" data-ws="task-geometry-check" aria-busy={status === "loading"}>
      <Note className="task-purpose" tip="Is the shape ready to mesh: a closed body, faces up to date, small enough to send?">
        Is it ready to mesh?
      </Note>
      {status === "loading" && <p className="muted task-note" role="status">Checking geometry…</p>}
      {status === "disconnected" && <p className="muted task-note" role="status">Connect to check geometry.</p>}
      {error !== "" && <p role="alert" className="mesh-error">{error}</p>}
      <Button
        variant="ghost"
        className="ws-small self-start"
        disabled={!canStartCheck(connected, status, fixing !== null)}
        onClick={() => {
          setError("");
          setRefresh((value) => value + 1);
        }}
        data-ws="geometry-check-again"
      >
        {status === "error" ? "Retry check" : "Check again"}
      </Button>
      {findings.length > 0 && (
        <ul className="task-findings">
          {findings.map((finding) => (
            <li key={finding.code} className={`task-finding task-finding-${finding.severity}`}>
              <CircleAlert size={13} className="shrink-0" aria-hidden="true" />
              <span className="task-finding-text">{finding.message}</span>
              {finding.fix !== undefined && (
                <button
                  type="button"
                  className="link no-drag"
                  disabled={fixing !== null || status !== "ready"}
                  onClick={() => runFix(finding)}
                  data-ws={`task-fix-${finding.code}`}
                >
                  <Wrench size={11} /> {fixing === finding.code ? "Fixing…" : "Fix"}
                </button>
              )}
              {finding.fix === undefined && (finding.nodes?.length ?? 0) > 0 && (
                <button
                  type="button"
                  className="link no-drag"
                  onClick={() => {
                    const node = findNode(tree, finding.nodes?.[0] ?? "");
                    if (node !== null) void useStore.getState().select(node.id);
                  }}
                >
                  Show it
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
      {showClear && (
        <p className="task-clear" data-ws="task-nothing-to-fix">
          <CircleCheck size={14} /> Nothing to fix
        </p>
      )}
    </div>
  );
}
