import { Switch } from "../components/ui";
import { useWorkspace } from "../workspace/store";

/** A display-quality profile; mesh geometry and CFD result values remain unchanged. */
export default function LODSection() {
  const lowDetail = useWorkspace((s) => s.document.viewport.lowDetail === true);
  const setValue = useWorkspace((s) => s.setValue);
  return (
    <section className="mesh-section" aria-label="Level of detail" data-ws="results-lod">
      <div className="section-title">Level of detail</div>
      <Switch
        checked={lowDetail}
        label="Reduced-detail display"
        description="Hides edge overlays, shadows and reflections to keep the view responsive. Geometry and result fields stay full resolution."
        onChange={(next) => void setValue(["viewport", "lowDetail"], next)}
      />
    </section>
  );
}
