/**
 * The user-text gate: what a user can READ must not leak our internal vocabulary.
 *
 * **Why a source scan rather than a component test.** These strings are spread across every panel and
 * menu, and the failure is invisible in a screenshot: `T-211` in a tooltip looks like a part number
 * to anyone who does not work on this repository. A scan over the whole tree is the only check that
 * covers a file nobody thought to test - and the owner's own instruction was "the internal ticket
 * numbers never appear in user text".
 *
 * **It reads source, not rendered output**, so it cannot see a string assembled at runtime. That is a
 * deliberate limit: the alternative is mounting every panel, which would miss the strings on the paths
 * a test does not drive. A plain literal is what these are in practice, and the scan is what caught
 * the pattern being absent today.
 *
 * The rules, all from claude-win2's 2000 message:
 *
 *   1. **no ticket numbers** - `T-211`, `T-220b`, `G-305` in anything a user reads;
 *   2. **no "coming next"** - an entry that does nothing yet says so, rather than promising work;
 *   3. **no ADR references** - `ADR-0024` is our vocabulary, not the user's.
 *
 * A comment or a test file is NOT user text, and tickets there are useful - a reader of the code
 * needs to know which task a function serves. So the scan strips comments and skips tests.
 */
import { readFileSync, readdirSync, statSync } from "node:fs";
import { describe, expect, it } from "vitest";

/**
 * Every `.py` under the packages whose strings reach a user, minus the tests.
 *
 * **Added because the gate MISSED one** (claude-win2's review item 9): the phrase *"over SSH (coming
 * next)"* lived in `stokes/run/local.py`, and this scan only read `.ts`/`.tsx` - so a rule that
 * existed caught nothing. The message a host row shows is written in Python, which makes the backend
 * part of "what a user reads".
 *
 * `agent/` is included because its probe answers are shown in the Compute section; `docs/` is not,
 * because a document is written for a developer and may name a ticket.
 */
function pythonSources(): { path: string; text: string }[] {
  const out: { path: string; text: string }[] = [];
  const roots = ["../stokes", "../agent"];
  for (const root of roots) {
    const base = new URL(`../../${root.replace("../", "")}/`, import.meta.url);
    const walk = (dir: URL) => {
      for (const entry of readdirSync(dir, { withFileTypes: true })) {
        if (entry.name === "__pycache__") continue;
        const url = new URL(`${entry.name}${entry.isDirectory() ? "/" : ""}`, dir);
        if (entry.isDirectory()) {
          walk(url);
          continue;
        }
        if (!entry.name.endsWith(".py") || entry.name.startsWith("test_")) continue;
        // **The path from the repository root**, not the bare file name: the first version reported
        // `stokes/shapes.py`, which does not exist - the file is `stokes/geometry/shapes.py` - so the
        // failure named a file a reader could not open. A gate's output is what somebody acts on.
        const from = url.pathname.split("/Documents/stokes/").at(-1) ?? entry.name;
        out.push({ path: from, text: readFileSync(url, "utf8") });
      }
    };
    walk(base);
  }
  return out;
}

/** Every `.ts`/`.tsx` under `src`, minus the tests. */
function sources(dir: URL): { path: string; text: string }[] {
  const out: { path: string; text: string }[] = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const url = new URL(`${entry.name}${entry.isDirectory() ? "/" : ""}`, dir);
    if (entry.isDirectory()) {
      out.push(...sources(url));
      continue;
    }
    if (!/\.tsx?$/.test(entry.name) || /\.test\.tsx?$/.test(entry.name)) continue;
    out.push({ path: entry.name, text: readFileSync(url, "utf8") });
  }
  return out;
}

/**
 * The source with comments and string-free noise removed.
 *
 * **A line-based strip, which is enough for what this checks.** The point is to ignore `// T-211` and
 * `/* T-211 *\/` while keeping `label: "T-211"` - and a ticket number inside a multi-line template
 * literal is caught because the literal's own line is not a comment.
 */
function codeOnly(text: string): string {
  return text
    .split("\n")
    .map((line) => {
      const trimmed = line.trim();
      // **`#` as well as the JavaScript forms.** Python's comment marker was missing, so every
      // Python comment was scanned as user text - and the FIRST thing that caught was my own comment
      // in `stokes/run/local.py` quoting the phrase this rule bans, which is a comment explaining the
      // fix rather than a message anybody reads.
      if (
        trimmed.startsWith("//") ||
        trimmed.startsWith("*") ||
        trimmed.startsWith("/*") ||
        trimmed.startsWith("#")
      )
        return "";
      return line;
    })
    .join("\n");
}

/**
 * Strings a user could read: **every literal that carries a ticket number or a phrase we forbid.**
 *
 * **My first version looked for named properties** (`label: "…"`) and proved useless by mutation: a
 * ticket in `row("maxSkewness", "Max skewness (T-211)")` - an ARGUMENT, which is how the mesh panel
 * builds its rows - passed every rule. A scan that only sees the spelling you thought of is the same
 * mistake as a rule that only refuses the attack you imagined.
 *
 * So this takes the opposite approach: **every quoted literal in the file is a candidate**, and the
 * rules decide. A comment is stripped first, because `// T-211` is a useful thing for a reader of
 * the code and not user text - and the boundary between the two is exactly what a `//` marks.
 */
/**
 * The source with Python docstrings blanked out, so the rules see only string literals.
 *
 * A docstring is the Python analogue of a code comment: it explains the module to whoever reads it,
 * and naming a ticket there is useful. A message a user reads is a string LITERAL, so blanking the
 * docstrings is what separates the two - the same job `codeOnly` does for `//` and block comments.
 *
 * **Written because the first version read them**: thirteen docstrings that name a ticket for a
 * reader of the CODE were reported as user text. That is the failure mode to avoid - a gate that
 * refuses good code gets relaxed by the next person, and then it protects nothing.
 */
function stripDocstrings(text: string): string {
  // A triple-quoted run to the same delimiter, non-greedy. Blanked rather than removed so the line
  // count is unchanged, which keeps a reported position meaningful.
  const triple = /("""|''')[\s\S]*?\1/g;
  return text.replace(triple, (found) => "\n".repeat((found.match(/\n/g) ?? []).length));
}

function userStrings(text: string): string[] {
  const found: string[] = [];
  // Every single-, double- or back-quoted literal, on one line (which is where these live).
  for (const match of text.matchAll(/(["'`])([^"'`\n]{2,})\1/g)) found.push(match[2]);
  // And JSX text between tags, which is not quoted at all.
  for (const match of text.matchAll(/>\s*([^<>{}\n]{3,})\s*</g)) found.push(match[1]);
  return found;
}

const SRC = new URL("../", import.meta.url);
// **Both languages, because a rule that reads one of them missed this bug.** `FILES` is the union,
// so every rule below covers the Python messages a host row or a panel shows.
const FILES = [...sources(SRC), ...pythonSources()];

/** The strings that break a rule, as `path: the offending text`. */
function offenders(pattern: RegExp): string[] {
  const out: string[] = [];
  for (const file of FILES) {
    // **`stripDocstrings` BEFORE `codeOnly`**: a Python docstring explains the module to whoever reads
    // the code, so it is the analogue of a `//` comment and not user text. My first version left this
    // call as `codeOnly(file.text)` and the added pass never ran - which is why the same thirteen
    // docstrings kept being reported after the "fix".
    for (const text of userStrings(codeOnly(stripDocstrings(file.text)))) {
      if (pattern.test(text)) out.push(`${file.path}: ${text.trim().slice(0, 80)}`);
    }
  }
  return out;
}

describe("the user-text gate", () => {
  it("finds sources to scan, so it cannot pass by looking at nothing", () => {
    // **A scan that reads no files passes every rule.** This is the assertion that makes the rest
    // meaningful, and the failure it prevents is silent: a wrong `src` path would make all three
    // rules below green for ever.
    expect(FILES.length).toBeGreaterThan(100);
    const withStrings = FILES.filter((f) => userStrings(codeOnly(f.text)).length > 0);
    expect(withStrings.length).toBeGreaterThan(30);
  });

  it("has no ticket numbers in anything a user reads", () => {
    expect(offenders(/\b[TG]-\d{2,4}[a-z]?\b/)).toEqual([]);
  });

  it("has no 'coming next' promises in anything a user reads", () => {
    expect(offenders(/coming (next|soon)|not implemented yet|TO DO/i)).toEqual([]);
  });

  it("has no ADR references in anything a user reads", () => {
    expect(offenders(/\bADR-\d{3,4}\b/)).toEqual([]);
  });

  it("has no MILESTONE codes in anything a user reads", () => {
    // **`M3`** (claude-win2's package-two verdict, leftover 2: a finding read *"set the physics
    // models in M3"*). A milestone code is the same class as a ticket number and an ADR reference -
    // an identifier this repository uses for itself - and it reached a user because the rule above
    // listed `T-` and `G-` and not the third kind.
    //
    // **The pattern was run over the corpus BEFORE it was adopted**, as every rule here has been: a
    // first attempt (`\bM\d+\b`) flagged `M12 bolt`, a part name a person would write, so it is
    // `\bM[1-9]\b(?!\.\d)` - one digit that is not the start of a decimal, which catches `M3`,
    // `M3.` and "the M3 build" and leaves `M12 bolt` and `M1.5` alone. Measured on the tree: zero
    // non-comment matches when it was added.
    // **SVG path data is excluded, and my first attempt at that exclusion was worse than useless**:
    // `/[MLHVQCSTAZ...]-?\d/` matches any letter followed by a digit, so `d-argument`, `g-` and
    // `e-` in ordinary words made EVERY string look like a path - and the mutation check, which swaps
    // a real `M3` back into a help string, passed. That is exactly why a rule is not adopted until it
    // is shown to fail.
    //
    // The signal that actually separates them: **a path has no words**. `"M4 1.6v4.8M1.6 4h4.8"` is
    // short, has path letters and digits, and not one run of three lowercase letters; prose such as
    // `an engine id (ignored until M3)` has plenty. Measured against five real paths and three
    // prose strings.
    const isPathData = (hit: string): boolean => {
      // **The TEXT after `path: `, not the whole report** - `offenders` formats its results as
      // `"topbarItems.tsx: M4 1.6v4.8M1.6 4h4.8"`, and the file name's own lowercase words made every
      // hit look like prose.
      const s = hit.includes(": ") ? hit.slice(hit.indexOf(": ") + 2) : hit;
      const t = s.replace(/^["'`]|["'`]$/g, "").trim();
      if (t.length > 60) return false;
      if (/[a-z]{3,}/.test(t)) return false;
      return /[MLHVQCSTAZmlhvqcstaz]/.test(t) && /\d/.test(t);
    };
    expect(offenders(/\bM[1-9]\b(?!\.\d)/).filter((hit) => !isPathData(hit))).toEqual([]);
  });

  it("has no internal error CODES in a message shown to a user", () => {
    // **`project.busy: Stop the run first, or use Save a copy`** (claude-win2's 1030 review). The
    // code is a stable identifier nobody outside this repository recognises, and it sat in front of
    // the one part the user needs. The failure path now passes the message as the sentence and the
    // code as a separate field, so a code in a TOAST means someone went back to concatenating.
    //
    // **A console line is not this rule's business.** `log("error", \`mesh.generate: ${message}\`)`
    // is right as it is: a reader of the log wants the identifier, and the Console tab is where a
    // developer looks. So this scans only the argument of `pushToast`, which is the one call that
    // puts text in front of a user.
    const offenders = FILES.flatMap((file) => {
      const found: string[] = [];
      for (const match of codeOnly(file.text).matchAll(/pushToast\(\s*"[a-z]+"\s*,\s*(["'`])([^"'`\n]*)\1/g)) {
        if (/^[a-z_]+\.[a-z_]+: \S/.test(match[2])) found.push(`${file.path}: ${match[2].slice(0, 60)}`);
      }
      return found;
    });
    expect(offenders).toEqual([]);
  });

  it("would catch a code prefixed to a toast message, and leaves the console alone", () => {
    // **The rule proves it can fail, and that it is aimed at the right call.** A rule that refused
    // every literal would fail on the six legitimate console lines below, and a rule that refused
    // none would never have caught the one that reached a user.
    const pattern = /^[a-z_]+\.[a-z_]+: \S/;
    const toastScan = (source: string): string[] =>
      [...source.matchAll(/pushToast\(\s*"[a-z]+"\s*,\s*(["'`])([^"'`\n]*)\1/g)]
        .map((m) => m[2])
        .filter((text) => pattern.test(text));
    expect(toastScan('pushToast("error", `project.busy: Stop the run first`)')).toHaveLength(1);
    expect(toastScan('pushToast("error", "Stop the run first, or use Save a copy")')).toHaveLength(0);
    // The console line this rule deliberately allows.
    expect(toastScan('log("error", `mesh.generate: ${message}`)')).toHaveLength(0);
  });

  it("would catch each rule if it were broken", () => {
    // **The gate proves it can fail.** A rule that cannot fail is worse than no rule, because it
    // reads as coverage - so each pattern is tried against a string it must reject.
    expect(/\b[TG]-\d{2,4}[a-z]?\b/.test("T-211")).toBe(true);
    expect(/\b[TG]-\d{2,4}[a-z]?\b/.test("Generate mesh")).toBe(false);
    expect(/coming (next|soon)/i.test("Results come next")).toBe(false);
    expect(/coming (next|soon)/i.test("Coming soon")).toBe(true);
    expect(/\bADR-\d{3,4}\b/.test("ADR-0024")).toBe(true);
    expect(/\bADR-\d{3,4}\b/.test("Mesh settings")).toBe(false);
    expect(/^[a-z_]+\.[a-z_]+: \S/.test("project.busy: Stop the run first")).toBe(true);
    expect(/^[a-z_]+\.[a-z_]+: \S/.test("ui.motion-reduced")).toBe(false);
    expect(/^[a-z_]+\.[a-z_]+: \S/.test("Stop the run first, or use Save a copy")).toBe(false);
  });
});

/**
 * **Rule 5: no note over 8 words on a panel** (the owner's standing rule, 2026-11-01: *"no long sentences in
 * the GUI"*). A few words go on the control and the sentence goes in a tooltip (`title` / `aria-description`,
 * or `<Note tip=...>` from `components/Note.tsx`).
 *
 * **What it reads**: the literal text between JSX tags in `panels/**` (the set-up, mesh, solve, results and
 * explore panels), found by walking each opening tag with a brace and quote counter so a `>` inside a prop
 * expression, a generic `Map<string, X>` or a comparison is not mistaken for a tag. A text node cut by a
 * `{expression}` counts per piece. **An alert is exempt** (`role="alert"`): a refusal has to say what went wrong.
 *
 * **Its limit**: a sentence that reaches the screen through `{variable}` is not literal text and is not seen;
 * those moved to tooltips by hand (the engine line, the section descriptions).
 */
const MAX_NOTE_WORDS = 8;

function withoutComments(text: string): string {
  return text.replace(/\/\*[\s\S]*?\*\//g, (found) => found.replace(/[^\n]/g, " ")).replace(/(^|\s)\/\/[^\n]*/g, "$1");
}

/** The literal JSX text nodes of a file, each with the opening tag it follows. */
function jsxTextNodes(source: string): { tag: string; text: string; line: number }[] {
  const text = withoutComments(source);
  const found: { tag: string; text: string; line: number }[] = [];
  const opening = /<([A-Za-z][\w.]*)/g;
  for (let match = opening.exec(text); match !== null; match = opening.exec(text)) {
    // `Map<string>`, `useState<T>` and `a<b` are not tags: a tag follows whitespace, `(`, `{`, `>` or an operator.
    if (/[\w\])]/.test(text[match.index - 1] ?? " ")) continue;
    let end = match.index + match[0].length;
    let depth = 0;
    let quote: string | null = null;
    for (; end < text.length; end++) {
      const c = text[end];
      if (quote !== null) {
        if (c === "\\") end++;
        else if (c === quote) quote = null;
      } else if (c === '"' || c === "'" || c === "`") quote = c;
      else if (c === "{") depth++;
      else if (c === "}") {
        depth--;
        if (depth < 0) break;
      } else if (c === ">" && depth === 0) break;
    }
    if (end >= text.length || depth < 0) continue;
    opening.lastIndex = end;
    if (text[end - 1] === "/") continue; // self-closing: no text after it belongs to this tag
    let stop = end + 1;
    while (stop < text.length && !"<>{}".includes(text[stop])) stop++;
    found.push({ tag: text.slice(match.index, end + 1), text: text.slice(end + 1, stop), line: text.slice(0, end + 1).split("\n").length });
  }
  return found;
}

/** Literal JSX text over `max` words, as `path:line: the text`; alerts are exempt. */
function longNotes(path: string, source: string, max = MAX_NOTE_WORDS): string[] {
  const out: string[] = [];
  for (const node of jsxTextNodes(source)) {
    const words = node.text.replace(/\s+/g, " ").trim();
    if (!/[A-Za-z]{2}/.test(words) || words.split(" ").length <= max) continue;
    if (/\brole=["']alert["']/.test(node.tag)) continue;
    out.push(`${path}:${node.line}: ${words.slice(0, 80)}`);
  }
  return out;
}

/** Every `.tsx` under `src/panels`, minus the tests, with its path from the web folder. */
function panelSources(dir = new URL("src/panels/", SRC), prefix = "src/panels/"): { path: string; text: string }[] {
  const out: { path: string; text: string }[] = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const url = new URL(`${entry.name}${entry.isDirectory() ? "/" : ""}`, dir);
    if (entry.isDirectory()) out.push(...panelSources(url, `${prefix}${entry.name}/`));
    else if (/\.tsx$/.test(entry.name) && !/\.test\.tsx$/.test(entry.name)) out.push({ path: `${prefix}${entry.name}`, text: readFileSync(url, "utf8") });
  }
  return out;
}

describe("the notes rule: a few words on the panel, the sentence in a tooltip", () => {
  it("scans the panels, so it cannot pass by reading nothing", () => {
    expect(panelSources().length).toBeGreaterThan(40);
    expect(panelSources().flatMap((f) => jsxTextNodes(f.text)).length).toBeGreaterThan(300);
  });

  it("has no literal note over 8 words in panels/**", () => {
    expect(panelSources().flatMap((f) => longNotes(f.path, f.text))).toEqual([]);
  });

  it("has no literal note over 8 words in the app's dialogs (app/**)", () => {
    const app = panelSources(new URL("src/app/", SRC), "src/app/");
    expect(app.length).toBeGreaterThan(10);
    expect(app.flatMap((f) => longNotes(f.path, f.text))).toEqual([]);
  });

  it("would catch a long note, and leaves a short one, an alert and a tooltip alone", () => {
    // **The rule proves it can fail**: each case is a real shape from the panels.
    const long = '<p className="muted">Nothing is selected. Pick an item in the tree or a patch in the viewport.</p>';
    expect(longNotes("x.tsx", long)).toHaveLength(1);
    // A sentence after a tag whose props hold JSX (the design table's empty state) is still read.
    const afterJsxProp = '<Empty action={<Button onClick={() => go()}>Go</Button>}>No design points. Generate the table from the variables, or add a point.</Empty>';
    expect(longNotes("x.tsx", afterJsxProp)).toHaveLength(1);
    expect(longNotes("x.tsx", '<Note tip="Pick an item in the tree or a patch in the viewport.">Nothing is selected</Note>')).toEqual([]);
    expect(longNotes("x.tsx", '<p role="alert">The server refused this value because it is below the lowest the mesher accepts.</p>')).toEqual([]);
    expect(longNotes("x.tsx", 'const m = new Map<string, number>(); if (a > b && c < d) return x;')).toEqual([]);
    expect(longNotes("x.tsx", 'const f = (a: number) => a > 1 ? "one two three four five six seven eight nine ten" : "";')).toEqual([]);
    // The limit is the ninth word.
    expect(longNotes("x.tsx", "<p>one two three four five six seven eight</p>")).toEqual([]);
    expect(longNotes("x.tsx", "<p>one two three four five six seven eight nine</p>")).toHaveLength(1);
  });
});
