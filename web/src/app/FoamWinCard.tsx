import { useEffect, useState } from "react";
import { Download, Loader2, X } from "lucide-react";
import { Dialog } from "../components/Dialog";
import { Note } from "../components/Note";
import { Button } from "../components/ui";
import { useStore } from "../state/store";
import {
  megabytes,
  offerFoamWin,
  progressFraction,
  progressLine,
  type FoamWinProgress,
  type FoamWinStatus,
  type LocalProbe,
} from "./foamWinInstall";

/** The card's full explanation, in its tooltip (notes on a panel stay at eight words or fewer). */
const ABOUT =
  "Stokes downloads FoamWin, OpenFOAM 14 built for Windows, from its GitHub release, checks the archive's " +
  "SHA-256 and installs it in your user folder. No administrator rights are needed, and no WSL. " +
  "Runs on more than one core also need Microsoft MPI.";

/**
 * "This computer has no OpenFOAM": offer to download and install FoamWin, the native Windows OpenFOAM 14
 * (ADR-0046). Shown only on a Windows computer whose probe found no supported install.
 *
 * **Nothing downloads without a confirmation that names it**: the release, the archive, its size and where
 * it comes from. The download runs on the server (`host.foamwin_install`), which checks the archive's
 * SHA-256 against the digest GitHub publishes, runs FoamWin's own setup into the default per-user folder
 * and needs no administrator rights. That folder is where discovery looks, so once it finishes a rescan
 * lists the install and it is selected like any other.
 */
export default function FoamWinCard({ probe, onInstalled }: { probe: LocalProbe | null; onInstalled: () => void }) {
  const execute = useStore((s) => s.execute);
  const client = useStore((s) => s.client);
  const pushToast = useStore((s) => s.pushToast);
  const offer = offerFoamWin(probe);
  const [status, setStatus] = useState<FoamWinStatus | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [progress, setProgress] = useState<FoamWinProgress | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!offer) return;
    void execute("host.foamwin", { release: true })
      .then((answer) => {
        const s = answer as FoamWinStatus;
        setStatus(s);
        if (s.running) setProgress({ stage: "release", done: 0, total: 0 });
      })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
  }, [offer, execute]);

  useEffect(() => {
    if (client === null) return;
    return client.onEvent((event, data) => {
      if (event === "host.foamwin_progress") setProgress(data as unknown as FoamWinProgress);
      if (event === "host.foamwin_installed") {
        setProgress(null);
        pushToast("ok", "FoamWin is installed; Stokes runs OpenFOAM on this computer now");
        onInstalled();
      }
      if (event === "host.foamwin_failed") {
        setProgress(null);
        const failure = data.error as { message?: string } | undefined;
        setError(failure?.message ?? "The install did not complete");
      }
    });
  }, [client, onInstalled, pushToast]);

  if (!offer) return null;
  const release = status?.release;
  const running = progress !== null;
  const fraction = progressFraction(progress);

  const start = () => {
    setConfirming(false);
    setError("");
    setProgress({ stage: "release", done: 0, total: 0 });
    execute("host.foamwin_install", {}).catch((e: unknown) => {
      setProgress(null);
      setError(e instanceof Error ? e.message : String(e));
    });
  };

  return (
    <div className="mt-2 rounded-md border p-2.5" style={{ borderColor: "var(--border)" }} data-ws="foamwin-card">
      <p className="text-[12px] font-semibold">Run OpenFOAM on this computer</p>
      <Note className="muted mt-0.5 text-[11.5px]" tip={ABOUT}>
        Install FoamWin, OpenFOAM 14 for Windows.
      </Note>
      {release && !running && (
        <p className="muted mt-1 text-[11px]">
          {release.tag} · OF14-win.zip · {megabytes(release.size)} · from github.com/dhanninacs/foamWin
        </p>
      )}
      {status?.release_error && !running && (
        <p className="mt-1 text-[11px]" style={{ color: "var(--danger)" }}>
          {status.release_error.message}
        </p>
      )}
      {running && (
        <div className="mt-2" role="status" aria-live="polite">
          <div className="import-progress-track" style={{ height: 4, borderRadius: 2, overflow: "hidden" }}>
            <div
              className="import-progress-bar"
              data-indeterminate={fraction === null}
              style={fraction === null ? undefined : { width: `${(fraction * 100).toFixed(1)}%` }}
            />
          </div>
          <p className="muted mt-1 text-[11px]">{progressLine(progress)}</p>
        </div>
      )}
      {error !== "" && (
        <p className="mt-1 text-[11px]" style={{ color: "var(--danger)" }} role="alert">
          {error}
        </p>
      )}
      <div className="mt-2 flex gap-1.5">
        {!running ? (
          <Button
            className="ws-small"
            disabled={release === undefined}
            title={release === undefined ? "Waiting for the release information" : `Download ${release.tag}`}
            onClick={() => setConfirming(true)}
          >
            <Download size={12} /> Download and install FoamWin
            {release ? ` (${megabytes(release.size)})` : ""}
          </Button>
        ) : (
          <>
            <Button className="ws-small" disabled>
              <Loader2 size={12} className="spin" /> Installing…
            </Button>
            <Button
              variant="ghost"
              className="ws-small"
              disabled={progress?.stage === "install"}
              title={progress?.stage === "install" ? "The setup step runs to its end" : "Stop the download"}
              onClick={() => void execute("host.foamwin_cancel", {})}
            >
              <X size={12} /> Cancel
            </Button>
          </>
        )}
      </div>
      <Dialog
        open={confirming}
        title="Download FoamWin?"
        onClose={() => setConfirming(false)}
        actions={
          <>
            <Button variant="ghost" onClick={() => setConfirming(false)}>
              Cancel
            </Button>
            <Button onClick={start}>Download and install</Button>
          </>
        }
      >
        {release && (
          <div className="text-[12.5px]">
            <p>
              <strong>{release.name || `FoamWin ${release.tag}`}</strong>
            </p>
            <p className="muted mt-1 text-[12px]">
              OF14-win.zip, {megabytes(release.size)}, from {release.url}
            </p>
            <p className="muted mt-1 text-[12px]">Checked against the release&apos;s published SHA-256.</p>
            <p className="muted mt-1 text-[12px]">
              Installs to {status?.folder ?? "your user folder"}
            </p>
          </div>
        )}
      </Dialog>
    </div>
  );
}
