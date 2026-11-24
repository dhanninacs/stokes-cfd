/**
 * The openings' values at the time on screen (ADR-0022 addendum 2026-11-24): `run.monitors_at` for the run whose
 * fields are shown, at the time they were read at. A transient run's pressure drop and streams then follow the time
 * slider instead of saying the last time's numbers at every position; a steady run's are its last iteration's, as
 * before. The flows come back in each opening's own direction (into the domain at an inlet), the server's sign
 * convention, so the Monitors table, the legend and the Streams agree.
 *
 * One answer per (run, time) is kept for the page: a finished run's tables cannot change, and a playback steps back
 * over the same times. While an answer is on its way, or when the host cannot be asked, the record's own monitors
 * (the last time's, the solver's sign) are used and `at` is null, so nothing claims to follow a time it does not.
 */
import { useEffect, useMemo, useState } from "react";
import { useStore } from "../state/store";
import { monitorsOf, readMonitorsAt, type MonitorsAt } from "./monitorsModel";
import type { RunSummary } from "./runApi";

const answers = new Map<string, MonitorsAt>();
const ANSWERS_CAP = 400;

/** The time the shown fields were read at, for `run`; null when the results on screen are another run's. */
function useShownTime(run: string | null): { time: number | null; last: number | null } {
  const resultsRun = useStore((s) => s.results.data?.run ?? null);
  const time = useStore((s) => s.results.data?.time ?? null);
  const times = useStore((s) => s.results.data?.times);
  return useMemo(() => {
    if (run === null || resultsRun !== run || time === null) return { time: null, last: null };
    const finite = (times ?? []).filter((t) => Number.isFinite(t));
    return { time, last: finite.length > 0 ? Math.max(...finite) : time };
  }, [run, resultsRun, time, times]);
}

export interface ShownMonitors {
  monitors: readonly MonitorsAt["monitors"][number][];
  /** The time the numbers are at (the nearest written row), or null for the record's last values. */
  at: number | null;
  /** The time on screen is the run's last written one. */
  last: boolean;
  /** The flows are in each opening's own direction (true for an answer of `run.monitors_at`). */
  directed: boolean;
}

/** The openings' values for a run, at the time its fields are shown (see the module note). */
export function useMonitorsAt(run: Pick<RunSummary, "id" | "monitors" | "transient"> | null): ShownMonitors {
  const execute = useStore((s) => s.execute);
  const id = run?.id ?? null;
  const { time, last } = useShownTime(id);
  const key = id === null || time === null ? "" : `${id}@${time}`;
  const [, bump] = useState(0);
  useEffect(() => {
    if (key === "" || answers.has(key)) return;
    let live = true;
    execute("run.monitors_at", { run: id, time }, { silent: true })
      .then((raw) => {
        const read = readMonitorsAt(raw);
        if (read === null) return;
        if (answers.size >= ANSWERS_CAP) answers.delete(answers.keys().next().value!);
        answers.set(key, read);
        if (live) bump((n) => n + 1);
      })
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, [execute, key, id, time]);
  const found = key === "" ? undefined : answers.get(key);
  if (found !== undefined && found.monitors.length > 0) {
    return { monitors: found.monitors, at: found.time, last: time !== null && last !== null && Math.abs(time - last) <= 1e-9 * Math.max(1, Math.abs(last)), directed: true };
  }
  return { monitors: monitorsOf(run), at: null, last: true, directed: false };
}
