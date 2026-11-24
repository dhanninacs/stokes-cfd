/**
 * What an Auto colour range spans in a run with several written times (ADR-0022 addendum 2026-11-24): **All times**
 * (the default: one scale for every frame, so a playback's colours compare) or **This time** (the range of the time
 * on screen). Drawn by the Guided Results card and the Full Results panel alike, only for such a run and only while
 * the range is Auto; a fixed range spans what the user fixed. While the server works the all-times range out, the
 * shown time's range is used and the line says so.
 */
import { useStore } from "../state/store";
import { Tooltip } from "../components/Tooltip";
import { hasSeveralTimes, setTimeScope, setView, spansAllTimes } from "./fieldsModel";
import { rangeModeOf } from "./legendModel";

export default function TimeScopeChoice({ label = true }: { label?: boolean }) {
  const data = useStore((s) => s.results.data);
  const view = useStore((s) => s.results.view);
  if (!hasSeveralTimes(data) || rangeModeOf(view) !== "auto") return null;
  const scope = view.timeScope ?? "all";
  const ready = spansAllTimes({ timeScope: "all" }, data, view.field);
  const choose = (next: "all" | "time") =>
    useStore.setState((s) => {
      const nextView = setTimeScope(s.results.view, next);
      return nextView === s.results.view ? s : { results: setView(s.results, nextView) };
    });
  return (
    <div className="looks-row time-scope" data-ws="results-time-scope">
      {label && <span className="looks-label">Over</span>}
      <Tooltip label="All times keeps one colour scale for every written time, so frames compare; This time fits the time on screen" side="top" describe={false}>
        <div className="segmented" role="radiogroup" aria-label="Colour range over">
          {(["all", "time"] as const).map((s) => (
            <button key={s} type="button" role="radio" aria-checked={scope === s} onClick={() => choose(s)} data-ws={`results-time-scope-${s}`}>
              {s === "all" ? "All times" : "This time"}
            </button>
          ))}
        </div>
      </Tooltip>
      {scope === "all" && !ready && (
        <span className="muted time-scope-note" data-ws="results-time-scope-pending" title="The server is reading every written time once to find each field's range; the shown time's range is used until then">
          {data?.timeRanges === "computing" ? "working it out…" : "this time's for now"}
        </span>
      )}
    </div>
  );
}
