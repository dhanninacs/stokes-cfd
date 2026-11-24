/**
 * Where a boundary's values that vary in time are reached (ADR-0022 addendum 2026-11-24).
 *
 * `VariesToggle` is the small "Varies in time" button beside a value: pressed while the value has a profile,
 * and it opens the editor. `BoundaryTimeProfiles` is the Full mode block on a boundary's Properties page: a
 * row per value the kind varies and uses (the speed of a velocity inlet given normal or along a vector, a
 * mass flow, an outlet's pressure, an inlet's temperature while the energy equation is on), each with its
 * toggle, its profile in a few words, and the shared editor (`TimeProfileEditor`) when open. The Guided
 * Boundaries card puts the same toggle and editor under its rows.
 */
import { useMemo, useState } from "react";
import { Waves } from "lucide-react";
import type { ConditionKind, NodeSummary } from "../api/types";
import { Note } from "../components/Note";
import { useStore } from "../state/store";
import { readSetupContext } from "../setup/basicSets";
import { inUseParams } from "./conditions";
import TimeProfileEditor from "./TimeProfileEditor";
import { VALUE_WORDS, constantKeptNote, profileSummary, storedProfiles, varyingOf } from "./timeProfileModel";
import "./timeProfile.css";

export function VariesToggle({ pressed, expanded, controls, name, onClick }: { pressed: boolean; expanded: boolean; controls?: string; name: string; onClick: () => void }) {
  return (
    <button
      type="button"
      className="tp-toggle no-drag"
      aria-pressed={pressed}
      aria-expanded={expanded}
      aria-controls={controls}
      aria-label={`${name} varies in time`}
      title={pressed ? "Varies in time: edit the profile (Constant makes it one value again)" : "Make this value vary in time"}
      onClick={onClick}
      data-ws="varies-toggle"
    >
      <Waves size={14} aria-hidden="true" />
    </button>
  );
}

/** A quantity parameter's SI value, or null. */
export function siOf(raw: unknown): number | null {
  if (typeof raw === "number" && Number.isFinite(raw)) return raw;
  if (typeof raw === "object" && raw !== null && typeof (raw as { value?: unknown }).value === "number") return (raw as { value: number }).value;
  return null;
}

/**
 * The values of this boundary that can vary in time now: the kind's `profiled` values it uses with its
 * switches, the temperature only while energy is solved.
 */
export function profilableValues(node: NodeSummary, kind: ConditionKind | undefined, energy: boolean): string[] {
  if (kind === undefined) return [];
  const used = new Set(inUseParams(kind, node.params).map((p) => p.name));
  return (kind.profiled ?? []).filter((name) => used.has(name) && (name !== "temperature" || energy));
}

/** The label the Properties page gives a value's constant field: its schema title, else its name. */
export function fieldTitle(kind: ConditionKind | undefined, name: string): string {
  return kind?.params.find((p) => p.name === name)?.schema.title ?? name;
}

export function BoundaryTimeProfiles({ node }: { node: NodeSummary }) {
  const kinds = useStore((s) => s.conditionKinds);
  const tree = useStore((s) => s.tree);
  const energy = useMemo(() => readSetupContext(tree).energy, [tree]);
  const kind = kinds.find((k) => k.id === node.params.kind);
  const values = profilableValues(node, kind, energy);
  const held = storedProfiles(node.params);
  const varies = varyingOf(node);
  const [open, setOpen] = useState<string | null>(null);
  if (values.length === 0) return null;
  return (
    <section className="tp-section" aria-label="Values over time" data-ws="boundary-time-profiles">
      {values.map((name) => {
        const stored = held[name] ?? null;
        const varying = varies.includes(name);
        const editorId = `tp-${node.id}-${name}`;
        const isOpen = open === name;
        return (
          <div key={name} className="tp-section" data-parameter={name}>
            <div className="tp-section-row">
              <VariesToggle pressed={varying} expanded={isOpen} controls={editorId} name={VALUE_WORDS[name] ?? name} onClick={() => setOpen(isOpen ? null : name)} />
              <span className="tp-section-label">{VALUE_WORDS[name] ?? name}</span>
              <span className="tp-summary" title={varying ? profileSummary(stored, name) : undefined}>
                {varying ? profileSummary(stored, name) : "constant"}
              </span>
            </div>
            {varying && (
              <Note className="tp-kept" tip={`The constant in the ${fieldTitle(kind, name)} field further down is kept for when the value is made constant again; the run follows the profile.`} data-ws="profile-constant-kept">
                {constantKeptNote(fieldTitle(kind, name))}
              </Note>
            )}
            {isOpen && (
              <div id={editorId}>
                <TimeProfileEditor
                  key={`${node.id}:${name}`}
                  boundaryId={node.id}
                  boundaryName={node.name}
                  parameter={name}
                  constant={siOf(node.params[name])}
                  stored={stored}
                  onDone={() => setOpen(null)}
                />
              </div>
            )}
          </div>
        );
      })}
    </section>
  );
}
