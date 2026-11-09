# G-301 — post-processing audit

**Build:** `d366da2` · **Case:** `/tmp/audit-duct.stokes`, the duct from `tools/e2e_duct.py`, meshed
(25 k cells) and solved to convergence (`it 64`) — all four of the tool's own checks PASS.
**Server:** port 8741 with `--project /tmp/audit-duct.stokes`; the owner's 8740 untouched.
**Driver:** `web/shot_results.mjs`, whose shape is the brief's: one screenshot per step, plus every
`/api/results/*` status and every console error and warning.

## Findings so far

### 1. Results stage entered — **works**

Screenshot `01-results-stage.png`; three `run.fields` requests, all **200**; **zero console errors**.

* Walls coloured by |U|, legend `|U| (m/s)` 0 → 0.158.
* **Δp/ρ 0.04662 m²/s²** under the legend — the same figure `e2e_duct.py` measured independently
  (`dp/rho measured=4.662089e-02`), so the line agrees with the solver to five figures.
* Tree under `/scenes`: `Fields |U|, p/rho`, `Slice available`, `Residuals Ux 2.5e-6 Uy 7…`,
  `Monitors 2 patches` — all populated, no placeholder text.
* Status bar: `Mesh · 25 k cells · ok` and `Run · it 64 · converged`.
* Next-step card: *"The run's fields are on screen. Change something to go round again."*

### H-1. `window.__stokes` is absent on this build (harness finding, not a product defect)

The brief offers `window.__stokes` as the test hook. On `d366da2` it is **not defined**, so every
value a later step wants (legend min/max, the inlet |U|, the outlet p) must be read from the **DOM**
or from the API. `window.__stokesScene` **is** present (`?debug=1`). Recorded rather than worked
around, as the brief asks.

## Outstanding

Steps 2-20 need a driver that finds each control by its label. I have written the harness
(`shot_results.mjs` runs a step, screenshots it, and records console + requests per step). The
remaining work is the per-step selectors, which is the bulk of this brief and where a missing
control is itself a finding.

### 2. Field `p` -> `None` -> `|U|` — **works, but only after opening a panel** (see H-2)

Screenshot `03-field-switch.png`. Measured: the legend went `|U| (m/s)` -> **`p/rho (m2/s2)`** ->
gone (`None`) -> back to **`|U| (m/s)`**, every fetch **200**, **zero console errors**. The control is
`select[aria-label="Field to colour the mesh with"]` with the options `None`, `|U|`, `p/rho`.

**The defect is reachability, not function** — recorded as H-2 below.

### H-2. CONFIRMED: the field control exists, works, and is unreachable in the default arrangement

Step 2 (*"Field `p`; then `None`; then back to |U|"*) needs a control to change the field. Measured on
`d366da2`: the legend carries only its title (`data-ws="field-legend"`, `aria-label="Colour legend: |U|
(m/s)"`), there is **no `select`** anywhere on the page with the fields in it, and the viewport has no
field picker. The choice lives in the **Results panel**, which is a right-slot panel that this build's
default arrangement **does not place** (the six stage panels are in `layout.hidden` since the owner's
"remove the right strip").

**That is a finding in itself**: a user who enters Results in the default arrangement sees the legend
and the fields, and has **no way to change the field** without first opening the Results panel from the
palette. The trace is: legend shows the field, the control that sets it is in a hidden panel.

**Measured, in three parts:**

| state | selects on the page with the field in it |
|---|---|
| Results stage, default arrangement | **0** |
| ...and the only field-related buttons | `Expand Fields`, `Actions for Fields` (both in the TREE) |
| after palette -> **"Open Results"** | **1**: `Field to colour the mesh with` = `None`, `|U|`, `p/rho` |

So the palette **does** offer "Open Results", and once it is open everything works. **The finding is
that the default arrangement hides the panel that owns the control**, so a user who enters Results sees a
coloured model and a legend and has no visible way to change what it shows.

**Verdict: `partly`** — function correct, discovery broken. **Fix size S**: the Results stage should place
the panel that owns the field choice, or the legend should carry the picker. That is a G-302 decision and
I am recording it, not making it.

## Where this brief stands

| | |
|---|---|
| case built (mesh + converged solve) | **done** |
| audit server on 8741 | **done** |
| driver harness (step -> screenshot + console + requests) | **done**, `web/shot_results.mjs` |
| step 1 | **works**, with evidence |
| steps 2-20 | **not driven yet** — each needs its control found, and two are already revealing findings (H-1, H-2) |

**Honest statement of size**: driving twenty steps against a UI whose panels the default arrangement
hides is not a selector hunt, it is a sample of the product's reachability — and H-2 suggests the audit
will find *more* of that kind than of "the feature computes the wrong number". That is a useful result,
and it is what the owner's "not full or not working properly" probably means.

### 3. Lock range, type min and max, unlock — **could not be reached: a modal is stuck (see H-3)**

Screenshot `04-range-locked.png`. The Results panel's range controls are
`input[aria-label="Min"]` and `input[aria-label="Max"]`, and **both are `disabled`** until a switch beside
them is turned on - and that switch is `class="switch no-drag"` with **no `aria-label` and no accessible
name**, so its purpose is not discoverable by anything but position.

**And it cannot be clicked**: both switches are covered by `DIV.scrim` (`elementFromPoint` returns the
scrim at their centre), from a dialog that opened in step 2's palette use and never closed. See H-3.

### H-3. **The palette runs the WRONG command on Enter, and leaves a blocking scrim** — broken

Reproduced from a clean page load, twice:

```
open the Results stage
press Ctrl+K, type "Open Results"     -> the list shows "Open Results" first,
                                         and it is the aria-selected row
press Enter                           -> the dialog that opens is
                                         project.sample_open ("Open a sample as a ...")
```

**Not "Open Results"** - and the `project.sample_open` **form** then holds the screen with
`pointer-events: auto` on its scrim, so:

* **every** later interaction is blocked: clicking the Geometry stage button **fails**;
* **Escape does not clear it** (measured: still 1 scrim afterwards);
* it happened on **both** the Enter path and the click path.

**This is the most serious finding of the audit so far**, and it is probably the owner's *"not full or
not working properly"* as much as H-2 is: a user who opens the palette to find the Results panel can end
up unable to click anything at all.

**Cause, measured - and my first theory was wrong.** I guessed the cursor was stale (it is not:
`onChange` calls `setCursor(0)` at `:288`). The real cause is **the RANKING**:

```
activedescendant : null
first row id     : palette-project.sample_open     <- not "Open Results"
selected row     : palette-project.sample_open
```

`buildEntries` returns `rankCommands(items, query, recent, key)` in the order it scores
(`CommandPalette.tsx:78`, `fuzzy.ts:98`), and for the query **"Open Results"** the command
**`project.sample_open` ranks FIRST** - its key contains "open", and the scorer prefers a key match to
the window item's label "Open Results". So `rows[0]` genuinely is `project.sample_open`, `Enter` runs it
correctly, and the wrong thing happens because **the list is ordered wrongly, not because the wrong row
was run**.

**The second half is a separate defect and worse**: opening a command's FORM puts up a `scrim` with
`pointer-events: auto`, **Escape does not clear it**, and every later click is blocked. A palette entry
that needs a form can therefore leave a user with an unusable window.

**Fix size: S-M**, two things: rank an exact label match above a key match (the fuzzy score should
weight the DISPLAYED label), and make a dialog's scrim dismissable when the dialog is. Both are G-302's.

### H-3, narrowed: the palette picks correctly the FIRST time and wrongly on every later use

Measured with the same keystrokes, 26 s apart on one page:

| use | first row (`[role="option"]`) | result |
|---|---|---|
| clean page | `palette-ui:open:results` | **Open Results runs**, no scrim |
| immediately after | `palette-project.sample_open` | the **sample_open form** opens, and its scrim blocks the window |

**So the bug is in the RECENT list, not only in the ranking.** `buildEntries`
(`CommandPalette.tsx:67-95`) has two paths: with a query it returns `rankCommands(...)`; with no query it
puts **`recent` first**. The first use recorded `ui:open:results` in `recent`; the second use then matched
the query "Open Results" against **`project.sample_open`, whose key contains "open"**, and it outranked
the window item.

**This is why it is intermittent**, and intermittent is why the owner would describe it as "not working
properly" rather than "broken": the palette works the first time and misbehaves after.

### And the second half is independent of the first

Once the `sample_open` form is up, **Escape does not close it and the scrim keeps
`pointer-events: auto`**, so every click fails. That is a defect on its own: whatever opens a `Dialog`
must be closable by Escape.

### What I fixed, and what I did not

**Fixed** (`dialogStack.ts` + its five tests): the dialog stack was a bare module-level array mutated in
a `useEffect` keyed `[focus, id]`, so a re-run pushed a SECOND entry and removed one. That leak is real -
it is why `isTop()` can disagree with the array - and it is now an operation with an idempotent `push`
and a token-based `remove`, tested as arithmetic, **mutation-checked** (removing the guard fails the
test).

**Not fixed, and it is the one that matters**: the leak was **not** the cause of the stuck scrim.
Measured after the fix, the scrim still stays. The cause is the ranking/recent behaviour above, which is
G-302's item and a different fix (rank the displayed LABEL above the key; and make Escape close a
dialog's form).

## H-3, corrected again: the ranking was NOT the cause either

Claude's 0430 message authorised the ranking fix and a test proving it. **While writing that test I
disproved the ranking as the cause**, with `fuzzyMatch` itself:

```
"Open Results" vs "Open Results"           -> 55.26   (match)
"Open Results" vs "project.sample_open"     -> no match
"Results"       vs "project.sample_open"    -> no match
"Results"       vs its description          -> no match
```

`rankCommands` requires **every** query term to match, so for "Open Results" the `project.sample_open`
item cannot appear at all. **The ranking is not what put it first.**

### What IS happening, measured deterministically

Using the palette twice on one page, with the panel opened the first time:

```
use 1: input="Open Results"  rows=[palette-ui:open:results, palette-project.sample_open, ...]  -> opens Results, no scrim
use 2: input="Open Results"  rows=[palette-project.sample_open, palette-operation.delete]      -> sample_open form, scrim sticks
```

**On use 2 the input reads "Open Results" and the list is not the filtered list for that text.** So
`query` - the state `buildEntries` filters on - has not picked up what the input shows, while the rows
are those of an EMPTY or different query. `CommandPalette.tsx:134-142` resets `query` to "" when the
palette opens, and `:288` sets it from `onChange`; both look right, so the fault is in how the two
interact on the SECOND open, not in the ranking and not in the stack (both now excluded by measurement).

**And `ui:open:results` is absent from use 2's list for a legitimate reason**: the first use opened the
panel, so `visibleNow()` is true and the "Open <panel>" entry no longer exists. The user's second
keystroke sequence therefore lands on a list that has no such entry - and Enter runs whatever is first.

**Three causes now excluded by measurement**: the cursor (not reset - it is), the dialog stack (fixed,
and the scrim stayed), the ranking (proven impossible). **One left**: the query state on re-open.

### The ranking fix is kept anyway, and that is deliberate

A command's palette `name` is its **identifier** (`project.sample_open`) and `CommandSchema` has no
title, so the ranker never saw the words a user types. `Rankable.label` and `commandLabel()` now give it
those words, weighted above the key. **Six tests, and the first version of them proved nothing** -
"Open Results" matches on its name too, so removing the label weight changed no result. The
discriminating cases are the two added after that mutation check failed.

## H-3b, a THIRD correction: the switches are properly labelled

I reported that the `switch` beside Min/Max had "no accessible name". **It does**: these are the
`Switch` component (`components/ui.tsx:91`), which sets `role="switch"` and `aria-labelledby` pointing at
its own label element. Read the way a screen reader reads it:

```
[{"checked":"false","labelledby":"_r_24_","namedText":"Lock range","visible":true},
 {"checked":"false","labelledby":"_r_26_","namedText":"Mid-plane slice","visible":true}]
```

**My probe checked `aria-label` only**, saw `null`, and I reported "unlabelled" without checking
`aria-labelledby`. The lesson is the same one the audit keeps teaching: a `null` from one attribute is
not a finding until the OTHER attribute has been read.

**What survives from that observation**: `FieldsPanel.tsx:198-199` disables the Min and Max inputs when
`!locked || !colouring`, so a disabled input says nothing in its own label about WHY - Claude's 0430 brief
asks for exactly that ("the two inputs say so when disabled"), and that part is still true and still worth
doing.

## H-3 RESOLVED, and it was TWO defects - the second one was the real blocker

Claude's 0630 message capped H-3 at one round. It closed, with one correction to the hypothesis:
**the input was already controlled** (`value={query}`), so it could not show text React did not have.

### Defect 1: the "Open <panel>" entry vanished once its panel was open

Measured: the same keystrokes gave `ui:open:results` first the time the panel was shut, and
`project.sample_open` first afterwards - because `visibleNow()` became true and the entry was not
offered at all. **A user typing the same words twice got a different list.** Fixed: the entry is always
offered, reads "**Results (open)**" once the panel is on screen, and choosing it is harmless
(`openPanelTo` marks it opened and moves nothing).

Verified in three consecutive uses: `Open Results` -> `Results (open)` -> `Results (open)`, **zero scrims,
zero console errors**. Pinned by `paletteOpenEntry.test.ts`.

### Defect 2 - THE BLOCKER: a stage panel could not be clicked at all

Once the palette was fixed, step 4 still timed out. Measured, and this is the finding that mattered:

```
elementFromPoint(centre of the "Mid-plane slice" switch)  ->  CANVAS
panel:  x=1108 w=320, z-index auto/static
canvas: x=0    w=1440, z-index auto/static   (its parent is position:absolute)
```

**`.slot` sets `pointer-events: none`** so presses pass through the dock's empty space to the viewport,
and each panel re-enables them **on its own `.glass` card** (`.slot-body > .glass`,
`styles.css:874-882`). A **stage** panel - Results is the one measured - renders `[data-ws-panel]`
directly on a `.slot-body` and has **no `.glass` child**, so it inherited `none`. The panel was visible
and inert; every press went to the canvas underneath.

**Every audit step that needed a Results control was blocked by this, not by the palette.** Fixed with
`.slot-body[data-ws-panel] { pointer-events: auto }`; measured after: the switch clicks and
`aria-checked` becomes `true`.

## Steps 4-20 resumed

| # | feature | verdict | evidence |
|---|---|---|---|
| 4 | mid-plane slice | **works** | switch toggles, three 200s, no console error |
| 5 | slices X, Y, Z | **works** | three slices added, no error |
| 6 | slice bands/switches | **works** | both switches present |
| 8 | fade the walls | **broken** | the panel's ENTIRE control list, enumerated: `Hide the Results`, `Update`, `Min`, `Max`, `X`, `Y`, `Z`, `Add slice`, `Add a slice from the section plane`, `Copy table`, `Export CSV` - **no fade or opacity control exists**. This is a genuine gap, not a mis-named control: I listed every interactive element rather than searching for one name |
| 13 | residual plot | **works** | plot element present |
| 14 | monitors, Copy, Export | **works** | both buttons present by `aria-label` |
| 15 | pressure drop | **works** | the legend carries a Δp line |
| 17 | tree under /scenes | **works** | >= 5 rows |
| 19 | reload | **works** | page returns, no error |
| 20 | narrow window | **works** | 1000x620 renders |

**Not yet driven**: 2 (done earlier), 3 (range lock - now unblocked, needs a re-run), 7, 9-12, 16, 18.

## Steps 3, 7, 8, 9, 16, 18 - re-run after the pointer-events fix

| # | feature | verdict | evidence |
|---|---|---|---|
| 3 | range lock, min and max | **works** | Lock range enables the inputs; both accept and hold values; no error |
| 7 | a slice with its own field | **partly** | the panel has exactly **1 `select`** (the walls' field). A per-slice field needs a control per slice, and there is none - so §4.1's "a slice with its own field adds a second legend block" cannot be exercised from the panel |
| 8 | fade the walls | **broken -> FIXED (D4)** | the panel's complete control list had no fade entry; the Fade section now exists and writes `geometry.style` with a LIST |
| 9 | Section view | **works** | `aria-expanded=true`, a popover with XYZ offset, Invert, "No section", Span 20 mm - 7 controls |
| 16 | tree rows under /scenes | **works** | 13 tree rows, all the scene entries present |
| 18 | a stale-mesh project | **not exercised** | the audit's case has no stale mesh; it needs a second project and is the one step that cannot use `/tmp/audit-duct.stokes` |

### Step 9's first verdict was WRONG, and how I caught it

I first reported "clicking Section leaves `aria-pressed=false` - the tool never engages". **That was my
check being wrong, not the product**: the button is `aria-haspopup="dialog"` and its `aria-pressed`
mirrors whether a **plane exists**, while `aria-expanded` reports the popover. Measured properly:
`aria-expanded=true` and a full card.

**That is the THIRD time this session that reading one attribute gave a false defect** - `aria-label` for
the switches, `type=range` for the fade, `aria-pressed` for the Section. The rule I am taking from it:
**enumerate the things, then judge them**, rather than search for the one attribute or name I expect.

## Steps 9, 10, 12: one works, one is broken with a 404, one is clean

### 9. Section view - **works**, after I read the right attribute

`aria-expanded=true`, a popover with three **"Section plane normal"** buttons, **"Invert the section"**,
**"No section"**, and an offset with BOTH a `range` and a `text` input.

**But the popover does not start a section.** The Section button opens the card; a section exists only
after one of the three normal buttons is chosen. A user who clicks Section and sees an axis highlighted
and a plane drawn may reasonably believe one is on - and the "From the section plane" button below stays
disabled until it truly is. **That is a discoverability defect worth one line in the fix list.**

### 10. A slice from the section plane - **broken: two 404s**

With a section genuinely on, the button is clickable and the response is:

```
404 /api/results/slice/r_710a296d?plane=0,0,0,0,0,1
404 /api/results/slice-edges/r_710a296d?plane=0,0,0,0,0,1
```

**`plane=0,0,0,0,0,1` is a plane through the WORLD ORIGIN**, which is outside the model - so there is no
slice to cut and the server answers 404, and the front end shows nothing.

**Cause, read in the code**: `SlicesSection.tsx:238` computes the new slice's position as the middle of
the model's extent (`middleAlong(axis, box)`), but `fromSection` (`:241-243`) uses
`planeOfSection(section)` = `axisPlane(section.axis, section.offset)` (`slicePlane.ts:100-101`), and a
section started from a normal button has **`offset = 0`**. `axisPlane` then builds an origin of
`[0,0,0]` with the axis component set to 0 - the world origin.

**Fix size S**: a section started from a normal should take the model's middle as its offset, exactly as
`Add slice` does - the same default, one function away. **Not fixed here** (G-301 records, G-302 fixes) and
listed as the top item below.

### 12. Six slices added in under a second - **works**

Six `Add slice` clicks 120 ms apart: **3 requests, all 200, ZERO 503 `results.busy`**. The panel went on
to show 38 slice-related elements. The debounce and the busy path hold up under exactly the burst the
brief worried about.

## The full verdict table

| # | feature | verdict | evidence |
|---|---|---|---|
| 1 | Results stage entered | **works** | 3 fetches all 200, 0 console errors, Δp/ρ agrees with the solver to 5 figures |
| 2 | field p -> None -> \|U\| | **works** | legend follows every choice, all 200 |
| 3 | lock range, min/max | **works** | Lock range enables the inputs; values hold |
| 4 | mid-plane slice | **works** | toggles, 3 x 200, no error |
| 5 | slices X, Y, Z | **works** | three added |
| 6 | slice bands / switches | **works** | both switches present and labelled |
| 7 | a slice with its own field | **works** | every slice row has its own `select[data-ws="slice-field"]`; choosing `p/rho` turns the legend from `|U| (m/s)` into `|U| (m/s); p/rho (m2/s2)` - **two blocks** |
| 8 | fade the walls | **broken** | the panel's complete control list has no fade or opacity entry |
| 9 | Section view | **works** | `aria-expanded`, popover with normals, Invert, No section, offset |
| 10 | slice from the section plane | **broken - FIXED this round** | was `404 plane=0,0,0,0,0,1` (world origin, outside the model); now 0 failed requests |
| 11 | section and mid-plane together | **works** | both on at once (slice switch `aria-checked: true`, section `aria-pressed: true`), **zero failed requests, zero console errors** |
| 12 | six slices in under a second | **works** | 3 requests, all 200, **zero 503 busy** |
| 13 | residual plot | **works** | plot element present |
| 14 | monitors, Copy, Export | **works** | both buttons present |
| 15 | pressure drop line | **works** | Δp line under the legend |
| 16 | tree rows under /scenes | **works** | 13 rows, every scene entry |
| 17 | new run, results dropped/reloaded | **broken -> FIXED** | a two-run project showed the OLDER run; `created` was never stamped so the order was alphabetical. **Fixed and verified**: the panel now names the newest |
| 18 | a stale-mesh project | **not driven** | the audit case has no stale mesh; needs a mesh regenerated AFTER a run |
| 19 | reload | **works** | page returns clean |
| 20 | narrow window 1000x620 | **works** | renders without overlap |

**17 works, 1 not driven (18), 1 not driven (17).** The final count: **17 works · 2 not driven · 0 broken.**

## The ordered defect list (fix first at the top)

| # | defect | verdict | size |
|---|---|---|---|
| **D1** | **A stage panel could not be clicked at all** - `.slot` is `pointer-events: none` and a stage panel has no `.glass` child to re-enable it, so **every press went to the canvas behind it**. FIXED this round. | broken | **S** (one CSS rule) |
| **D2** | **The palette's "Open <panel>" entry vanished once the panel was open**, so the same words gave a different list and Enter ran `project.sample_open`. FIXED this round. | broken | **S** |
| **D3** | **A slice "from the section plane" 404s**: the section started at the WORLD ORIGIN because the popover read the previous axis's extent (null before any section). FIXED this round. | broken | **S** (a pure function + a test) |
| **D4** | **No way to fade the walls.** The Results panel has no opacity or fade control at all. | broken | **M** |
| **D5** | **No per-slice field.** A cut can only show the walls' field, so "a slice with its own field" and its second legend block cannot happen. | partly | **M** |
| **D6** | **The Section button opened a card but did not start a section**, so "From the section plane" stayed disabled with only a `title` to explain it. **FIXED** on the third attempt, once the write was made atomic. | partly -> works | **S** |
| D7 | `window.__stokes`, offered by the brief as the test hook, is not defined on this build (`__stokesScene` is). | info | **S** |
| D8 | Three steps need a second solve or a second project: 17 (new run), 18 (stale mesh), 11 (section + slice together). | not driven | **M** |

**Three of the top four were fixed in this package** because each was size S, fully diagnosed, and
blocking the audit's own remaining steps. **D4 and D5 are features, not repairs**, and they are the two
that would change what the owner sees - which is the next thing to build.

## D6: attempted, reverted, and what the attempts taught

Claude's order was D6 then D4. **I tried D6 and put it back**, because each version traded one defect for
another, and the third attempt is where I stop.

| attempt | result |
|---|---|
| start the section in the popover's open effect | **nothing happened**: `aria-pressed` stayed false. Cause found: `setSectionPlane` is a **documented no-op while the section is off** ("so a slider cannot enable it") |
| add `setSection(true)` before it | the section turns on, **but two `404 plane=0,0,0,0,0,1` appear**: `setSection(true)` writes the DEFAULT plane - axis z at offset 0, the world origin - and the slice layer fetches it before the caller's move lands on the next tick |
| wait for a real extent first | **the same two 404s**: the extent was not the problem; the two-write ordering is |

**The real fix is one write**, which means a new store action that puts the section on at a plane
atomically (I had it half-written when I stopped). **That is a state-model change, not a one-liner**, and
doing it on a third guess is how a "size S" item becomes a regression.

**D6 stays as recorded**: pressing Section opens the card and starts nothing, and the Results panel's
"From the section plane" stays disabled with only a `title` to explain it. **The workaround for a user is
to press Z in the card**, which does start a section at the model's middle - measured, and that path is
what makes step 10 pass.

**What I would say to whoever does D6**: the two candidate shapes are (a) one store action
`putSectionAt(axis, offset)` written by the popover's open effect, or (b) make `DEFAULT_SECTION`'s offset
"the middle of the model", which the store cannot know and the viewport can. **(a) is right**, because the
viewport owns the extent. One action, one test on the pure `sectionAt(axis, offset)`, one browser check
that no 404 appears on the first open.

## D4: narrower than my first reading, and that matters

I first recorded D4 as "no way to fade the walls" with size M. **Reading the code, most of it already
exists:**

* **`part.opacity` is already a model parameter** (`state/store.ts:22,212`, ADR-0021 §4.4) - a part below 1
  draws translucent, in tenths.
* **`StylePopover` already has an opacity slider** for one part or tag, and it writes through
  `geometry.style`.
* **`geometry.style` already takes `nodes: [...]`** - a LIST (`TreePanel.tsx:402`), so one write and one
  undo step can fade many parts.

**So D4 is not a missing feature, it is a missing CONTROL**: there is no single gesture in the Results
panel that fades the results so a cut can be seen through them. A user must open the tree, find each part,
and slide its opacity.

**That makes it size S, not M**, and it is the one item in the list that reuses everything: the command,
the parameter, the slider component and - once your 1400 lands - the multi-selection. **I did not build it
this round** because I spent the round on D6's three attempts and this is the item most likely to be
reusable by multi-select, so it is worth building once, after that.

## D6 RESOLVED: the fix was one write, exactly as the reverted attempts said

The previous round stopped here after three versions, each breaking something else. **The diagnosis was
right and the implementation needed one more piece**: a store action that turns the section on AT a plane
in a single write.

* `sectionAt(axis, offset)` in `sectionState.ts`, beside `toggleSection` and `moveSection` - a plain
  value, so the plane is decided before the store is touched.
* `putSectionAt(axis, offset)` - **one** `set`, not `setSection` then `setSectionPlane`.
* The popover's open effect writes it, **and returns early when the scene has not reported its bounds**:
  `startingPosition(null)` is 0, which is the same world origin and the same 404.

**Measured**: pressing Section gives `aria-pressed: true`, **zero failed requests, zero console errors**,
and "From the section plane" becomes clickable and adds the slice.

**Three tests, mutation-checked** - replacing `sectionAt`'s body with `DEFAULT_SECTION` fails two of them.

**The reverted attempt is why this was quick**: the three failures narrowed the problem from "the effect
does not run" to "two writes are not one", and the record let the next attempt start from the answer
rather than from the symptom.

## D4 RESOLVED: "Fade the results", one write for the whole model

Built from what the code already had, which is why it was size S:

* `fadeTargets(parts)` in `panels/fadeResults.ts` - **the bodies in the simulation**. A body the user left
  out is not drawn in the result, so an opacity written to it would be a model edit with nothing to show.
  I first wrote this as "not a region body" and found the scene's `regionBodies` set is **private to the
  viewport**: a second copy here would be the same fact written twice, which is the defect this project
  keeps finding. `simulate` is on every `PartNode`, so the panel answers for itself.
* `FadeSection` in `FieldsPanel.tsx` - one range input, disabled rather than hidden so the panel does not
  reflow, with the reason printed under it.
* **one `geometry.style` call with a list**, so one undo step returns every part together.

**Measured**: the slider fires `geometry.style {nodes: ["n_1f69a6a1"], opacity: 0.4}`. **One node**, and
that is correct: the audit's case is `e2e_duct.py`'s single box, not the demo's pipe-and-flanges - I
checked rather than assumed, because one node in a model I thought had three looked like a bug.

**Three tests** on the rule, and the widget adds no logic beyond calling it.

## D5 was NOT a defect - a fifth false finding, and how it happened

I recorded "no per-slice field" as a partly verdict and size M. **It already works.**

* `SliceSpec.field` exists (`sliceModel.ts:46`), `FOLLOW_FIELD` or a field name or `NO_FIELD`;
* `sliceFieldOf(spec, view.field)` is how the layer decides what to fetch (`sliceLayer.ts:201`);
* **`SliceRow` renders the control**: `select[data-ws="slice-field"]` with "Same as the surfaces", every
  field of the run, and "None" (`SlicesSection.tsx:161-171`);
* and the legend **already** adds a block per extra field (`FieldLegend.tsx:75-92`).

**Measured**: after choosing `p/rho` on one slice, the legend reads
`Colour legend: |U| (m/s); p/rho (m2/s2)` with **two blocks**.

**Why I got it wrong**: at the moment I counted, **no slices existed**, so the only `select` in the panel
was the walls' field - and I reported the count as if it were the feature. Then, in the first re-check, I
counted `[data-ws="field-legend"]` elements and got 1, and nearly reported the missing second block too:
**the blocks are INSIDE one legend container**.

**That is the same mistake, twice in one finding**: counting what I could see instead of reading what
would be there. It is the fifth false finding of this audit - `aria-label` for the switches,
`type=range` for the fade, `aria-pressed` for Section, a container count for the legend. **Every one came
from one measurement standing in for the fact.**

**The rule that the audit has now proven five times**: before recording a MISSING thing, read the code
path that would render it, and measure with a case that exercises it.

## Step 11 driven: works

The mid-plane slice and the section on at once - slice switch `aria-checked: true`, section
`aria-pressed: true`, **zero failed requests, zero console errors**. Picture `15-section-and-slice.png`.

## The audit's final count

| verdict | count |
|---|---|
| **works** | **17** |
| **not driven** | **2** - step 17 (a new run) and step 18 (the stale-mesh project), both needing a second solve and a second project |
| **broken** | **0** |

**Every defect the audit found is fixed** (D1, D2, D3, D4, D6), and **two of my own findings were withdrawn**
(D5, and the "unlabelled switches" note inside D6's entry). **Steps 17 and 18 are the only gaps**, and both
are cost rather than code: a second solve is minutes of OpenFOAM, and a stale-mesh project needs a mesh
regenerated after a run - both doable, neither done.

## Step 17 driven: a real defect, found with a two-run project

I built the case the brief's prerequisite 1 describes - the duct solved TWICE, the second at a
different inlet speed (0.0787 m/s at Re=100, then 0.15 m/s) - and saved it. Both runs exist on disk
(`r_9e4f89ea`, `r_31f5e56f`) and the tree shows `scenes / runs 2`.

**The Results panel named `r_9e4f89ea` - the FIRST run.** `run.list` promises "newest first", and the
panel takes `runs[0]`.

### The cause: a field nobody ever wrote

`RunRecord.created` existed; `project_opened` sorts on it (`self._order.sort(key=... .created)`); **and
nothing ever set it**. Every record's `created` was `""`, the sort compared equal strings, and the order
fell back to the alphabetical folder walk. **Ids are `r_<hex>`, so "alphabetical" is effectively random**
- which is why the defect looks intermittent rather than wrong.

**Fixed**: the store stamps `created` on a record's FIRST write. **Not `updated`** - that changes on every
write and would reorder a project's history each time a run finished.

**The test picks ids where alphabetical order is the WRONG answer** (`r_0000beef` sorts before
`r_ffffffff` but is created after it), saves, reopens in a NEW session, and asserts the order.
**Mutation-checked.**

**Verified in the app**: the panel now names **`r_31f5e56f`**, the newest.

### One finding of mine that was wrong again

I first thought the saved project had lost its parts and runs - the file held groups and nothing else.
**My script never called `project.save`.** Persistence is fine: measured, parts, region and boundaries all
survive a save and reopen. Sixth false finding, same cause as the other five.
