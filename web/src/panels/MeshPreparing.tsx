/**
 * What a press of Generate is doing before its job exists (`meshPanelModel.preparingLine`): the server's named step
 * (`mesh.preparing`: surfaces, host, case files, upload, start), the host and the seconds so far, a bar that moves by
 * step (and by bytes while the case is sent), and Cancel, which the server honours between steps and staging chunks
 * (ADR-0024 addendum 2026-11-14). The Mesh panel, the guided Mesh card and the stepper draw it in place of their
 * running line until the job's own progress arrives.
 */
import { useEffect, useState } from "react";
import { LoaderCircle } from "lucide-react";
import { Button } from "../components/ui";
import { prepareFraction, preparingLine } from "./meshPanelModel";
import { cancelMesh, useMeshContext } from "./meshRun";

export default function MeshPreparing({ showCancel = true }: { showCancel?: boolean }) {
  const busy = useMeshContext((c) => c.busy);
  const step = useMeshContext((c) => c.busyStep);
  const since = useMeshContext((c) => c.busySince);
  const host = useMeshContext((c) => c.busyHost);
  const prep = useMeshContext((c) => c.prep);
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!busy) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [busy]);
  if (!busy || step === "") return null;
  const line = preparingLine(step, since > 0 ? Math.max(0, (now - since) / 1000) : 0, host, prep.progress);
  const fraction = prepareFraction(prep.progress);
  // Cancel needs the server's preparation (not the settings call before it), and is asked once.
  const canCancel = step !== "configure" && !prep.cancelling;
  return (
    <div className="mesh-preparing" role="status" data-ws="mesh-preparing" data-step={step}>
      <p className="guided-line muted" title={line.detail}>
        <LoaderCircle size={13} className="mesh-spin" aria-hidden="true" /> <span data-ws="mesh-preparing-text">{prep.cancelling ? "Stopping the preparation" : line.text}</span>
      </p>
      <div className="mesh-bar" aria-hidden="true" data-indeterminate={fraction === null || undefined} data-tone="busy">
        <span style={{ width: `${Math.round((fraction ?? 0) * 100)}%` }} />
      </div>
      <p className="muted mesh-hint">{line.detail}</p>
      {showCancel && (
        <Button
          aria-disabled={!canCancel || undefined}
          title={prep.cancelling ? "Stopping at the next step" : step === "configure" ? "The mesh settings are being made" : "Stop preparing the case"}
          onClick={() => canCancel && void cancelMesh()}
          data-ws="mesh-preparing-cancel"
        >
          {prep.cancelling ? "Stopping…" : "Cancel"}
        </Button>
      )}
    </div>
  );
}
