/**
 * The fingerprint confirmation: what counts as "the user checked it".
 *
 * **This is the security rule of the Add-host flow** (the Settings spec §2, and `host.trust`'s own
 * design). `host.trust` refuses a fingerprint that does not match the key the network returned, and
 * this component's button is disabled until the typed text does match - so a wrong typing can never
 * reach the command, and the gate is here as well as there.
 *
 * **A confirmation that accepts anything confirms nothing.** The tests below are mostly about what
 * is REJECTED, because that is the half that protects a user: too short, a mismatch, empty, and the
 * `SHA256:` prefix being optional rather than required.
 */
import { describe, expect, it } from "vitest";
import { CONFIRM_CHARS, confirms } from "./fingerprint";

const REAL = "SHA256:AbCdEfGhIjKlMnOpQrStUvWxYz0123456789abcdEFG";

describe("confirms", () => {
  it("accepts the first characters of the real fingerprint", () => {
    expect(confirms(REAL, "AbCdEfGh")).toBe(true);
    // The whole thing is also a confirmation.
    expect(confirms(REAL, REAL)).toBe(true);
  });

  it("ignores case, because nobody should have to match our capitals", () => {
    expect(confirms(REAL, "abcdefgh")).toBe(true);
    expect(confirms(REAL, "ABCDEFGH")).toBe(true);
  });

  it("does not require the SHA256: prefix on EITHER side", () => {
    // **Both forms are accepted**, which the first version got wrong: it stripped the prefix from
    // the fingerprint only, so a user who pasted the WHOLE value - prefix included, which is what a
    // terminal shows - was refused for typing too much. What is compared is the hash.
    expect(confirms(REAL, "AbCdEfGh")).toBe(true);
    expect(confirms(REAL, "SHA256:AbCdEfGh")).toBe(true);
    expect(confirms("AbCdEfGhIjKlMnOpQrStUvWxYz0123456789abcdEFG", "SHA256:AbCdEfGh")).toBe(true);
  });

  it("REJECTS anything shorter than the required characters", () => {
    // **The rule that makes the confirmation worth asking for.** Seven characters of a hash are not
    // enough to compare by eye; the threshold is stated in the UI as well as enforced here.
    expect(confirms(REAL, "")).toBe(false);
    expect(confirms(REAL, "AbCdEfG")).toBe(false);
    expect(confirms(REAL, "a")).toBe(false);
    expect(CONFIRM_CHARS).toBe(8);
  });

  it("REJECTS a wrong value of any length", () => {
    // The whole point: a fingerprint that does not match is what an interception looks like.
    expect(confirms(REAL, "ZzYyXxWw")).toBe(false);
    expect(confirms(REAL, "AbCdEfGhZZZZ")).toBe(false);
  });

  it("treats a missing fingerprint as unconfirmable", () => {
    // **The case a broken connection produces.** An empty fingerprint must not be confirmable by
    // typing anything - otherwise `host.trust` would be asked to check nothing.
    expect(confirms("", "abcdefgh")).toBe(false);
    expect(confirms("", "")).toBe(false);
  });

  it("ignores surrounding whitespace, which a paste brings", () => {
    expect(confirms(REAL, "  AbCdEfGh  ")).toBe(true);
    expect(confirms(`  ${REAL}  `, "  AbCdEfGh  ")).toBe(true);
  });
});
