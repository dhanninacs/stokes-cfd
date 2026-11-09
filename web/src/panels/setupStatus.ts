/**
 * The set-up status readout (ADR-0022 §11.1, T-049).
 *
 * The text and the dot colour for one `setup.check` answer. Kept as a pure function so the wording
 * §11.1 specifies — "Set-up · needs an outlet · 12 faces unassigned" — is testable without a DOM:
 * it is the part a reviewer reads and the part that silently becomes wrong if a count is reordered.
 *
 * The shape of the line is `Set-up · <next> · <first count>`, and every piece is optional except the
 * label, so a project with nothing to say still shows "Set-up" rather than vanishing:
 *
 * * `not_defined` is muted: nothing has been set up yet, which is the normal state of a new project
 *   and not an error to shout about.
 * * `incomplete` is red, `stale` amber, `ready` green.
 * * The trailing count is the first *non-zero* of §11.1's three figures, in its order: uncovered
 *   faces first ("12 faces unassigned"), then boundaries, then regions. A zero count is not printed,
 *   so a half-set-up project reads as a sentence rather than a table.
 */

import type { SetupCheck, SetupState } from "../api/types";

/** What the readout shows: the line, the dot's colour token and a sentence for the title. */
export interface SetupReadout {
  /** "Set-up · needs an outlet · 12 faces unassigned"; always starts with "Set-up". */
  text: string;
  /** The state, which the caller maps to a colour token. */
  state: SetupState;
  /** A longer description for the readout's tooltip: the findings, or what is left to do. */
  detail: string;
}

/** The colour each state's dot takes, as a theme token name (never a literal colour). */
export const SETUP_DOT: Record<SetupState, string> = {
  not_defined: "var(--text-muted)",
  incomplete: "var(--danger)",
  stale: "var(--warning)",
  ready: "var(--success)",
};

/** The first non-zero figure of §11.1's three, as its own phrase; "" when there is none. */
export function setupCountPhrase(check: SetupCheck): string {
  const { uncovered_faces: faces, boundaries, regions } = check.counts;
  // **Once the set-up is ready, the open faces are walls by rule**, and "unassigned" in the strip read
  // as a problem left to fix while the Boundaries card said "wall by default". Said as what they are.
  if (faces > 0) return `${faces} face${faces === 1 ? "" : "s"} ${check.state === "ready" ? "left as wall" : "unassigned"}`;
  if (boundaries > 0 && check.findings.some((f) => f.severity === "error")) {
    return `${boundaries} boundar${boundaries === 1 ? "y" : "ies"}`;
  }
  if (regions > 0 && check.state === "ready") return `${regions} region${regions === 1 ? "" : "s"}`;
  return "";
}

/**
 * The readout for an answer, or null when there is none yet: the caller shows nothing rather than
 * claiming a state it has not been told.
 */
export function setupReadout(check: SetupCheck | null): SetupReadout | null {
  if (check === null) return null;
  const parts = ["Set-up"];
  const next = check.next.trim();
  // `next` sometimes already begins with "Set-up", and repeating it reads as a stutter.
  if (next !== "" && !/^set[- ]?up\b/i.test(next)) parts.push(next);
  const count = setupCountPhrase(check);
  if (count !== "") parts.push(count);
  return {
    text: parts.join(" · "),
    state: check.state,
    detail:
      check.findings.length === 0
        ? "Everything needed for a run is defined."
        : check.findings.map((f) => f.suggestion ?? f.message).join(" · "),
  };
}
