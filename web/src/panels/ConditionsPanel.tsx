/**
 * The Conditions panel (ADR-0022 §9.1, T-048): the set-up stage's flat view beside the tree.
 *
 * §9.1's rows, in order:
 *
 * * **each region** — kind dot, name, material or "no material", body count, proxy swatch and eye —
 *   with its **boundaries beneath** it: swatch, name, kind label, primary value in display units,
 *   derived status dot;
 * * **Unassigned faces** ("12 faces of 3 bodies": hover highlights them, click picks them);
 * * **Findings** from `setup.check`: severity dot, message, and a Fix button where the fix names a
 *   command.
 *
 * Two rules from the ADR shape this file:
 *
 * * §9.2 — "**one tree stays**": this is a flat, set-up-stage view *beside* the tree, not a second
 *   tree. It renders no hierarchy of its own beyond region → its boundaries.
 * * D2.10 — the **status is derived, never written**: every dot comes from `setupRows.ts`, which is
 *   also what the tree's rows use, so the two views cannot disagree about a boundary's state.
 *
 * The data is the server's: `region.list` and `boundary.list` give the rows (with the derived status
 * the server computes for the CLI and MCP), and `setup.check` — already in the store from T-049 —
 * gives the unassigned-face count and the findings.
 */

import { useEffect, useMemo, useState } from "react";
import { ChevronsUpDown, ListChecks } from "lucide-react";
import { StokesApiError } from "../api/client";
import type { BoundaryEntry, RegionEntry, UncoveredAnswer } from "../api/types";
import { findNode, useStore } from "../state/store";
import { rangesToIds, uncoveredCount, uncoveredParts } from "./coverage";
import { useWorkspace } from "../workspace/store";
import { placeOf } from "../workspace/geometry";
import { Button } from "../components/ui";
import { boundaryRow, regionRow, type SetUpRow } from "./setupRows";
import { canCreate, defaultsParams, describeDefaults, findingFix, firstClause, flowOffer, type FlowTemplate } from "./setupFlowModel";
import { Note } from "../components/Note";

/** One row's dot colour, from the same map the drawer readout uses. */
function dotColor(status: SetUpRow["status"]): string {
  if (status === "ok") return "var(--success)";
  if (status === "warning") return "var(--warning)";
  return "var(--danger)"; // stale and failed both read as a problem to fix
}

/** Whether a row's tag can be toggled: only a row that actually proxies a tag. */
function tagRow(row: SetUpRow): boolean {
  return row.color !== null && row.visible !== null;
}

/**
 * The offer for a body that has no region yet (T-219 C): one press makes the fluid region and gives every face a
 * default boundary (`setup.defaults`), so what is left is picking the inlet and outlet faces and assigning them.
 */
function SetUpFlow() {
  const tree = useStore((s) => s.tree);
  const setup = useStore((s) => s.setup);
  const kinds = useStore((s) => s.conditionKinds);
  const offer = useMemo(() => flowOffer(tree, setup, kinds), [tree, setup, kinds]);
  const [part, setPart] = useState("");
  const [template, setTemplate] = useState<FlowTemplate | "">("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  if (offer === null) return null;
  const chosenPart = offer.parts.find((p) => p.id === part) ?? offer.parts[0];
  const chosenTemplate = template === "" ? offer.template : template;
  const allowed = canCreate(offer, chosenTemplate);

  const create = async () => {
    const state = useStore.getState();
    setBusy(true);
    setNote("");
    try {
      const answer = (await state.execute("setup.defaults", defaultsParams(chosenPart.id, chosenTemplate), { silent: true })) as Record<string, unknown>;
      state.pushToast("ok", describeDefaults(answer));
      // Select the new region so the tree and Properties show where it landed.
      const region = answer.region as { id?: string } | string | null | undefined;
      const regionId = typeof region === "string" ? region : region?.id;
      if (typeof regionId === "string") useStore.getState().select(regionId);
    } catch (err) {
      setNote(err instanceof StokesApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="conditions-flow" aria-label="Set up flow" data-ws="setup-flow">
      <div className="condition-family-title">Set up flow</div>
      <ol className="setup-flow-steps muted m-0 mb-2 text-[12px]" aria-label="Order of the set-up">
        <li data-done={offer.openings.inlet > 0} title="Pick the inlet face, then Assign > Velocity inlet." aria-description="Pick the inlet face, then Assign > Velocity inlet.">
          Inlet face
        </li>
        <li data-done={offer.openings.outlet > 0} title="Pick the outlet face, then Assign > Pressure outlet." aria-description="Pick the outlet face, then Assign > Pressure outlet.">
          Outlet face
        </li>
        <li
          title={`Make a fluid region of ${offer.parts.length === 1 ? chosenPart.name : "a part"} with a material; every other face becomes a wall.`}
          aria-description="Every other face becomes a wall."
        >
          Fluid region {offer.parts.length === 1 && <b>{chosenPart.name}</b>}
        </li>
      </ol>
      {offer.parts.length > 1 && (
        <div className="select-wrap mb-2">
          <select className="field" aria-label="Part to make the fluid" value={chosenPart.id} disabled={busy} onChange={(e) => setPart(e.target.value)} data-ws="setup-flow-part">
            {offer.parts.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
          <ChevronsUpDown size={13} className="select-chevron" aria-hidden="true" />
        </div>
      )}
      <div className="select-wrap mb-2">
        <select className="field" aria-label="Kind of flow" value={chosenTemplate} disabled={busy} onChange={(e) => setTemplate(e.target.value as FlowTemplate)} data-ws="setup-flow-template">
          <option value="internal">Internal flow: the body is the fluid</option>
          <option value="external">External flow: the body sits in an enclosure</option>
        </select>
        <ChevronsUpDown size={13} className="select-chevron" aria-hidden="true" />
      </div>
      <Button
        variant="primary"
        aria-disabled={!allowed.enabled || busy || undefined}
        title={allowed.enabled ? "Make the fluid region, give it a material and make every unassigned face a wall" : allowed.reason}
        onClick={() => allowed.enabled && !busy && void create()}
        data-ws="setup-flow-create"
      >
        {busy ? "Creating…" : "Create fluid region and default boundaries"}
      </Button>
      {!allowed.enabled && (
        <Note className="mt-2" tip={allowed.reason} data-ws="setup-flow-why">
          {firstClause(allowed.reason)}
        </Note>
      )}
      {note !== "" && (
        <p role="alert" className="m-0 mt-2 text-[12px]" style={{ color: "var(--danger)" }}>
          {note}
        </p>
      )}
    </section>
  );
}

export default function ConditionsPanel() {
  const setup = useStore((s) => s.setup);
  const tree = useStore((s) => s.tree);
  const stage = useStore((s) => s.stage);
  const [regions, setRegions] = useState<RegionEntry[]>([]);
  const [boundaries, setBoundaries] = useState<BoundaryEntry[]>([]);
  const [openRegions, setOpenRegions] = useState<Record<string, boolean>>({});
  const [error, setError] = useState("");

  // In the Physics stage this is the tab in front, wherever the workspace put it (the slot's other tabs stay).
  useEffect(() => {
    if (stage !== "Physics") return;
    const workspace = useWorkspace.getState();
    const place = placeOf(workspace.document.layout, "conditions");
    if (place === "left" || place === "right" || place === "bottom") workspace.setActiveTab(place, "conditions");
  }, [stage]);

  // The lists are the server's; refresh whenever the tree changes, which is when a boundary or a
  // region may have been added, renamed or removed.
  useEffect(() => {
    const state = useStore.getState();
    if (state.client === null || state.project === null) {
      setRegions([]);
      setBoundaries([]);
      return;
    }
    let cancelled = false;
    void Promise.all([
      state.execute("region.list", {}, { silent: true }),
      state.execute("boundary.list", {}, { silent: true }),
    ])
      .then(([regionList, boundaryList]) => {
        if (cancelled) return;
        setRegions(regionList as RegionEntry[]);
        setBoundaries(boundaryList as BoundaryEntry[]);
        setError("");
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(err instanceof StokesApiError ? err.message : String(err));
      });
    return () => {
      cancelled = true;
    };
  }, [tree, setup]);

  // Which faces no boundary holds (issue: no up-front indication): of the selected part when a part
  // is selected, else of every region's bodies. A query through `client.call`, so it neither logs
  // to the Console nor schedules another `setup.check` (this effect already follows `setup`).
  const selectedId = useStore((s) => s.selectedId);
  const selectedPart = useMemo(() => {
    const node = findNode(tree, selectedId);
    return node?.type === "part" ? node.id : null;
  }, [tree, selectedId]);
  const [coverage, setCoverage] = useState<UncoveredAnswer | null>(null);
  useEffect(() => {
    const { client, project } = useStore.getState();
    if (client === null || project === null) {
      setCoverage(null);
      return;
    }
    let live = true;
    client
      .call<UncoveredAnswer>("boundary.uncovered", selectedPart === null ? {} : { part: selectedPart })
      .then((answer) => {
        if (live) setCoverage(answer);
      })
      .catch(() => {
        if (live) setCoverage(null);
      });
    return () => {
      live = false;
    };
  }, [tree, setup, selectedPart]);

  // The boundaries under each region, in list order.
  const byRegion = useMemo(() => {
    const map = new Map<string, BoundaryEntry[]>();
    for (const entry of boundaries) {
      const list = map.get(entry.region.id);
      if (list === undefined) map.set(entry.region.id, [entry]);
      else list.push(entry);
    }
    return map;
  }, [boundaries]);

  /** Highlight the faces a row proxies, without changing the pick (§9.1's hover rule). */
  const highlight = (row: SetUpRow): void => {
    const state = useStore.getState();
    const node = findTagNode(state.tree, row.id);
    if (node === null) return;
    useStore.setState({ hoverPatch: node });
  };

  /** Pick the faces a row proxies (§9.1: "click picks them"). */
  const pick = (row: SetUpRow): void => {
    const state = useStore.getState();
    const node = findTagNode(state.tree, row.id);
    if (node === null || state.client === null) return;
    void state
      .execute("tag.members", { tag: node }, { silent: true })
      .then((answer) => {
        const members = (answer as { members?: { part: string; faces: number[] }[] }).members ?? [];
        if (members.length === 0) return;
        useStore.getState().pickMany(
          members.map((member) => ({ part: member.part, faces: member.faces })),
          "replace",
        );
      })
      .catch(() => undefined);
  };

  /** Show or hide the tag a row proxies, through the command that owns it. */
  const toggleVisible = (row: SetUpRow): void => {
    const state = useStore.getState();
    const node = findTagNode(state.tree, row.id);
    if (node === null) return;
    // `parameter.set` is the command that owns a node parameter; there is no `node.set`.
    void state
      .execute("parameter.set", { node, name: "visible", value: row.visible !== true }, { silent: true })
      .catch(() => undefined);
  };

  const uncovered = setup?.counts.uncovered_faces ?? 0;
  const findings = setup?.findings ?? [];
  /** Send the finding's own command; `execute` shows a refusal as a toast and the check reruns on the change. */
  const runFix = (finding: (typeof findings)[number]) => {
    const fix = findingFix(finding);
    if (fix === null) return;
    void useStore.getState().execute(fix.command, fix.params).catch(() => undefined);
  };
  const openParts = uncoveredParts(coverage);

  return (
    <div className="conditions-panel" data-ws="conditions-panel">
      <SetUpFlow />
      {error !== "" && (
        <p role="alert" className="m-0 mb-2 px-2 text-[12px]" style={{ color: "var(--danger)" }}>
          {error}
        </p>
      )}
      {regions.length === 0 && boundaries.length === 0 && error === "" && (
        <Note className="px-2 py-1" tip="Make a fluid region above, or give bodies a material.">
          No regions yet
        </Note>
      )}

      {regions.map((region) => {
        const row = regionRow(region);
        const open = openRegions[region.id] ?? true;
        const under = byRegion.get(region.id) ?? [];
        return (
          <div key={region.id} className="conditions-group">
            <div className="conditions-row" role="treeitem" aria-expanded={under.length > 0 ? open : undefined}>
              <button
                type="button"
                className="conditions-twisty"
                aria-label={open ? "Collapse" : "Expand"}
                onClick={() => setOpenRegions((current) => ({ ...current, [region.id]: !open }))}
                disabled={under.length === 0}
              >
                {under.length > 0 ? (open ? "▾" : "▸") : ""}
              </button>
              <span className="setup-dot" style={{ background: dotColor(row.status) }} aria-hidden="true" />
              {tagRow(row) && <span className="conditions-swatch" style={{ background: row.color ?? undefined }} aria-hidden="true" />}
              <button type="button" className="conditions-name" onClick={() => useStore.getState().select(region.id)}>
                {row.name}
              </button>
              <span className="muted">{row.value === "" ? "no material" : row.value}</span>
              <span className="muted">{row.detail.match(/\d+ bod/)?.[0] ?? ""}</span>
              {tagRow(row) && (
                <button
                  type="button"
                  className="conditions-eye"
                  aria-label={row.visible ? "Hide" : "Show"}
                  aria-pressed={row.visible === true}
                  onClick={() => toggleVisible(row)}
                >
                  {row.visible ? "◉" : "○"}
                </button>
              )}
            </div>
            {open &&
              under.map((entry) => {
                const boundary = boundaryRow(entry);
                return (
                  <div
                    key={entry.id}
                    className="conditions-row conditions-boundary"
                    role="treeitem"
                    onMouseEnter={() => highlight(boundary)}
                    onMouseLeave={() => useStore.setState({ hoverPatch: null })}
                  >
                    <span className="conditions-twisty" aria-hidden="true" />
                    <span className="setup-dot" style={{ background: dotColor(boundary.status) }} aria-hidden="true" />
                    {tagRow(boundary) && (
                      <span className="conditions-swatch" style={{ background: boundary.color ?? undefined }} aria-hidden="true" />
                    )}
                    <button
                      type="button"
                      className="conditions-name"
                      title="Pick this boundary's faces"
                      onClick={() => {
                        useStore.getState().select(entry.id);
                        pick(boundary);
                      }}
                    >
                      {boundary.name}
                    </button>
                    <span className="muted">{boundary.legacy ? "" : boundary.label}</span>
                    <span className="muted">{boundary.value}</span>
                    {tagRow(boundary) && (
                      <button
                        type="button"
                        className="conditions-eye"
                        aria-label={boundary.visible ? "Hide" : "Show"}
                        aria-pressed={boundary.visible === true}
                        onClick={() => toggleVisible(boundary)}
                      >
                        {boundary.visible ? "◉" : "○"}
                      </button>
                    )}
                  </div>
                );
              })}
          </div>
        );
      })}

      {(openParts.length > 0 || (coverage === null && uncovered > 0)) && (
        <div className="conditions-group" aria-label="Unassigned faces" data-ws="unassigned-faces">
          {/*
            The parts and faces come from `boundary.uncovered` (the server's covered-set arithmetic): the
            selected part when one is selected, else every region's bodies. Clicking a part picks exactly
            its unassigned faces, ready for Assign; they become the `unassigned` wall if left.
          */}
          <div className="conditions-row">
            <span className="setup-dot" style={{ background: "var(--warning)" }} aria-hidden="true" />
            <span className="conditions-name">Unassigned faces</span>
            <span className="muted">{(coverage?.faces ?? uncovered).toLocaleString()}</span>
          </div>
          {openParts.map((entry) => (
            <button
              key={entry.part}
              type="button"
              className="conditions-row conditions-boundary conditions-unassigned"
              title={`Pick the faces of ${entry.name} no boundary holds, then Assign`}
              aria-current={entry.part === selectedPart || undefined}
              data-ws="unassigned-part"
              onClick={() => {
                useStore.getState().setPickMode("face");
                useStore.getState().pickMany([{ part: entry.part, faces: rangesToIds(entry.ranges) }], "replace");
              }}
            >
              <span className="conditions-twisty" aria-hidden="true" />
              <span className="conditions-name">{entry.name}</span>
              <span className="muted">{uncoveredCount(entry)}</span>
            </button>
          ))}
        </div>
      )}

      {findings.length > 0 && (
        <div className="conditions-findings">
          <div className="condition-family-title">Findings</div>
          {findings.map((finding, index) => (
            /*
              **The key is the INDEX and the code together** (claude-win2's 0530 item 5). Two findings
              legitimately share a code - both of these are `setup.needs_physics`, one per boundary -
              so `key={finding.code}` gave React duplicate keys, and the rows were indistinguishable
              because the SUGGESTION is shared too ("set the physics models in …"). Measured: two
              identical rows, both truncated at the same place.
            */
            <div key={`${finding.code}:${index}`} className="conditions-row" data-ws="finding">
              <span
                className="setup-dot"
                style={{ background: finding.severity === "error" ? "var(--danger)" : "var(--warning)" }}
                aria-hidden="true"
              />
              {/*
                **The MESSAGE, not the suggestion.** The message names what is wrong (which boundary),
                and the suggestion is the same sentence for every finding of a code - so showing the
                suggestion made two different problems look like one repeated. The suggestion is the
                tooltip, which is where a shared explanation belongs.
              */}
              <button
                type="button"
                className="conditions-name min-w-0 text-left"
                // **Clicking selects the node the finding is about**, which is what makes a finding
                // actionable rather than a note: the Details pane then shows that node's own page.
                onClick={() => {
                  const first = finding.nodes?.[0];
                  if (first !== undefined) useStore.getState().select(first);
                }}
                title={finding.suggestion ?? finding.message}
                style={{ whiteSpace: "normal", overflowWrap: "anywhere" }}
                disabled={finding.nodes === undefined || finding.nodes.length === 0}
              >
                {finding.message}
              </button>
              {/* **Fix runs the finding's own command** (`region.set`, `tag.rematch`); a finding whose fix is a
                  step on a page has no command, so it has no button (it used to say "not wired yet"). */}
              {findingFix(finding) !== null && (
                <Button
                  className="!h-6 !px-2 text-[11.5px]"
                  title={finding.suggestion ?? "Apply the fix"}
                  onClick={() => runFix(finding)}
                >
                  Fix
                </Button>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/** The tag node a boundary or region references, by id, in the live tree. */
function findTagNode(root: unknown, id: string): string | null {
  // The row's id is the boundary/region node's; its `tag` parameter names the tag node.
  const node = findById(root as Parameters<typeof findById>[0], id);
  const tag = node?.params.tag;
  return typeof tag === "string" ? tag : null;
}

/** A minimal id lookup over the node summary tree, local so this panel needs no store export. */
function findById(
  root: { id: string; params: Record<string, unknown>; children?: readonly unknown[] } | null,
  id: string,
): { id: string; params: Record<string, unknown> } | null {
  if (root === null) return null;
  if (root.id === id) return root;
  for (const child of root.children ?? []) {
    const found = findById(child as Parameters<typeof findById>[0], id);
    if (found !== null) return found;
  }
  return null;
}

/** The panel's icon, kept beside it so the registration and the panel cannot drift apart. */
export const CONDITIONS_ICON = ListChecks;
