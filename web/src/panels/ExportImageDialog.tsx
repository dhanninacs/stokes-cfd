/**
 * The "Export image" dialog (R3 of the results overhaul): the choices of a figure - size, background, legend,
 * scale bar, title - and the button that makes it. The rules are `exportModel.ts` (tested); the drawing is
 * `viewport/exportImage.ts`; this file only edits the settings and runs the export.
 *
 * **Mounted once, at the window's root, and opened from anywhere**: `openExportDialog()` flips a one-field
 * store, so the Full-mode Results panel and the guided Results card call the same function and neither
 * has to mount a dialog of its own (a guided card is not on screen in Full mode, and the other way round).
 *
 * Text is a few words a control, the sentence is the tooltip. The settings are remembered in `localStorage`
 * only when "Remember" is on (`exportModel.readStoredExport`), and never the title.
 */
import { useState } from "react";
import "./ExportImageDialog.css";
import { create } from "zustand";
import { Button, Segmented, Switch } from "../components/ui";
import { Dialog } from "../components/Dialog";
import { Tooltip } from "../components/Tooltip";
import { useStore } from "../state/store";
import { currentScene } from "../viewport/scene";
import { ExportMemoryError, download, downloadSidecar, exportImage } from "../viewport/exportImage";
import {
  BACKGROUND_WORDS,
  EXPORT_BACKGROUNDS,
  MAX_SIDE,
  MIN_SIDE,
  exportPixels,
  readStoredExport,
  writeStoredExport,
  type ExportBackground,
  type ExportSettings,
} from "./exportModel";

const useExportDialog = create<{ open: boolean }>(() => ({ open: false }));

/** Open the dialog. The one door other code uses (the Results panel and the guided card). */
export function openExportDialog(): void {
  useExportDialog.setState({ open: true });
}

export function closeExportDialog(): void {
  useExportDialog.setState({ open: false });
}

function storage(): Storage | null {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

/** `1x 2x 4x` and the exact size: the four choices the plan names. */
type SizeChoice = "1" | "2" | "4" | "size";

/** The window's size when no scene is up (the dialog still opens; the export then says why it cannot run). */
const FALLBACK_LIVE = { width: 1500, height: 900 };

function Body() {
  const stored = useState(() => readStoredExport(storage()))[0];
  const [settings, setSettings] = useState<ExportSettings>(stored.settings);
  const [remember, setRemember] = useState(stored.remember);
  const [sidecar, setSidecar] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const pushToast = useStore((s) => s.pushToast);
  const scene = currentScene();
  const live = scene?.liveSize ?? FALLBACK_LIVE;
  const pixels = exportPixels(settings, live, scene?.maxExportSide);
  const choice: SizeChoice = settings.size ? "size" : (String(settings.scale) as SizeChoice);
  const set = (change: Partial<ExportSettings>) => setSettings((s) => ({ ...s, ...change }));

  const chooseSize = (next: SizeChoice) => {
    if (next === "size") set({ size: settings.size ?? { w: pixels.w, h: pixels.h } });
    else setSettings(({ size: _dropped, ...rest }) => ({ ...rest, scale: Number(next) as ExportSettings["scale"] }));
  };

  const run = async () => {
    const live = currentScene();
    if (live === null) {
      setError("Open a model in the viewport first.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      writeStoredExport(storage(), { remember, settings });
      const result = await exportImage(live, settings);
      download(result.blob, result.name);
      if (sidecar) await downloadSidecar(result.sidecar, result.name);
      pushToast("ok", `Exported ${result.name} (${result.pixels.w} x ${result.pixels.h})`);
      closeExportDialog();
    } catch (e) {
      // Out of graphics memory is a toast (the dialog stays open on its size choice); the rest are a line here.
      if (e instanceof ExportMemoryError) pushToast("error", e.message);
      else setError(e instanceof Error ? e.message : "The picture could not be made.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog
      open
      title="Export image"
      onClose={closeExportDialog}
      actions={
        <>
          <Button onClick={closeExportDialog}>Cancel</Button>
          <Button variant="primary" onClick={() => void run()} disabled={busy} data-ws="export-image-go">
            {busy ? "Exporting…" : "Export"}
          </Button>
        </>
      }
    >
      <div className="export-dialog" data-ws="export-image-dialog">
        <div className="export-row">
          <span className="muted">Size</span>
          <Tooltip label="The picture is the window's size times this, or an exact size in pixels" side="top">
            <div>
              <Segmented<SizeChoice>
                label="Size"
                value={choice}
                options={[
                  { value: "1", label: "1x" },
                  { value: "2", label: "2x" },
                  { value: "4", label: "4x" },
                  { value: "size", label: "W × H" },
                ]}
                onChange={chooseSize}
              />
            </div>
          </Tooltip>
        </div>
        {settings.size && (
          <div className="export-row">
            <span />
            <div className="export-size">
              <SideInput label="Width" value={settings.size.w} onCommit={(w) => set({ size: { w, h: settings.size?.h ?? pixels.h } })} />
              <span className="muted">×</span>
              <SideInput label="Height" value={settings.size.h} onCommit={(h) => set({ size: { w: settings.size?.w ?? pixels.w, h } })} />
            </div>
          </div>
        )}
        <div className="export-row">
          <span />
          <Tooltip label={pixels.clamped ? "The graphics driver cannot draw the size asked for; the picture is made at this size" : "The size of the picture in pixels"} side="top">
            <span className={pixels.clamped ? "export-size-note warn" : "export-size-note muted"} data-ws="export-image-size">
              {`${pixels.w} × ${pixels.h} px${pixels.clamped ? " (reduced)" : ""}`}
            </span>
          </Tooltip>
        </div>
        <div className="export-row">
          <span className="muted">Background</span>
          <Tooltip label="Viewport keeps the window's colours; White is for a page; Clear leaves the ground transparent" side="top">
            <div>
              <Segmented<ExportBackground>
                label="Background"
                value={settings.background}
                options={EXPORT_BACKGROUNDS.map((value) => ({ value, label: BACKGROUND_WORDS[value] }))}
                onChange={(background) => set({ background })}
              />
            </div>
          </Tooltip>
        </div>
        <Tooltip label="Put the colour legend beside the picture, where the Legend setting places it" side="top">
          <div>
            <Switch checked={settings.legend} label="Legend" onChange={(legend) => set({ legend })} />
          </div>
        </Tooltip>
        <Tooltip label="Draw the length of the scale at the bottom left (in a perspective view it is measured at the centre)" side="top">
          <div>
            <Switch checked={settings.scaleBar} label="Scale bar" onChange={(scaleBar) => set({ scaleBar })} />
          </div>
        </Tooltip>
        <label className="export-row">
          <span className="muted">Title</span>
          <input
            className="field"
            aria-label="Title"
            placeholder="None"
            maxLength={200}
            value={settings.title}
            onChange={(e) => set({ title: e.target.value })}
            data-ws="export-image-title"
          />
        </label>
        <Tooltip label="Also save the figure's settings as a .json file; the PNG carries them already, in its metadata" side="top">
          <div>
            <Switch checked={sidecar} label="Settings file" onChange={setSidecar} />
          </div>
        </Tooltip>
        <Tooltip label="Keep these choices for the next export (the title is not kept)" side="top">
          <div>
            <Switch checked={remember} label="Remember" onChange={setRemember} />
          </div>
        </Tooltip>
        {error !== "" && (
          <p className="export-error" role="alert" data-ws="export-image-error">
            {error}
          </p>
        )}
      </div>
    </Dialog>
  );
}

/** A pixel count that commits on Enter or when it loses focus, and puts back what it had on text that is not a number. */
function SideInput({ label, value, onCommit }: { label: string; value: number; onCommit: (value: number) => void }) {
  const [draft, setDraft] = useState<string | null>(null);
  const commit = () => {
    const parsed = draft === null ? NaN : Math.round(Number(draft.trim()));
    setDraft(null);
    if (Number.isFinite(parsed)) onCommit(Math.min(MAX_SIDE, Math.max(MIN_SIDE, parsed)));
  };
  return (
    <input
      className="field mono export-side"
      inputMode="numeric"
      spellCheck={false}
      aria-label={`${label} in pixels`}
      value={draft ?? String(value)}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => {
        if (e.key === "Enter") commit();
        else if (e.key === "Escape") setDraft(null);
      }}
    />
  );
}

/** Mount this once at the window's root (`App.tsx`). It draws nothing until `openExportDialog()` is called. */
export default function ExportImageDialog() {
  const open = useExportDialog((s) => s.open);
  return open ? <Body /> : null;
}
