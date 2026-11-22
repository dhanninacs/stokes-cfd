/**
 * Streams: each fluid stream's mass flow, pressure drop and heat, its inlet and outlet temperature under
 * it, from the run's monitors (`results.streams`), and the heat balance between the streams. Shown under the results of a run with
 * inlets and outlets; nothing at all for a run without them.
 */
import { Fragment, useEffect, useState } from "react";
import { useStore } from "../state/store";
import { balanceLine, readStreams, streamRow, type StreamsAnswer } from "./streamsModel";

export default function StreamsSummary({ run }: { run: string | null }) {
  const execute = useStore((s) => s.execute);
  const [answer, setAnswer] = useState<StreamsAnswer | null>(null);

  useEffect(() => {
    setAnswer(null);
    if (run === null) return;
    let live = true;
    execute("results.streams", { run }, { silent: true })
      .then((raw) => live && setAnswer(readStreams(raw)))
      .catch(() => live && setAnswer(null));
    return () => {
      live = false;
    };
  }, [run, execute]);

  if (answer === null || answer.streams.length === 0) return null;
  const line = balanceLine(answer.balance);
  return (
    <div className="streams" data-ws="results-streams">
      <div className="streams-title">Streams</div>
      <table className="streams-table">
        <thead>
          <tr>
            <th scope="col">Stream</th>
            <th scope="col">Flow</th>
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
