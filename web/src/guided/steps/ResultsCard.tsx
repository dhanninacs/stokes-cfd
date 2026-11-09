/**
 * Results, as LOOKS: pick what colours the model, then how it is shown - Surface, Slice, Iso, Stream, Vectors or
 * Clip - with the two or three controls that look needs, the legend's range, map and bands, and the probe, the
 * rest of the Results panel ("More…") and the report.
 *
 * Velocity and Pressure are the run's `U_mag` and `p` fields (a "more" menu lists the others by name); a press
 * colours the surfaces through the results store, exactly as the Results panel's field list does. The legend,
 * with the pressure drop under it when the run has an inlet and an outlet, is drawn by the layer directly under
 * this card. Opening the card reads the newest finished run's results when they are not read yet and, with none
 * chosen, shows the speed. A press of Velocity or Pressure before the results are read reads them
 * (`showResults`) and shows that field as soon as they arrive. Report is off, saying "Finish a run first", until
 * a run is done.
 *
 * **The card decides nothing about a look.** Which tile is lit, what its controls are and what a press does are
 * `panels/lookModel.ts` (pure, tested) reading and writing the stores Full mode's sections already use, through
 * `looksControl.ts`; this file draws the rows it hands back. The range, map and bands write `results.view` with
 * `legendModel`'s reducers, as the Results panel's menus do.
 *
 * **The first picture is made once per project** (plan §3.7): when the results first load, `firstShowDefaults`
 * shows a slice through the flow for a 3-D mesh (the surfaces and an orthographic camera for a 2-D one: the
 * server's `two_d`, else a box under 5 % thin), and fits the view once the scene has settled. It waits for the scene to know the model's box, and is remembered by project, as the
 * Geometry card remembers its guesses, so a user's own choice is never overruled by a later run.
 */
import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { Box, ChevronsUpDown, Layers, Mountain, MoveUpRight, Scissors, Wind, type LucideIcon } from "lucide-react";
import { Button } from "../../components/ui";
import { Tooltip } from "../../components/Tooltip";
import { NO_FIELD, SMOOTH, chooseField, effectiveRange, parseRangeInput, setBandCount, setView } from "../../panels/fieldsModel";
import { fieldLabel, fieldUnits } from "../../panels/fieldsModel";
import { RANGE_MODE_WORDS, legendOf, rangeModeOf, setManualEnd, setMap, setRangeMode, type RangeMode } from "../../panels/legendModel";
import { LOOKS, DEFAULT_PREFS, activationActions, activeLook, controlActions, controlsOf, firstShowDefaults, lookAvailability, type LookControl, type LookId, type LookPrefs } from "../../panels/lookModel";
import { createDebounce } from "../../panels/sliceModel";
import { openExportDialog } from "../../panels/ExportImageDialog";
import { openPanel } from "../../panels/panelOpen";
import { probeRows } from "../../panels/probeModel";
import { showResults, useFieldOptions, useHasFinishedRun } from "../../panels/resultsControl";
import { COLORMAPS, COLORMAP_IDS, type ColormapId } from "../../state/colormap";
import { runAction } from "../../app/actions";
import { useStore } from "../../state/store";
import GuidedCard from "../GuidedCard";
import ViewsMenu from "../ViewsMenu";
import { LineButton, LinePlot } from "../LineBlock";
import AnalyseBlock from "../AnalyseBlock";
import TimeBlock from "../TimeBlock";
import { fitRange } from "../fitRange";
import { contourLinesOn, linesFor, withContourLines } from "../analyseModel";
import { Chips, Note, type Choice } from "../controls";
import { reportBlocker, resultChoices, RESULT_FIELD_WORDS, type GuidedStep } from "../guidedModel";
import { lookContext, lookStateOf, runLooks, type Report } from "../looksControl";
import { chooseMode } from "../modeStore";
import { arrangeFor } from "../treeToggle";

function update(change: (live: ReturnType<typeof useStore.getState>["results"]) => ReturnType<typeof useStore.getState>["results"]): void {
  useStore.setState((s) => {
    const next = change(s.results);
    return next === s.results ? s : { results: next };
  });
}

/** Projects whose first picture has been made, so a later run or a later visit never redoes it over the user's choices. */
const defaulted = new Set<string>();

/** How often the first picture waits for the scene to know the model's box, and for how long: then it goes ahead without. */
const BOX_RETRY_MS = 400;
const BOX_RETRIES = 25;

const ICONS: Record<LookId, LucideIcon> = { surface: Box, slice: Layers, iso: Mountain, stream: Wind, vectors: MoveUpRight, clip: Scissors };

const BAND_CHOICES: readonly Choice<string>[] = [
  { value: String(SMOOTH), label: "Smooth" },
  { value: "8", label: "8" },
  { value: "16", label: "16" },
];

/** A range slider that acts when it rests (`settleMs`), showing the value being dragged meanwhile. */
function LookSlider({ control, onCommit }: { control: Extract<LookControl, { kind: "slider" }>; onCommit: (value: number) => void }) {
  const [draft, setDraft] = useState<number | null>(null);
  const commit = useRef(onCommit);
  commit.current = onCommit;
  const settle = control.settleMs;
  const debounce = useRef(createDebounce<number>(() => undefined, settle));
  useEffect(() => {
    const d = createDebounce<number>((value) => {
      commit.current(value);
      setDraft(null);
    }, settle);
    debounce.current = d;
    // A press that leaves the card or the look is dropped, not flushed: it would write into the look that replaced this one.
    return () => d.cancel();
  }, [settle]);
  const shown = draft ?? control.value;
  return (
    <>
      <input
        type="range"
        className="slider looks-slider"
        min={control.min}
        max={control.max}
        step={control.step}
        value={shown}
        disabled={control.disabled !== undefined}
        aria-label={control.label}
        aria-valuetext={control.text !== "" ? control.text : undefined}
        onChange={(e) => {
          const value = Number(e.target.value);
          if (settle === 0) {
            commit.current(value);
            return;
          }
          setDraft(value);
          debounce.current.call(value);
        }}
        data-ws={`guided-look-${control.id}`}
      />
      <span className="looks-value mono">{draft === null ? control.text : ""}</span>
    </>
  );
}

function LookControls({ shown, controls, onChoose, onSlide, onPress }: { shown: LookId; controls: readonly LookControl[]; onChoose: (id: string, value: string) => void; onSlide: (id: string, value: number) => void; onPress: (id: string) => void }) {
  const buttons = controls.filter((c) => c.kind === "button");
  return (
    <div className="looks-controls" data-ws="guided-look-controls">
      {controls.map((c) =>
        c.kind === "button" ? null : (
          <Tooltip key={`${shown}:${c.id}`} label={c.kind === "slider" ? (c.disabled ?? c.tip) : c.tip} side="top" describe={false}>
            <div className="looks-row">
              <span className="looks-label">{c.label}</span>
              {c.kind === "choice" ? (
                <Chips<string> label={c.label} value={c.value} choices={c.options.map((o) => ({ value: o.value, label: o.label, disabled: o.disabled }))} onChange={(v) => onChoose(c.id, v)} wide />
              ) : (
                <LookSlider control={c} onCommit={(v) => onSlide(c.id, v)} />
              )}
            </div>
          </Tooltip>
        ),
      )}
      {buttons.length > 0 && (
        <div className="guided-actions">
          {buttons.map((b) => (
            <Tooltip key={b.id} label={b.disabled ?? b.tip} side="top">
              <Button aria-disabled={b.disabled !== undefined || undefined} onClick={() => b.disabled === undefined && onPress(b.id)} data-ws={`guided-look-${b.id}`}>
                {b.label}
              </Button>
            </Tooltip>
          ))}
        </div>
      )}
    </div>
  );
}

/** One end of a typed range: commits on Enter or blur, puts back what it had on Escape or on text that is not a number. */
function EndInput({ label, value, onCommit }: { label: string; value: number | null; onCommit: (value: number) => void }) {
  const [draft, setDraft] = useState<string | null>(null);
  const shown = draft ?? (value === null ? "" : String(Number(value.toPrecision(4))));
  const commit = () => {
    const parsed = draft === null ? null : parseRangeInput(draft);
    setDraft(null);
    if (parsed !== null) onCommit(parsed);
  };
  const keys = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") commit();
    else if (e.key === "Escape") setDraft(null);
  };
  return (
    <Tooltip label={`The ${label.toLowerCase()} end of the range`} side="top">
      <input
        className="field mono looks-end"
        inputMode="decimal"
        spellCheck={false}
        aria-label={label}
        value={shown}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={keys}
        data-ws={`guided-range-${label.toLowerCase()}`}
      />
    </Tooltip>
  );
}

export default function ResultsCard({ step }: { step: GuidedStep }) {
  const client = useStore((s) => s.client);
  const view = useStore((s) => s.results.view);
  const results = useStore((s) => s.results);
  const data = useStore((s) => s.results.data);
  const status = useStore((s) => s.results.status);
  const slices = useStore((s) => s.slices);
  const iso = useStore((s) => s.iso);
  const streamlines = useStore((s) => s.streamlines);
  const arrows = useStore((s) => s.arrows);
  const section = useStore((s) => s.section);
  const orthographic = useStore((s) => s.orthographic);
  const hiddenNodes = useStore((s) => s.hiddenNodes);
  const projectKey = useStore((s) => s.project?.path ?? s.project?.name ?? "");
  const probing = useStore((s) => s.probePick);
  const probe = useStore((s) => s.probe);
  const options = useFieldOptions();
  const finished = useHasFinishedRun();

  const [prefs, setPrefs] = useState<LookPrefs>(DEFAULT_PREFS);
  const [note, setNote] = useState<{ text: string; busy: boolean; tip?: string }>({ text: "", busy: false });
  /** The look whose ask is still going: its tile lights and its controls show while the server answers. */
  const [pending, setPending] = useState<LookId | null>(null);
  const report: Report = (text, busy, tip) => setNote({ text, busy, tip });

  // Read the run's fields when the card opens and they are not read yet.
  useEffect(() => {
    if (finished && data === null && status === "idle") showResults();
  }, [finished, data, status]);

  // With the fields in hand and none chosen, show the speed: the card's first answer is a coloured model.
  useEffect(() => {
    if (data === null || view.field !== NO_FIELD) return;
    const first = options.find((o) => o.name === "U_mag") ?? options.find((o) => o.name === "p");
    if (first !== undefined) update((live) => chooseField(live, first.name));
  }, [data, view.field, options]);

  // A field asked for before the results were read: shown once they are (after the default above, so it wins).
  const wanted = useRef<string | null>(null);
  useEffect(() => {
    if (data === null || wanted.current === null) return;
    const name = wanted.current;
    wanted.current = null;
    if (options.some((o) => o.name === name)) update((live) => chooseField(live, name));
  }, [data, options]);

  // The first picture of a project's results, once (see the file's head). It waits for the scene's box, then goes ahead without.
  useEffect(() => {
    if (data === null || defaulted.has(projectKey)) return;
    let tries = 0;
    let timer = 0;
    const attempt = () => {
      const ctx = lookContext();
      if (ctx.bounds === null && tries++ < BOX_RETRIES) {
        timer = window.setTimeout(attempt, BOX_RETRY_MS);
        return;
      }
      defaulted.add(projectKey);
      const live = useStore.getState();
      // The server's word that the mesh is one cell thick (`run.fields` `two_d`); absent, the box decides.
      runLooks(firstShowDefaults({ state: lookStateOf(live), ctx, ...(ctx.data?.twoD === true ? { oneCellThick: true } : {}) }), report);
    };
    timer = window.setTimeout(attempt, 0);
    return () => window.clearTimeout(timer);
    // `report` only sets state of this card; the effect is about the results and the project.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, projectKey]);

  const choices: Choice<string>[] = useMemo(() => resultChoices(data === null ? null : options.map((o) => o.name)), [data, options]);
  const others = useMemo(() => options.filter((o) => !(o.name in RESULT_FIELD_WORDS)), [options]);
  const chooseOrLoad = (name: string) => {
    if (data === null) {
      wanted.current = name;
      if (status !== "loading") showResults();
      return;
    }
    update((live) => chooseField(live, name));
  };

  const run = data?.run ?? null;
  const reportHref = useMemo(
    () => (client === null || run === null || (data?.fields.length ?? 0) === 0 ? null : `${client.apiBase.replace(/\/$/, "")}/api/results/report/${encodeURIComponent(run)}?token=${encodeURIComponent(client.token)}`),
    [client, run, data],
  );

  const state = { results, slices, iso, streamlines, arrows, section, orthographic, hiddenNodes };
  const availability = useMemo(() => lookAvailability(data), [data]);
  const active = activeLook(state, data);
  const shown = note.busy && pending !== null ? pending : active;
  // The look the controls belong to, for a slider that rests and then acts: it must still be the one on screen.
  const shownRef = useRef<LookId>(shown);
  shownRef.current = shown;
  const ctx = lookContext();
  const controls = controlsOf(shown, state, ctx, prefs);

  const press = (look: LookId) => {
    // The user has chosen a look: a first picture still waiting for the scene's box must not overwrite it (R2 review).
    defaulted.add(projectKey);
    if (availability[look] !== null) return;
    const fresh = lookStateOf(useStore.getState());
    const actions = activationActions(look, fresh, lookContext(), prefs);
    setPending(actions.some((a) => a.kind === "askIso" || a.kind === "askStreamlines") ? look : null);
    runLooks(actions, report);
  };
  const act = (look: LookId, id: string, value: string) => {
    defaulted.add(projectKey);
    if (shownRef.current !== look) return;
    const out = controlActions(look, id, value, lookStateOf(useStore.getState()), lookContext(), prefs);
    if (out.prefs !== prefs) setPrefs(out.prefs);
    setPending(out.actions.some((a) => a.kind === "askIso" || a.kind === "askStreamlines") ? look : null);
    runLooks(out.actions, report);
  };

  const legend = legendOf(view);
  const midShown = view.slice && data?.slice === true;
  const linesOn = contourLinesOn(slices) ?? (midShown ? false : null);
  const setLines = (on: boolean) => {
    defaulted.add(projectKey);
    const live = useStore.getState();
    const out = linesFor(live.slices, live.results.view.slice && live.results.data?.slice === true, lookContext().midPlane, on, legendOf(live.results.view).bands);
    useStore.setState((s) => ({ slices: out.slices, ...(out.midOff ? { results: { ...s.results, view: { ...s.results.view, slice: false } } } : {}) }));
  };
  const rangeMode = rangeModeOf(view);
  const colouring = view.field !== NO_FIELD;
  const range = effectiveRange(view, data);

  if (!finished) {
    return (
      <GuidedCard step={step}>
        <Note tip="The fields appear here when the run finishes.">Run the case first</Note>
        <div className="guided-actions">
          <Tooltip label={reportBlocker(false, false) ?? ""} side="top" disabled={reportBlocker(false, false) === null}>
            <Button aria-disabled data-ws="guided-report">
              Report…
            </Button>
          </Tooltip>
        </div>
      </GuidedCard>
    );
  }

  return (
    <GuidedCard step={step}>
      {data === null ? (
        <>
          <Chips<string> label="What colours the model" value={null} choices={choices} onChange={chooseOrLoad} />
          <Note tip={status === "error" ? "Press a field to try again." : undefined}>{status === "error" ? "The results could not be read" : "Reading the results…"}</Note>
          <div className="guided-actions">
            <Tooltip label={reportBlocker(true, false) ?? ""} side="top" disabled={reportBlocker(true, false) === null}>
              <Button aria-disabled data-ws="guided-report">
                Report…
              </Button>
            </Tooltip>
          </div>
        </>
      ) : (
        <>
          <div className="looks-fields">
            <Chips<string> label="What colours the model" value={view.field} choices={choices} onChange={chooseOrLoad} wide />
            {others.length > 0 && (
              <div className="select-wrap looks-more">
                <Tooltip label="The run's other fields" side="top">
                  <select className="field" aria-label="More fields" value={others.some((o) => o.name === view.field) ? view.field : ""} onChange={(e) => e.target.value !== "" && chooseOrLoad(e.target.value)} data-ws="guided-more-fields">
                    <option value="" disabled hidden>
                      more ▾
                    </option>
                    {others.map((o) => (
                      <option key={o.name} value={o.name}>
                        {fieldLabel(o.name, fieldUnits(data, o.name))}
                      </option>
                    ))}
                  </select>
                </Tooltip>
                <ChevronsUpDown size={12} className="select-chevron" aria-hidden="true" />
              </div>
            )}
          </div>

          <div className="looks-tiles" role="group" aria-label="Look" data-ws="guided-looks">
            {LOOKS.map((look) => {
              const Icon = ICONS[look.id];
              const why = availability[look.id];
              return (
                <Tooltip key={look.id} label={why ?? look.tip} side="top">
                  <button
                    type="button"
                    className="looks-tile no-drag"
                    aria-pressed={look.id === shown}
                    aria-disabled={why !== null ? true : undefined}
                    data-look={look.id}
                    data-ws={`guided-look-${look.id}`}
                    onClick={() => press(look.id)}
                  >
                    <Icon size={15} aria-hidden="true" />
                    <span>{look.label}</span>
                  </button>
                </Tooltip>
              );
            })}
          </div>

          <TimeBlock />
          <LookControls shown={shown} controls={controls} onChoose={(id, value) => act(shown, id, value)} onSlide={(id, value) => act(shown, id, String(value))} onPress={(id) => act(shown, id, "")} />
          {note.text !== "" && <Note tip={note.tip}>{note.text}</Note>}

          <div className="looks-legend" data-ws="guided-legend">
            <Tooltip label="Auto follows the run, Fit spreads the colours over what is on screen, Locked freezes the range as it is, Manual lets you type its ends" side="top" describe={false}>
              <div className="looks-row">
                <span className="looks-label">Range</span>
                <Chips<RangeMode | "fit">
                  label="Range"
                  value={rangeMode}
                  choices={(["auto", "fit", "locked", "manual"] as const).map((m) => ({ value: m, label: m === "fit" ? "Fit" : RANGE_MODE_WORDS[m], ...(colouring ? {} : { disabled: "Pick a field first" }) }))}
                  onChange={(mode) => {
                    if (mode === "fit") {
                      defaulted.add(projectKey);
                      report("Fitting the range…", true);
                      fitRange()
                        .then((why) => report(why ?? "", false))
                        .catch((e: unknown) => report(e instanceof Error ? e.message : "The range could not be fitted", false));
                      return;
                    }
                    update((live) => setView(live, setRangeMode(live.view, live.data, mode)));
                  }}
                  wide
                />
              </div>
            </Tooltip>
            {rangeMode === "manual" && (
              <div className="looks-row">
                <span className="looks-label" />
                <EndInput label="Min" value={range === null ? null : range.min} onCommit={(v) => update((live) => setView(live, setManualEnd(live.view, live.data, "min", v)))} />
                <EndInput label="Max" value={range === null ? null : range.max} onCommit={(v) => update((live) => setView(live, setManualEnd(live.view, live.data, "max", v)))} />
              </div>
            )}
            <div className="looks-row">
              <span className="looks-label">Map</span>
              <div className="select-wrap looks-map">
                <Tooltip label="The colours the field is drawn with, on the walls, cuts and legend" side="top">
                  <select className="field" aria-label="Colour map" value={legend.map} onChange={(e) => update((live) => setView(live, setMap(live.view, e.target.value as ColormapId)))} data-ws="guided-map">
                    {COLORMAP_IDS.map((id) => (
                      <option key={id} value={id}>
                        {COLORMAPS[id].label}
                      </option>
                    ))}
                  </select>
                </Tooltip>
                <ChevronsUpDown size={12} className="select-chevron" aria-hidden="true" />
              </div>
            </div>
            <Tooltip label="Flat colour steps instead of the smooth map" side="top" describe={false}>
              <div className="looks-row">
                <span className="looks-label">Bands</span>
                <Chips<string>
                  label="Bands"
                  value={String(legend.bands)}
                  choices={BAND_CHOICES}
                  onChange={(n) => {
                    update((live) => setView(live, setBandCount(live.view, Number(n))));
                    // Lines already on follow the new bands, so they stay on the band edges.
                    if (contourLinesOn(useStore.getState().slices) === true) useStore.setState((s) => ({ slices: withContourLines(s.slices, true, Number(n)) }));
                  }}
                  wide
                />
              </div>
            </Tooltip>
            <Tooltip label={linesOn === null ? "Show a slice to draw contour lines on it" : "Contour lines on the slice, at the band edges, coloured by level"} side="top" describe={false}>
              <div className="looks-row">
                <span className="looks-label">Lines</span>
                <Chips<string>
                  label="Contour lines"
                  value={linesOn === true ? "on" : "off"}
                  choices={[
                    { value: "off", label: "Off", ...(linesOn === null ? { disabled: "Show a slice first" } : {}) },
                    { value: "on", label: "On", ...(linesOn === null ? { disabled: "Show a slice first" } : {}) },
                  ]}
                  onChange={(v) => setLines(v === "on")}
                  wide
                />
              </div>
            </Tooltip>
          </div>

          <div className="guided-actions looks-actions">
            <Tooltip label="Click the model to read the field at that point" side="top">
              <Button active={probing} aria-pressed={probing} onClick={() => runAction("view.probe")} data-ws="guided-probe">
                Probe
              </Button>
            </Tooltip>
            <LineButton />
            <Tooltip label="The rest of the Results panel, in Full mode" side="top">
              <Button
                onClick={() => {
                  chooseMode("full");
                  arrangeFor("full");
                  useStore.getState().setStage("Results");
                  openPanel({ panel: "results" });
                }}
                data-ws="guided-more"
              >
                More…
              </Button>
            </Tooltip>
            <Tooltip label="A PNG at print resolution with the legend, and its settings file" side="top">
              <Button onClick={openExportDialog} data-ws="guided-export">
                Export…
              </Button>
            </Tooltip>
            <ViewsMenu
              stream={{ seeds: prefs.streamSeeds, count: prefs.streamCount }}
              onStream={(s) => setPrefs((p) => ({ ...p, streamSeeds: s.seeds, streamCount: s.count }))}
              onTouch={() => defaulted.add(projectKey)}
            />
            {reportHref !== null ? (
              <a className="btn no-drag guided-link" href={reportHref} download={`${run}-report.html`} data-ws="guided-report">
                Report…
              </a>
            ) : (
              <Tooltip label={reportBlocker(true, false) ?? ""} side="top" disabled={reportBlocker(true, false) === null}>
                <Button aria-disabled data-ws="guided-report">
                  Report…
                </Button>
              </Tooltip>
            )}
          </div>
          <LinePlot />
          <AnalyseBlock />
          {probing && probe.phase === "idle" && <Note tip="Click the model to read the field there">Click the model</Note>}
          {probe.phase === "preparing" && <Note tip="The surface the probe reads is being prepared">Preparing…</Note>}
          {(probe.phase === "outside" || probe.phase === "failed") && <Note tip={probe.message}>{probe.phase === "outside" ? "Outside the model" : "No reading"}</Note>}
          {probe.phase === "ready" && probe.values.length > 0 && probe.field !== null && (
            <div className="guided-readout" data-ws="guided-probe-value">
              <strong>
                {fieldLabel(probe.field, fieldUnits(data, probe.field))} {probeRows(probe.values)[0]?.value} {fieldUnits(data, probe.field)}
              </strong>
              {probe.values.length > 1 && (
                <Tooltip label={`The first of ${probe.values.length} points on the line`} side="top">
                  <span className="muted" tabIndex={0}>
                    1 of {probe.values.length}
                  </span>
                </Tooltip>
              )}
            </div>
          )}
        </>
      )}
    </GuidedCard>
  );
}
