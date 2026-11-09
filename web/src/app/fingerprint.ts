/**
 * Whether what a user typed confirms a host's fingerprint.
 *
 * **Its own module, because it is the security rule of the Add-host flow and needs testing without a
 * browser.** The component that uses it imports the state store, which reads `window` as it loads -
 * so a test importing the component cannot run at all (`ReferenceError: window is not defined`,
 * measured). A rule this important should not be unreachable from a test.
 *
 * **`host.trust` performs the same comparison on the server** and refuses a mismatch; this gate is
 * what stops a wrong typing reaching it, and the two agreeing is what makes the flow safe rather
 * than merely convenient.
 */

/** How many leading characters of the fingerprint a user must type (the spec's "first 8"). */
export const CONFIRM_CHARS = 8;

/**
 * Whether `typed` confirms `fingerprint`.
 *
 * **Case-insensitive, and the `SHA256:` prefix is optional on EITHER side.** A user copying from a
 * terminal has the prefixed form and one typing from a note may not, so both are stripped before
 * comparing - what is compared is the hash, and nothing that merely CONTAINS it is accepted.
 *
 * **At least `CONFIRM_CHARS`**, because seven characters of a hash are not enough to compare by eye -
 * which is the whole reason the number is stated in the UI as well as enforced.
 */
export function confirms(fingerprint: string, typed: string): boolean {
  // **Both sides lose the prefix.** Stripping it from the fingerprint only meant that a user who
  // pasted the WHOLE value - prefix included, which is what a terminal shows - was refused for
  // typing too much. Measured by the test that types the real value back.
  const clean = strip(fingerprint);
  const given = strip(typed);
  if (clean === "" || given.length < CONFIRM_CHARS) return false;
  return clean.startsWith(given);
}

/**
 * The bare hash: no `SHA256:`, no surrounding space, lower case.
 *
 * **Trimmed BEFORE the prefix is removed**, because a paste often arrives as
 * `"  SHA256:abc…  "`. Stripping first missed the prefix behind the leading space and the value
 * compared was the whole string - measured by the whitespace test, which is why it exists.
 */
function strip(value: string): string {
  return value.trim().replace(/^SHA256:/i, "").trim().toLowerCase();
}
