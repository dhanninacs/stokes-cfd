/**
 * The strip's model for a component: the stores' slices in, `guidedView` out.
 *
 * Every selector returns a slice that the stores replace only when it changes, and the view is memoised on
 * them, so the strip does not redraw on a camera frame.
 */
import { useMemo } from "react";
import { useUpload } from "../app/upload";
import { findMeshNodes, isStale, phaseNow, readResult } from "../panels/meshPanelModel";
import { NO_FIELD } from "../panels/fieldsModel";
import { latestProgress } from "../state/parts";
import { useStore } from "../state/store";
import { guidedView, type GuidedView } from "./guidedModel";

export function useGuidedView(): GuidedView {
  const project = useStore((s) => s.project);
  const tree = useStore((s) => s.tree);
  const setup = useStore((s) => s.setup);
  const live = useStore((s) => s.mesh);
  const runs = useStore((s) => s.run.runs);
  const loaded = useStore((s) => s.results.data !== null);
  const field = useStore((s) => s.results.view.field);
  const importProgress = useStore((s) => s.importProgress);
  const uploading = useUpload((u) => u.phase === "uploading");

  return useMemo(() => {
    const view = readResult(findMeshNodes(tree).result);
    return guidedView({
      tree,
      hasProject: project !== null,
      importing: uploading || latestProgress(importProgress) !== null,
      setup,
      meshPhase: phaseNow(view, live),
      meshStale: isStale(view),
      meshCells: view?.cells ?? 0,
      runs,
      resultsLoaded: loaded,
      fieldChosen: field !== NO_FIELD,
    });
  }, [project, tree, setup, live, runs, loaded, field, importProgress, uploading]);
}
