/**
 * The offer of unsaved work an earlier session left (`project.info.recovery`): Restore or Discard.
 *
 * Autosave (`autosave.ts`) keeps that work in the project's recovery copy; when the server stopped
 * before a Save, the next open finds it. The card stays until the user answers, because quietly
 * dropping the offer would lose exactly the work autosave exists to keep. It wears the getting-started
 * card's look, in the same place, so it adds no new style.
 */
import { useState } from "react";
import { Button } from "../components/ui";
import { useStore } from "../state/store";
import { recoveryWhen } from "./autosaveModel";

export default function RecoveryCard() {
  const recovery = useStore((s) => s.project?.recovery ?? null);
  const execute = useStore((s) => s.execute);
  const [busy, setBusy] = useState(false);
  if (recovery === null) return null;
  const answer = (method: "project.recover" | "project.discard_recovery") => {
    setBusy(true);
    void execute(method, {})
      .catch(() => undefined) // the store has already shown the refusal
      .finally(() => setBusy(false));
  };
  return (
    <div className="hint-card glass" role="alertdialog" aria-label="Unsaved changes found" data-ws="recovery-card">
      <span>
        <b>Unsaved changes</b> from {recoveryWhen(recovery)} were kept when Stokes stopped.
      </span>
      <Button variant="primary" disabled={busy} onClick={() => answer("project.recover")} data-ws="recovery-restore">
        Restore
      </Button>
      <Button disabled={busy} onClick={() => answer("project.discard_recovery")} data-ws="recovery-discard">
        Discard
      </Button>
    </div>
  );
}
