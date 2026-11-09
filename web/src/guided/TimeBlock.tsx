/**
 * Transient results on the Results card: a Time row with a slider over the times the run wrote, a step back and
 * on, and Play (Fluent's solution animation playback, Discovery's time slider). Shown only when the run wrote
 * fields at more than one time.
 *
 * A time is shown by `run.time`; the host reads that instant in the background and says so with `run.changed`
 * (or `run.failed_extract`), and the fields are then read again (`loadResults`), so every look on screen — the
 * colours, the slice, the legend — is redrawn at the new time. Play waits for each frame to arrive before it asks
 * for the next, so a slow host slows the playback down rather than piling requests up. The rules are
 * `timeModel.ts` (pure, tested); this file asks and draws.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { ChevronLeft, ChevronRight, Pause, Play } from "lucide-react";
import { Button } from "../components/ui";
import { Tooltip } from "../components/Tooltip";
import { loadResults } from "../panels/resultsControl";
import { useStore } from "../state/store";
import { Note } from "./controls";
import "./analyse.css";
import { looksSteady, nextFrame, stepTime, timeIndex, timeText, writtenTimes } from "./timeModel";

/** How long a frame stays on screen once it has arrived, before Play asks for the next. */
const FRAME_MS = 700;

export default function TimeBlock() {
  const client = useStore((s) => s.client);
  const run = useStore((s) => s.results.data?.run ?? null);
  const shown = useStore((s) => s.results.data?.time ?? 0);
  const rawTimes = useStore((s) => s.results.data?.times);
  const times = useMemo(() => writtenTimes(rawTimes), [rawTimes]);
  const steady = looksSteady(times);
  const [pending, setPending] = useState<number | null>(null);
  const [playing, setPlaying] = useState(false);
  const [loop, setLoop] = useState(false);
  const [error, setError] = useState("");
  const pendingRef = useRef<number | null>(null);
  pendingRef.current = pending;

  // The host's word that a time has been read: read the fields again, which redraws every look at that time.
  useEffect(() => {
    if (client === null || run === null) return;
    return client.onEvent((event, data) => {
      if (data.run_id !== run || pendingRef.current === null) return;
      if (event === "run.changed") void loadResults().finally(() => setPending(null));
      if (event === "run.failed_extract") {
        setPending(null);
        setPlaying(false);
        setError(typeof data.error === "string" && data.error !== "" ? data.error : "That time could not be read");
      }
    });
  }, [client, run]);

  const show = (time: number) => {
    if (client === null || run === null) return;
    setError("");
    setPending(time);
    client.call("run.time", { run, time }).catch((e: unknown) => {
      setPending(null);
      setPlaying(false);
      setError(e instanceof Error ? e.message : String(e));
    });
  };

  // Playback: once a frame has arrived (nothing pending), wait a moment, then ask for the next.
  useEffect(() => {
    if (!playing || pending !== null) return;
    const timer = window.setTimeout(() => {
      const next = nextFrame(times, shown, loop);
      if (next === null) setPlaying(false);
      else show(next);
    }, FRAME_MS);
    return () => window.clearTimeout(timer);
    // `show` closes over the client and run, which the effect's own deps already follow.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [playing, pending, shown, times, loop]);

  if (run === null || times.length < 2) return null;
  const at = Math.max(0, timeIndex(times, pending ?? shown));
  const back = stepTime(times, shown, -1);
  const on = stepTime(times, shown, 1);

  return (
    <div className="analyse-time" data-ws="guided-time">
      <Tooltip label={`The run wrote its fields at ${times.length} times; drag to show another`} side="top" describe={false}>
        <div className="looks-row">
          <span className="looks-label">Time</span>
          <input
            type="range"
            className="slider looks-slider"
            min={0}
            max={times.length - 1}
            step={1}
            value={at}
            aria-label="Time shown"
            aria-valuetext={timeText(times[at]!, steady)}
            onChange={(e) => {
              setPlaying(false);
              show(times[Number(e.target.value)]!);
            }}
            data-ws="guided-time-slider"
          />
          <span className="looks-value mono">{timeText(times[at]!, steady)}</span>
        </div>
      </Tooltip>
      <div className="guided-actions analyse-time-actions">
        <Tooltip label="The time before" side="top">
          <Button aria-label="Previous time" aria-disabled={back === null || playing || undefined} onClick={() => back !== null && !playing && show(back)} data-ws="guided-time-back">
            <ChevronLeft size={14} aria-hidden="true" />
          </Button>
        </Tooltip>
        <Tooltip label={playing ? "Stop the playback" : "Play the written times in order, each as soon as it has been read"} side="top">
          <Button active={playing} aria-pressed={playing} onClick={() => setPlaying(!playing)} data-ws="guided-time-play">
            {playing ? <Pause size={14} aria-hidden="true" /> : <Play size={14} aria-hidden="true" />}
            {playing ? "Stop" : "Play"}
          </Button>
        </Tooltip>
        <Tooltip label="The time after" side="top">
          <Button aria-label="Next time" aria-disabled={on === null || playing || undefined} onClick={() => on !== null && !playing && show(on)} data-ws="guided-time-on">
            <ChevronRight size={14} aria-hidden="true" />
          </Button>
        </Tooltip>
        <Tooltip label="Start again from the first time after the last" side="top">
          <Button active={loop} aria-pressed={loop} onClick={() => setLoop(!loop)} data-ws="guided-time-loop">
            Loop
          </Button>
        </Tooltip>
      </div>
      {pending !== null && <Note tip="The host is reading the fields at that time">{`Reading ${timeText(pending, steady)}…`}</Note>}
      {error !== "" && (
        <p role="alert" className="guided-error">
          {error}
        </p>
      )}
    </div>
  );
}
