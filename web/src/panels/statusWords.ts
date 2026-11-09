import type { NodeStatus } from "../api/types";

/**
 * What a row's status dot MEANS, in words (claude-win2's 0200 item 6, the guide's finding 7).
 *
 * **The guide's words**: *"Badges and dots explain nothing. Dots show a native tooltip with the raw
 * word 'ok', 'new'; every folder is a green 'ok'."* A user reading "ok" learns nothing - the useful
 * statement is what is ready and, when something is not, **what it needs**.
 *
 * **Green is not news.** The guide suggests "no dot when ok", and that is the stronger reading: a dot
 * on every folder trains a user to ignore dots, so the ones that matter stop working. The tone and the
 * wording here are what the dot renders; `ready` is what a caller uses to hide it.
 */
export interface StatusWord {
  /** The sentence a tooltip shows: what the state is, and what to do about it. */
  text: string;
  /** True when nothing needs attention, which is when a caller may show no dot at all. */
  ready: boolean;
}

const WORDS: Record<NodeStatus, StatusWord> = {
  ok: { text: "Ready", ready: true },
  // **`new` is not a warning**: the node exists and has not been used yet, which is the ordinary
  // state of a fresh settings node. Saying "needs attention" here would make every new project amber.
  new: { text: "Not used yet", ready: false },
  warning: { text: "Needs attention", ready: false },
  stale: { text: "Out of date: something it depends on changed", ready: false },
  failed: { text: "Failed: open it to see the reason", ready: false },
};

/** The words for a status; an unknown status says so rather than pretending to be ready. */
export function statusWord(status: string): StatusWord {
  return WORDS[status as NodeStatus] ?? { text: `Status: ${status}`, ready: false };
}

/**
 * What a folder's count badge means.
 *
 * The guide's finding 7: *"A grey number on a folder is `children_count` including virtual rows, so
 * `/scenes` says 4 before any run and `/geometry` says 3 for three folders."* The badge is right; what
 * was missing is a sentence saying it counts the items **inside**, which is what this gives.
 */
export function countWords(count: number): string {
  return `${count} ${count === 1 ? "item" : "items"} inside`;
}
