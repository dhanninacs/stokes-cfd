/**
 * The words the cards print, kept pure so the rules are tested.
 *
 * Import rule: no React, no store, no DOM.
 */

/** A server sentence without the identifier in front of it ("mesh.no_host: the host has..." reads "the host has..."). */
export function plainSentence(text: string): string {
  return text.replace(/^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+:\s*/, "");
}
