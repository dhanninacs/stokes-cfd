/**
 * "Views ▾" on the Looks card (R4): the project's saved views, as rows. A press on a name applies the view (camera,
 * field, range, map, bands, looks), a × removes it, "Save view…" names what is on screen and keeps it, and "Export
 * all" makes one picture per view. The rules are `panels/viewsModel.ts` (pure, tested); the stores and the camera are
 * `panels/viewsControl.ts`; the four commands are `panels/viewsApi.ts`. This file draws the list and calls them.
 *
 * Text is a word or two on the control and the sentence in the tooltip. The name prompt is the app's `PromptDialog`
 * (never `window.prompt`, which the desktop host would draw as a native window). A save under a name that exists
 * replaces that view; the toast says which happened. A × removes at once, with an Undo in the toast; while Export all runs
 * the card is `inert` (a field chip pressed mid-export would change the picture being drawn) and the progress is read out
 * by a polite live region outside the card (an inert card is not in the accessibility tree).
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import "./viewsMenu.css";
import { X } from "lucide-react";
import { Button } from "../components/ui";
import { PromptDialog } from "../components/PromptDialog";
import { Popover } from "../components/Menu";
import { Tooltip } from "../components/Tooltip";
import { useStore } from "../state/store";
import { getView, listViews, removeView, undoRemoveView, type Execute, type ViewSummary } from "../panels/viewsApi";
import { applyRecord, exportAllViews, saveCurrentView, settleView } from "../panels/viewsControl";
import { cleanViewName, streamOfRecord, viewNameProblem, type StreamExtras } from "../panels/viewsModel";
import { exportAllSummary } from "../panels/viewsRun";

interface Props {
  /** The streamline seeds the card is set to, which no store holds; saved with a view that has lines. */
  stream: StreamExtras | null;
  /** The card remembers the seeds of an applied view. */
  onStream: (stream: StreamExtras) => void;
  /** The user chose something: a first picture still waiting for the scene must not overwrite it. */
  onTouch: () => void;
}

export default function ViewsMenu({ stream, onStream, onTouch }: Props) {
  const execute = useStore((s) => s.execute) as Execute;
  const pushToast = useStore((s) => s.pushToast);
  const log = useStore((s) => s.log);
  const projectKey = useStore((s) => s.project?.path ?? s.project?.name ?? "");
  const [open, setOpen] = useState(false);
  const [naming, setNaming] = useState(false);
  const [views, setViews] = useState<ViewSummary[]>([]);
  const [busy, setBusy] = useState<string>("");
  /** What the live region says while Export all runs. */
  const [status, setStatus] = useState<string>("");
  const anchor = useRef<HTMLButtonElement>(null);
  const exporting = busy !== "";

  const refresh = useCallback(async () => {
    try {
      setViews(await listViews(execute));
    } catch {
      setViews([]);
    }
  }, [execute]);

  // The list is the project's: read it when the menu opens and when another project comes up.
  useEffect(() => {
    if (open) void refresh();
  }, [open, refresh]);
  useEffect(() => {
    setViews([]);
  }, [projectKey]);

  // While Export all runs the card takes no input: a field chip pressed now would change the picture being drawn.
  useEffect(() => {
    const card = anchor.current?.closest<HTMLElement>('[data-ws="guided-card"]') ?? null;
    if (card === null || !exporting) return;
    card.inert = true;
    return () => {
      card.inert = false;
    };
  }, [exporting]);

  const apply = async (view: ViewSummary) => {
    setOpen(false);
    onTouch();
    try {
      const record = await getView(execute, view.id);
      const application = record === null ? null : applyRecord(record);
      if (application === null) {
        pushToast("error", `${view.name} could not be read`);
        return;
      }
      const seeds = streamOfRecord(record);
      if (seeds !== null) onStream(seeds);
      if (application.skipped.length > 0) pushToast("info", `${view.name}: ${application.skipped.join(", ")}`);
      await settleView(application);
    } catch {
      // The command layer has already toasted a refusal.
    }
  };

  /** Take the removal back, while it is still the newest step; otherwise say so. */
  const undoRemove = async (view: ViewSummary) => {
    try {
      if (await undoRemoveView(execute)) {
        await useStore.getState().refresh();
        await refresh();
        pushToast("ok", `Restored ${view.name}`);
      } else pushToast("info", `${view.name} was removed before other changes: use Undo to step back`);
    } catch {
      // The command layer has already toasted a refusal.
    }
  };

  const remove = async (view: ViewSummary) => {
    try {
      await removeView(execute, view.id);
    } catch {
      return;
    }
    pushToast("info", `Removed ${view.name}`, { label: "Undo", run: () => void undoRemove(view) });
    await refresh();
  };

  const save = async (value: string): Promise<string | null> => {
    const name = cleanViewName(value);
    try {
      const reply = await saveCurrentView(name, stream);
      pushToast("ok", `${reply.replaced ? "Replaced" : "Saved"} ${reply.name}`);
      setNaming(false);
      await refresh();
      return null;
    } catch (err) {
      return err instanceof Error ? err.message : "Not saved";
    }
  };

  const exportAll = async () => {
    setOpen(false);
    if (views.length === 0 || busy !== "") return;
    onTouch();
    setBusy("0");
    setStatus("Exporting views");
    try {
      const result = await exportAllViews(views, stream, (p) => {
        setBusy(`${p.done + 1}/${p.total}`);
        setStatus(`Exporting ${p.done + 1} of ${p.total}: ${p.name}`);
      });
      const summary = exportAllSummary(result, views.length);
      pushToast(summary.kind, summary.text);
      // The toast is short: every line goes to the console.
      for (const line of summary.details) log(summary.kind === "error" ? "error" : "info", `views: ${line}`);
      setStatus(summary.text);
    } finally {
      setBusy("");
    }
  };

  return (
    <>
      <Tooltip label="Save the view on screen, apply a saved one, or export a picture of each" side="top">
        <Button ref={anchor} aria-haspopup="dialog" aria-expanded={open} aria-disabled={busy !== "" || undefined} onClick={() => busy === "" && setOpen((o) => !o)} data-ws="guided-views">
          {busy === "" ? "Views ▾" : busy}
        </Button>
      </Tooltip>
      <Popover open={open} anchor={anchor} onClose={() => setOpen(false)} role="dialog" label="Views" className="menu views-menu" focusSelector=".views-save" placement="top-start">
        <Tooltip label="Name what is on screen and keep it; a name already used is replaced" side="right">
          <button
            type="button"
            className="menu-item no-drag views-save"
            onClick={() => {
              setOpen(false);
              setNaming(true);
            }}
            data-ws="guided-views-save"
          >
            <span className="menu-text">Save view…</span>
          </button>
        </Tooltip>
        <div className="menu-sep" role="separator" />
        {views.length === 0 ? (
          <div className="views-empty muted">No views</div>
        ) : (
          <div className="views-list" role="list" data-ws="guided-views-list">
            {views.map((view) => (
              <div key={view.id} className="views-row" role="listitem">
                <Tooltip label={`Apply ${view.name}: its camera, field, range, map and looks`} side="right" describe={false}>
                  <button type="button" className="menu-item no-drag views-name" onClick={() => void apply(view)} data-ws="guided-view-apply">
                    <span className="menu-text">{view.name}</span>
                  </button>
                </Tooltip>
                <Tooltip label={`Remove ${view.name}`} side="right" describe={false}>
                  <button type="button" className="views-remove no-drag" aria-label={`Remove ${view.name}`} onClick={() => void remove(view)} data-ws="guided-view-remove">
                    <X size={13} aria-hidden="true" />
                  </button>
                </Tooltip>
              </div>
            ))}
          </div>
        )}
        <div className="menu-sep" role="separator" />
        <Tooltip label="Apply each view in turn and download its picture, named after the view, with the Export choices you asked to Remember" side="right">
          <button type="button" className="menu-item no-drag views-export" aria-disabled={views.length === 0 || undefined} onClick={() => void exportAll()} data-ws="guided-views-export-all">
            <span className="menu-text">Export all</span>
          </button>
        </Tooltip>
      </Popover>
      {createPortal(
        <div className="views-live" role="status" aria-live="polite" data-ws="guided-views-status">
          {status}
        </div>,
        document.body,
      )}
      <PromptDialog open={naming} title="Save view" label="Name" placeholder="Lengthwise velocity" confirmLabel="Save" validate={viewNameProblem} onSubmit={save} onCancel={() => setNaming(false)} />
    </>
  );
}
