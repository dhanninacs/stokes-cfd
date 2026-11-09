/** Subsequence ("fuzzy") matching for the command palette. */

import type { CommandSchema } from "../api/types";

export interface FuzzyMatch {
  score: number;
  /** Positions in the text of the matched characters, ascending. */
  indices: number[];
}

const WORD_START = /[\s._\-/:]/;
const START_BONUS = 3;
const RUN_BONUS = 2.5;
const GAP_PENALTY = 0.15;

/**
 * Does every character of `term` occur in `text` in order? If so, the best-scoring alignment: matches
 * at the start of a word and runs of adjacent characters score higher, long gaps and late starts lower.
 */
export function fuzzyMatch(term: string, text: string): FuzzyMatch | null {
  const q = term.toLowerCase();
  const t = text.toLowerCase();
  const n = q.length;
  const m = t.length;
  if (n === 0) return { score: 0, indices: [] };
  if (n > m) return null;

  let probe = 0; // cheap reject before the quadratic pass
  for (let j = 0; j < m && probe < n; j++) if (t[j] === q[probe]) probe++;
  if (probe < n) return null;

  const NONE = Number.NEGATIVE_INFINITY;
  const best: number[][] = Array.from({ length: n }, () => new Array<number>(m).fill(NONE));
  const from: number[][] = Array.from({ length: n }, () => new Array<number>(m).fill(-1));
  for (let i = 0; i < n; i++) {
    for (let j = i; j < m; j++) {
      if (t[j] !== q[i]) continue;
      const startsWord = j === 0 || WORD_START.test(t[j - 1]);
      const gain = 1 + (startsWord ? START_BONUS : 0);
      if (i === 0) {
        best[0][j] = gain - Math.min(j, 20) * 0.05;
        continue;
      }
      let top = NONE;
      let source = -1;
      for (let k = i - 1; k < j; k++) {
        if (best[i - 1][k] === NONE) continue;
        // Skipping whole words to land on a word start is deliberate; skipping letters inside a word is not.
        const link = k === j - 1 ? RUN_BONUS : startsWord ? 0 : -GAP_PENALTY * (j - k - 1);
        if (best[i - 1][k] + link > top) {
          top = best[i - 1][k] + link;
          source = k;
        }
      }
      if (source >= 0) {
        best[i][j] = top + gain;
        from[i][j] = source;
      }
    }
  }
  let end = -1;
  let score = NONE;
  for (let j = n - 1; j < m; j++) {
    if (best[n - 1][j] > score) {
      score = best[n - 1][j];
      end = j;
    }
  }
  if (end < 0) return null;
  const indices: number[] = [];
  for (let i = n - 1, j = end; i >= 0; i--) {
    indices.push(j);
    j = from[i][j];
  }
  indices.reverse();
  // Of two equally good alignments, the shorter text is the more specific hit.
  return { score: score - m * 0.02 + (t === q ? 10 : 0), indices };
}

/** What can be ranked: a name and a description. An API command is one; so is any other palette entry. */
export interface Rankable {
  name: string;
  description: string;
  /**
   * The words a user would type to find this, when they differ from `name`.
   *
   * **A command's `name` is an identifier** (`project.sample_open`), while a window item's is the words
   * on screen ("Open Results"). Ranking only `name` compared a query against identifiers, so "open"
   * matched `project.sample_open`'s description as strongly as the item literally called "Open Results" -
   * and on the second use of the palette the wrong row came first (G-301's H-3). A label match now
   * outranks everything.
   */
  label?: string;
}

export interface RankedCommand<T extends Rankable = CommandSchema> {
  command: T;
  score: number;
  nameIndices: number[];
  descriptionIndices: number[];
}

/**
 * Entries matching every word of the query, best first. A word can match the name (worth double) or
 * the description; entries in `recent` get a boost, the most recent most. `keyOf` is how an entry
 * is written in `recent` (its name, unless the caller keeps another key).
 */
export function rankCommands<T extends Rankable>(
  commands: readonly T[],
  query: string,
  recent: readonly string[],
  keyOf: (entry: T) => string = (entry) => entry.name,
): RankedCommand<T>[] {
  const terms = query.trim().split(/\s+/).filter(Boolean);
  const ranked: RankedCommand<T>[] = [];
  for (const command of commands) {
    let score = 0;
    const nameIndices = new Set<number>();
    const descriptionIndices = new Set<number>();
    let all = true;
    for (const term of terms) {
      const inLabel = command.label === undefined ? null : fuzzyMatch(term, command.label);
      const inName = fuzzyMatch(term, command.name);
      const inDescription = fuzzyMatch(term, command.description);
      if (!inLabel && !inName && !inDescription) {
        all = false;
        break;
      }
      /*
        **The label outranks the key, which outranks the description.** The label is what the user is
        reading; the key is an identifier they may not know; the description is a hint. Weights 4 / 2 /
        0.6 keep the old key-over-description order and put the label above both, which is the whole of
        the fix - the ranking was never wrong about *what* matched, only about which match mattered.
      */
      score += Math.max(
        inLabel ? inLabel.score * 4 : Number.NEGATIVE_INFINITY,
        inName ? inName.score * 2 : Number.NEGATIVE_INFINITY,
        inDescription ? inDescription.score * 0.6 : Number.NEGATIVE_INFINITY,
      );
      inName?.indices.forEach((i) => nameIndices.add(i));
      inDescription?.indices.forEach((i) => descriptionIndices.add(i));
    }
    if (!all) continue;
    const rank = recent.indexOf(keyOf(command));
    if (rank >= 0) score += 4 - rank * 0.5;
    ranked.push({ command, score, nameIndices: [...nameIndices].sort((a, b) => a - b), descriptionIndices: [...descriptionIndices].sort((a, b) => a - b) });
  }
  return ranked.sort((a, b) => b.score - a.score || a.command.name.localeCompare(b.command.name));
}
