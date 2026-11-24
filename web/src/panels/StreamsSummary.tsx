/**
 * Streams: each fluid stream's mass flow, pressure drop and heat, its inlet and outlet temperature under
 * it, from the run's monitors (`results.streams`), and the heat balance between the streams. Shown under the results of a run with
 * inlets and outlets; nothing at all for a run without them.
 *
 * **A transient run's streams follow the time on screen** (ADR-0022 addendum 2026-11-24): the command is asked at
 * the time the fields were read at, and the title says which ("at t = 0.25 s"). The flow is what comes in through
 * the inlets, positive (the server's sign convention).
 */
import { Fragment, useEffect, useState } from "react";
import { useStore } from "../state/store";
import { atTimeText } from "./monitorsModel";
import { balanceLine, readStreams, streamRow, type StreamsAnswer } from "./streamsModel";

export default function StreamsSummary({ run }: { run: string | null }) {
  const execute = useStore((s) => s.execute);
  const record = useStore((s) => (run === null ? undefined : s.run.runs.find((r) => r.id === run)));
  const transient = record?.transient === true;
  const shownTime = useStore((s) => (s.results.data !== null && s.results.data.run === run ? s.results.data.time : null));
  const lastTime = useStore((s) => {
    const times = s.results.data !== null && s.results.data.run === run ? (s.results.data.times ?? []) : [];
    return times.length > 0 ? Math.max(...times) : null;
  });
  const time = transient ? shownTime : null;
  const [answer, setAnswer] = useState<StreamsAnswer | null>(null);

  useEffect(() => {
    if (run === null) {
      setAnswer(null);
      return;
    }
    let live = true;
    execute("results.streams", time === null ? { run } : { run, time }, { silent: true })
      .then((raw) => live && setAnswer(readStreams(raw)))
      .catch(() => live && setAnswer(null));
    return () => {
      live = false;
    };
  }, [run, time, execute]);

  if (answer === null || answer.streams.length === 0) return null;
  const line = balanceLine(answer.balance);
  const last = time === null || (lastTime !== null && Math.abs(time - lastTime) <= 1e-9 * Math.max(1, Math.abs(lastTime)));
  const when = atTimeText(transient, answer.time, last, record?.endTime ?? null);
  return (
    <div className="streams" data-ws="results-streams">
      <div className="streams-title">
        Streams
        {when !== "" && (
          <span className="muted streams-when" data-ws="results-streams-when">
            {` ${when}`}
          </span>
        )}
      </div>
      <table className="streams-table">
        <thead>
          <tr>
            <th scope="col">Stream</th>
            <th scope="col" title="What comes in through the stream's inlets: positive into the model">
              Flow in
            </th>
            <th scope="col">Δp</th>
            <th scope="col">Q</th>
          </tr>
        </thead>
        <tbody>
          {answer.streams.map((s) => {
            const row = streamRow(s, answer.units);
            return (
              <Fragment key={s.region}>
                <tr data-stream={s.region}>
                  <th scope="row" title={`in: ${s.inlets.join(", ")} · out: ${s.outlets.join(", ")}`}>
                    {row.name}
                  </th>
                  <td>{row.flow}</td>
                  <td>{row.dp}</td>
                  <td>{row.heat}</td>
                </tr>
                {row.temps !== "-" && (
                  <tr className="streams-temps">
                    <td colSpan={4}>{row.temps}</td>
                  </tr>
                )}
              </Fragment>
            );
          })}
        </tbody>
      </table>
      {line !== null && <p className="muted streams-balance">{line}</p>}
    </div>
  );
}
