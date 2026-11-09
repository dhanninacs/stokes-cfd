/**
 * Export the run's results as a self-contained HTML report (results item 5).
 *
 * **A link to the server's route, not a fetch and a blob**: `GET /api/results/report/{run}` answers with
 * `Content-Disposition: attachment`, so the browser saves the file with the name the server chose and no
 * page state is involved. A blob built in the client would need the same bytes twice in memory and would
 * have to invent the filename.
 *
 * **The token goes in the query here**, because a download is a navigation rather than a fetch and
 * cannot carry the `X-Stokes-Token` header - which is why the route accepts the token either way.
 */
import { Download } from "lucide-react";
import { Button } from "../components/ui";
import { useStore } from "../state/store";
import { Note } from "../components/Note";

export default function ReportSection() {
  const client = useStore((s) => s.client);
  const run = useStore((s) => s.results.data?.run ?? null);
  const hasFields = useStore((s) => (s.results.data?.fields?.length ?? 0) > 0);
  const ready = client !== null && run !== null && hasFields;
  const href =
    client === null || run === null
      ? null
      : `${client.apiBase.replace(/\/$/, "")}/api/results/report/${encodeURIComponent(run)}?token=${encodeURIComponent(client.token)}`;
  return (
    <section className="mesh-section" aria-label="Report" data-ws="results-report">
      <div className="section-title">Report</div>
      {ready && href !== null ? (
        <a
          className="report-download"
          href={href}
          // **`download` asks the browser to save rather than navigate**, and the server's
          // `Content-Disposition` names the file; both agree on "this is a file, not a page".
          download={`${run}-report.html`}
          data-ws="report-download"
        >
          <Download size={13} aria-hidden="true" /> Export report
        </a>
      ) : (
        <Button aria-label="Export report" disabled>
          <Download size={13} aria-hidden="true" /> Export report
        </Button>
      )}
      {!hasFields && (
        <Note className="mesh-hint" tip="Show the results first: the report carries the run's fields.">Show results first</Note>
      )}
    </section>
  );
}
