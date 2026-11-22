/**
 * "Numbers are mm" inside an expression too.
 *
 * The sketcher's cells say a bare number is millimetres, and `25` was: but `10 + c` was refused
 * ("cannot apply + to  and mm"), because inside an expression the 10 reached the evaluator as a
 * plain number. This gives the unit to the numbers that are ADDED or SUBTRACTED, the ones a person
 * means as lengths, and leaves factors alone: `W / 2` and `2 * c` keep their plain 2.
 *
 * The rule, per numeric literal: no unit when it already has one (`30 [deg]`), when it is a factor,
 * divisor or exponent (a `*`, `/` or `^` beside it), or when it is part of a name (`h2`).
 * Everything inside square brackets is a unit and is not read.
 */
const NUMBER_AT = /^(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?/;
const NAME_CHAR = /[A-Za-z0-9_.]/;
const FACTOR = /[*/^]/;

export function mmExpression(text: string): string {
  let out = "";
  let i = 0;
  while (i < text.length) {
    const ch = text[i] as string;
    if (ch === "[") {
      const end = text.indexOf("]", i);
      const stop = end === -1 ? text.length : end + 1;
      out += text.slice(i, stop);
      i = stop;
      continue;
    }
    const match = NUMBER_AT.exec(text.slice(i));
    const before = i > 0 ? (text[i - 1] as string) : "";
    if (match === null || NAME_CHAR.test(before)) {
      out += ch;
      i += 1;
      continue;
    }
    const literal = match[0];
    const after = text.slice(i + literal.length);
    const next = after.trimStart()[0] ?? "";
    const previous = out.trimEnd().slice(-1);
    // `**` is a power in some hands; either way a `*` on a side makes the number a factor.
    const plain = next === "[" || FACTOR.test(next) || FACTOR.test(previous);
    out += plain ? literal : `${literal} [mm]`;
    i += literal.length;
  }
  return out;
}

/** An expression as a cell shows it: the millimetres `mmExpression` added are not repeated back. */
export function withoutMm(text: string): string {
  return text.replace(/(\d) \[mm\]/g, "$1");
}
