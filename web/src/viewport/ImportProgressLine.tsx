import { useEffect, useMemo, useState } from "react";
import { latestOperation, operationFraction, operationText } from "../state/operations";
import { latestProgress, progressFraction, progressLabel, progressText } from "../state/parts";
import { useStore } from "../state/store";

interface View {
  text: string;
  /** 0..1, or null while the length of the wait is not known (the file is being read, a run just started). */
  fraction: number | null;
  /** When it last spoke, in ms: the line shows the newest of the imports and the operations. */
  at: number;
}

/**
 * A slim line along the top edge of the viewport while something is running: "Reading part.step…",
 * then "Meshing 3 of 12 (coarse)…" with a bar that fills over the coarse and the fine pass, and
 * "Enclosure: cutting 1 body out of the enclosure 60 %" while a geometry operation runs (T-029).
 * It shows the newest of the imports and the operations under way, and is gone when the last one
 * has finished. It keeps its last text while it fades out, so the words do not change under the
 * eye. It never takes the pointer.
 */
export default function ImportProgressLine() {
  const progress = useStore((s) => s.importProgress);
  const operations = useStore((s) => s.operations);
  const partNodes = useStore((s) => s.partNodes);
  const latest = useMemo(() => latestProgress(progress), [progress]);
  const operation = useMemo(() => latestOperation(operations), [operations]);
  const label = useMemo(() => (latest ? progressLabel(latest, partNodes) : ""), [latest, partNodes]);

  const imported: View | null = latest ? { text: progressText(latest, label), fraction: progressFraction(latest), at: latest.at } : null;
  const running: View | null = operation ? { text: operationText(operation), fraction: operationFraction(operation), at: operation.at } : null;
  // Both at once is rare (an import while an operation runs); the one that spoke last is the news.
  const view = running !== null && (imported === null || running.at >= imported.at) ? running : imported;

  const [kept, setKept] = useState<{ text: string; fraction: number | null } | null>(null);
  const text = view?.text ?? null;
  const now = view?.fraction ?? null;
  useEffect(() => {
    if (text !== null) setKept({ text, fraction: now });
  }, [text, now]);
  const shown = view !== null ? { text: view.text, fraction: view.fraction } : kept;
  const fraction = shown?.fraction ?? null;

  return (
    <div
      className="import-progress"
      data-active={view !== null}
      role="progressbar"
      aria-label="Progress"
      aria-hidden={view === null}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={fraction === null ? undefined : Math.round(fraction * 100)}
      aria-valuetext={shown?.text}
    >
      <div className="import-progress-track">
        <div className="import-progress-bar" data-indeterminate={fraction === null} style={fraction === null ? undefined : { width: `${(fraction * 100).toFixed(1)}%` }} />
      </div>
      <div className="import-progress-label">{shown?.text}</div>
    </div>
  );
}
