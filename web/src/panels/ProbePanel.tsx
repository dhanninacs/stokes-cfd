/**
 * The probe's readings: one value, or a profile along a line (results item 2).
 *
 * **A lightweight SVG, like `ResidualPlot`**, and for the same reason: no chart dependency, and the
 * geometry is a tested pure function (`probeChart`) rather than measured in the component.
 *
 * **The phases are shown as they are**, which is the whole contract: *"preparing"* is a wait with a
 * reason, *"outside the surface"* is a different fact from a failure, and a failure says what the
 * server said. A panel that showed all three as "error" would teach a user to distrust a value that
 * arrives a moment later.
 */
import { useStore } from "../state/store";
import { probeChart, probeCurve, probeRows } from "./probeModel";
import { Note } from "../components/Note";

const WIDTH = 264;
const HEIGHT = 96;

export default function ProbePanel() {
  const probe = useStore((s) => s.probe);
  const curve = probeCurve(probe.points, probe.values);
  const chart = probeChart(curve, WIDTH, HEIGHT);
  const hasValues = probe.phase === "ready" && probe.values.length > 0;
  return (
    <section className="mesh-section" aria-label="Probe" data-ws="results-probe">
      <div className="section-title">Probe</div>
      {probe.phase === "idle" && <Note className="mesh-hint" tip="Press Probe, then click the model to read the field there.">Press Probe, then click</Note>}
      {probe.phase === "preparing" && <p className="muted mesh-hint" data-ws="probe-preparing">Preparing the surface…</p>}
      {probe.phase === "outside" && <p className="muted mesh-hint" data-ws="probe-outside">{probe.message}</p>}
      {probe.phase === "failed" && <p className="muted mesh-hint" data-ws="probe-failed">{probe.message}</p>}
      {hasValues && (
        <>
          {probe.values.length > 1 && (
            <svg className="probe-chart" viewBox={`0 0 ${WIDTH} ${HEIGHT}`} role="img" aria-label={`${probe.field} along the line`} data-ws="probe-chart">
              <path d={chart.path} className="probe-line" fill="none" />
            </svg>
          )}
          <table className="probe-readings" aria-label="Probe readings">
            <tbody>
              {probeRows(probe.values).map((row) => (
                <tr key={row.index}>
                  <td className="muted">{curve[row.index]?.at !== undefined ? `${curve[row.index]!.at.toFixed(4)} m` : ""}</td>
                  <td className="mono" data-ws="probe-value">{row.value}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </section>
  );
}
