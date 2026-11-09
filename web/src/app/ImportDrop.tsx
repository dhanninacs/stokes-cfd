/**
 * Getting a CAD file into the viewport from the browser (T-219 A): the drop target, the hint of an empty
 * project and the upload's status pill.
 *
 * * A drag that carries files outlines the viewport in the accent colour; the drop hands its one file to
 *   the same flow the project menu's picker uses (`importBridge.ts`), several files are refused with a toast.
 * * An open project with no part shows "Drop a CAD file here" with the way to the menu entry; the hint is
 *   gone the moment a part exists or an upload or import is under way.
 * * While a file goes up, a small card says "Uploading part.step 12 MB / 40 MB", draws the bar and offers Cancel.
 *
 * Everything that decides is `uploadModel.ts`; this file listens, draws and takes no pointer except on Cancel
 * and the hint's button. The listeners are the window's: a drag over a side panel is still a drag over the
 * window the file would otherwise navigate away from, so every file drop is taken or refused here.
 */
import { useEffect, useMemo, useState } from "react";
import { FileUp, X } from "lucide-react";
import { latestProgress } from "../state/parts";
import { useStore } from "../state/store";
import { Button } from "../components/ui";
import { runAction } from "./actions";
import { importFileObject } from "./importBridge";
import { useUpload } from "./upload";
import { chooseFile, dragHasFiles, progressFraction, progressText, showImportHint } from "./uploadModel";
import "./importDrop.css";

export default function ImportDrop() {
  const project = useStore((s) => s.project);
  const tree = useStore((s) => s.tree);
  const importProgress = useStore((s) => s.importProgress);
  const pushToast = useStore((s) => s.pushToast);
  const upload = useUpload();
  const [dragging, setDragging] = useState(false);

  useEffect(() => {
    const over = (e: DragEvent) => {
      if (!dragHasFiles(e.dataTransfer?.types)) return;
      e.preventDefault(); // without this the drop is not offered to the page
      if (e.dataTransfer) e.dataTransfer.dropEffect = "copy";
      setDragging(true);
    };
    // A leave with no `relatedTarget` is the pointer leaving the window; a leave into another element is not.
    const leave = (e: DragEvent) => {
      if (e.relatedTarget === null) setDragging(false);
    };
    const drop = (e: DragEvent) => {
      if (!dragHasFiles(e.dataTransfer?.types)) return;
      e.preventDefault();
      setDragging(false);
      const files = Array.from(e.dataTransfer?.files ?? []);
      if (files.length !== 1) {
        const refused = chooseFile(files, []);
        pushToast("error", refused.ok ? "No file was given." : refused.reason);
        return;
      }
      if (!importFileObject(files[0])) pushToast("error", "Import is not available: the project menu is hidden from the top bar.");
    };
    window.addEventListener("dragenter", over);
    window.addEventListener("dragover", over);
    window.addEventListener("dragleave", leave);
    window.addEventListener("drop", drop);
    return () => {
      window.removeEventListener("dragenter", over);
      window.removeEventListener("dragover", over);
      window.removeEventListener("dragleave", leave);
      window.removeEventListener("drop", drop);
    };
  }, [pushToast]);

  const importing = useMemo(() => latestProgress(importProgress) !== null, [importProgress]);
  const uploading = upload.phase === "uploading";
  const hint = showImportHint({ hasProject: project !== null, tree, uploading, importing });
  const fraction = progressFraction(upload.loaded, upload.total);

  return (
    <>
      {dragging && (
        <div className="import-drop-outline" data-ws="import-drop" role="presentation">
          <span className="import-drop-label glass">
            <FileUp size={16} aria-hidden="true" />
            Drop to import
          </span>
        </div>
      )}
      {hint && !dragging && (
        <div className="import-hint glass" data-ws="import-hint" role="note">
          <FileUp size={22} aria-hidden="true" />
          <div className="import-hint-title">Drop a CAD file here</div>
          <div className="muted import-hint-text" title="Or open the project menu and choose Import geometry. STEP, IGES, BREP, STL, OBJ and 3MF are read.">Reads STEP, IGES, BREP, STL, OBJ, 3MF.</div>
          <Button className="import-hint-button" onClick={() => runAction("project.importStep")} data-ws="import-hint-button">
            Import geometry…
          </Button>
        </div>
      )}
      {uploading && (
        <div className="upload-status glass" role="status" data-ws="upload-status">
          <div className="upload-status-row">
            <span className="upload-status-text" data-ws="upload-status-text">
              {progressText(upload.name, upload.loaded, upload.total)}
            </span>
            <button type="button" className="upload-status-cancel no-drag" aria-label="Cancel the upload" data-ws="upload-cancel" onClick={() => upload.cancel?.()}>
              <X size={13} aria-hidden="true" />
              Cancel
            </button>
          </div>
          <div
            className="upload-status-bar"
            role="progressbar"
            aria-label="Upload progress"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={fraction === null ? undefined : Math.round(fraction * 100)}
            data-indeterminate={fraction === null}
          >
            <span style={fraction === null ? undefined : { width: `${(fraction * 100).toFixed(1)}%` }} />
          </div>
        </div>
      )}
    </>
  );
}
