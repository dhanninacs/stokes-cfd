/**
 * The elbow energy project, end to end through the GUI (ADR-0026 tree layout, ADR-0027 energy, the
 * iso route of ADR-0026 amendment 1).
 *
 * It starts `python -m stokes.server` on the project named by `STOKES_ELBOW_ENERGY_PROJECT` (a
 * finished run with the energy equation on), opens the page with the token URL the server prints,
 * and checks what a user sees:
 *
 *   a. the tree shows Capping under Mesh and Surfaces ▸ Slice under Results;
 *   b. the residuals name the energy equation and draw the run's history as a chart, T is among the
 *      result fields and p is labelled in Pa (not p/rho);
 *   c. with T selected, an iso value in range (300 K) is served by the iso route and one out of range
 *      (500 K) is refused with the out-of-range message;
 *   e. the Results panel's Iso-surface control cuts T = 300 K and shows `ready` with its triangle count;
 *   g. that surface is drawn in the viewport (its line `iso T = 300 K, N triangles`), the switch hides
 *      and shows it, and Hide takes it out;
 *   f. opening the Capping row under Mesh shows the capping card, not the Mesh panel;
 *   d. the page logs no console error other than the known benign ones below.
 *
 * (c) asks the route directly with the page's token; (e) goes through the control a user presses.
 *
 * The browser never changes the model (ADR-0013): every check reads. Paths come from the environment
 * only; the token and the project path are never printed.
 *
 * Environment: `STOKES_ELBOW_ENERGY_PROJECT` (required, else the test skips), `STOKES_PYTHON` (the
 * interpreter with Stokes installed; default `python`), `STOKES_E2E_SHOTS` (where screenshots go;
 * default the test's output folder). The server inherits the environment, so `STOKES_AGENT_HOME`
 * must be the agent home the run was made with: the iso cut runs on that agent's job.
 */
import { spawn, type ChildProcess } from "node:child_process";
import { existsSync, mkdirSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { createInterface } from "node:readline";
import { fileURLToPath } from "node:url";
import { expect, test, type Page } from "@playwright/test";

const PROJECT = process.env.STOKES_ELBOW_ENERGY_PROJECT ?? "";
const PYTHON = process.env.STOKES_PYTHON ?? "python";
const HERE = dirname(fileURLToPath(import.meta.url));
const REPO = resolve(HERE, "..", "..");

/** The iso values: T in the elbow run spans about 293 to 353 K. */
const ISO_IN_RANGE = "300 [K]";
const ISO_OUT_OF_RANGE = "500 [K]";

/**
 * Console errors that are not defects, each with the reason. Empty today: the run is clean. A pattern
 * belongs here only with a sentence saying why the message is expected.
 */
const BENIGN_CONSOLE_ERRORS: readonly { pattern: RegExp; why: string }[] = [
  // Software GL (swiftshader) reports pipeline stalls through the console on some drivers.
  { pattern: /GL Driver Message/, why: "software GL performance notice, not an application error" },
];

/** Starts the server on an OS-chosen port and resolves with the URL it prints (token included). */
function startServer(project: string): Promise<{ child: ChildProcess; url: string }> {
  const child = spawn(PYTHON, ["-m", "stokes.server", "--port", "0", "--project", project], {
    cwd: REPO,
    stdio: ["ignore", "pipe", "pipe"],
    windowsHide: true,
  });
  const stderr: string[] = [];
  child.stderr?.on("data", (chunk: Buffer) => {
    stderr.push(chunk.toString());
    if (stderr.length > 200) stderr.shift();
  });
  return new Promise((resolveUrl, reject) => {
    const timer = setTimeout(() => reject(new Error(`the server printed no URL in 90 s:\n${stderr.join("").slice(-2000)}`)), 90_000);
    child.once("exit", (code) => {
      clearTimeout(timer);
      reject(new Error(`the server exited with ${code}:\n${stderr.join("").slice(-2000)}`));
    });
    const lines = createInterface({ input: child.stdout! });
    lines.on("line", (line) => {
      if (/^https?:\/\/\S+\?token=\S+$/.test(line.trim())) {
        clearTimeout(timer);
        resolveUrl({ child, url: line.trim() });
      }
    });
  });
}

/** The tree's rows in order: level, row kind and text. */
async function treeRows(page: Page): Promise<{ level: number; row: string; text: string }[]> {
  return page.$$eval("[role=treeitem]", (els) =>
    els.map((e) => ({ level: Number(e.getAttribute("aria-level")), row: e.getAttribute("data-row") ?? "", text: (e.textContent ?? "").trim() })),
  );
}

/** Opens the tree row `name` at `level` when it is closed. */
async function expandRow(page: Page, name: string, level: number): Promise<void> {
  const row = page.locator(`[role=treeitem][aria-level="${level}"]`).filter({ hasText: new RegExp(`^${name}`) }).first();
  await expect(row, `tree row ${name} at level ${level}`).toBeVisible();
  if ((await row.getAttribute("aria-expanded")) === "false") await row.locator(`[aria-label="Expand ${name}"]`).click();
  await expect(row).toHaveAttribute("aria-expanded", "true");
}

/**
 * The Contours row of one field. The row's text runs the name into its range (`T293 to 353 K`), so the
 * name is matched as a prefix not followed by another name character, not with `\b`.
 */
function fieldRow(page: Page, name: string) {
  return page.locator('[role=treeitem][data-row="field"]').filter({ hasText: new RegExp(`^${name}(?![A-Za-z_])`) });
}

/** The rows under the first level-`level` row named `name`, up to the next row at that level. */
function childrenOf(rows: { level: number; row: string; text: string }[], name: string, level: number) {
  const at = rows.findIndex((r) => r.level === level && r.text.startsWith(name));
  if (at < 0) return [];
  const end = rows.findIndex((r, i) => i > at && r.level <= level);
  return rows.slice(at + 1, end < 0 ? undefined : end);
}

test.describe("elbow energy project", () => {
  test.skip(PROJECT === "", "set STOKES_ELBOW_ENERGY_PROJECT to the elbow energy .stokes project to run this test");

  let server: ChildProcess | null = null;
  let url = "";

  test.beforeAll(async () => {
    test.skip(!existsSync(PROJECT), "STOKES_ELBOW_ENERGY_PROJECT does not name an existing file");
    test.skip(!existsSync(join(HERE, "..", "dist", "index.html")), "web/dist is missing: run npm run build first");
    ({ child: server, url } = await startServer(PROJECT));
  });

  test.afterAll(async () => {
    server?.kill();
    server = null;
  });

  test("tree layout, energy residuals, fields, iso and capping", async ({ page }, testInfo) => {
    const shots = process.env.STOKES_E2E_SHOTS ?? testInfo.outputPath();
    mkdirSync(shots, { recursive: true });
    const shot = (name: string) => page.screenshot({ path: join(shots, `elbow-${name}.png`) });

    const consoleErrors: string[] = [];
    page.on("console", (m) => {
      if (m.type() === "error") consoleErrors.push(m.text());
    });
    page.on("pageerror", (e) => consoleErrors.push(`pageerror: ${String(e)}`));
    let runId = "";
    const fieldRequests: string[] = [];
    page.on("request", (r) => {
      const m = /\/api\/results\/field\/([A-Za-z0-9_]+)\/[^?]+\?field=([A-Za-z0-9_]+)/.exec(r.url());
      if (m) {
        runId ||= m[1];
        fieldRequests.push(m[2]);
      }
    });

    await page.goto(url, { waitUntil: "load" });
    await expect(page.locator('[role=treeitem][aria-level="1"]').filter({ hasText: /^Mesh/ })).toBeVisible({ timeout: 60_000 });

    await test.step("read the run's results", async () => {
      await page.getByRole("button", { name: /Show results/ }).click();
      await expect.poll(() => runId, { message: "a field request names the run", timeout: 120_000 }).not.toBe("");
    });

    await test.step("a. Capping under Mesh; Surfaces ▸ Slice under Results", async () => {
      await expandRow(page, "Mesh", 1);
      await expandRow(page, "Results", 1);
      await expandRow(page, "Surfaces", 2);
      const rows = await treeRows(page);
      const mesh = childrenOf(rows, "Mesh", 1);
      const capping = mesh.find((r) => r.row === "mesh-capping");
      expect.soft(capping?.text ?? "", "Mesh has a Capping row").toMatch(/^Capping/);
      expect.soft(capping?.level, "the Capping row is a direct child of Mesh").toBe(2);
      const results = childrenOf(rows, "Results", 1);
      const surfaces = childrenOf(results, "Surfaces", 2);
      expect.soft(results.some((r) => r.level === 2 && r.text.startsWith("Surfaces")), "Results has Surfaces").toBe(true);
      expect.soft(surfaces.find((r) => r.row === "slice")?.text ?? "", "Surfaces has Slice").toMatch(/^Slice/);
      await shot("a-tree");
    });

    await test.step("b. residuals name Energy; T is a result field", async () => {
      await page.getByRole("tab", { name: "Residuals" }).click();
      // A run with its history draws the legend; one without lists the final residuals. Either names the field.
      // A reopened project's run has its history read back from the host: the chart, not "unavailable".
      await expect.soft(page.locator('[data-ws="residuals-live"]'), "the residual chart of the run's history").toBeVisible({ timeout: 60_000 });
      const residuals = page.locator('[aria-label="Final residuals"], [aria-label="Residual fields"]').first();
      await expect.soft(residuals, "the residuals panel").toContainText("Energy");
      await expandRow(page, "Graphics", 2);
      await expandRow(page, "Contours", 3);
      const t = fieldRow(page, "T");
      await expect.soft(t, "the T field row with its range in K").toContainText(/K$/);
      // The energy engine's p is a pressure in Pa, labelled p, not the kinematic p/rho.
      const p = fieldRow(page, "p");
      await expect.soft(p, "the p field row with its range in Pa").toContainText(/Pa$/);
      await expect.soft(p, "the p field row is not labelled p/rho").not.toContainText("p/rho");
      testInfo.annotations.push({ type: "p row", description: ((await p.first().textContent()) ?? "").trim() });
      // The legend's pressure drop is a drop in Pa: Δp, not Δp/ρ.
      const drop = page.locator('[data-ws="field-drop"]');
      if ((await drop.count()) > 0) {
        await expect.soft(drop, "the legend's pressure drop").not.toContainText("Δp/ρ");
        testInfo.annotations.push({ type: "legend drop", description: ((await drop.first().textContent()) ?? "").trim() });
      }
      await shot("b-residuals-fields");
    });

    await test.step("c. select T; iso 300 K is served, 500 K is refused", async () => {
      await fieldRow(page, "T").click();
      await expect.poll(() => fieldRequests.includes("T"), { message: "selecting T asks for the T field", timeout: 60_000 }).toBe(true);
      await shot("c-field-T");

      const token = new URL(url).searchParams.get("token") ?? "";
      const iso = (value: string) =>
        page.request.get(`${new URL(url).origin}/api/results/iso/${runId}`, { params: { field: "T", value, token }, failOnStatusCode: false, timeout: 240_000 });

      // 503 means the cut slots are busy (a cut is in progress): ask again until it resolves.
      let status = 0;
      let bytes = 0;
      let code = "";
      await expect
        .poll(
          async () => {
            const reply = await iso(ISO_IN_RANGE);
            status = reply.status();
            bytes = status === 200 ? (await reply.body()).length : 0;
            code = status === 200 ? "" : (((await reply.json().catch(() => ({}))) as { detail?: { code?: string } }).detail?.code ?? "");
            return status;
          },
          { message: "the iso route serves T = 300 K", timeout: 240_000, intervals: [2_000, 5_000] },
        )
        .not.toBe(503);
      // `results.host_unavailable` with a finished run usually means the agent cannot find the run's
      // job: the server must see the STOKES_AGENT_HOME the run was made with.
      const hint = code === "results.host_unavailable" ? " (is STOKES_AGENT_HOME the one the run was made with?)" : "";
      expect.soft(status, `iso 300 K status, code ${code || "none"}${hint}`).toBe(200);
      expect.soft(bytes, "iso 300 K payload bytes").toBeGreaterThan(0);
      testInfo.annotations.push({ type: "iso 300 K", description: `HTTP ${status}, ${bytes} bytes` });

      const refused = await iso(ISO_OUT_OF_RANGE);
      const detail = ((await refused.json().catch(() => ({}))) as { detail?: { code?: string; message?: string } }).detail ?? {};
      expect.soft(refused.status(), "iso 500 K status").toBe(422);
      expect.soft(detail.code, "iso 500 K code").toBe("results.iso_out_of_range");
      expect.soft(detail.message ?? "", "iso 500 K message").toMatch(/^500 K is outside the range of T, \S+ to \S+ K$/);
      testInfo.annotations.push({ type: "iso 500 K", description: `HTTP ${refused.status()}: ${detail.message ?? ""}` });
    });

    await test.step("e. the Iso-surface control: T = 300 K is ready with its triangle count", async () => {
      await fieldRow(page, "T").click();
      const iso = page.locator('[data-ws="results-iso"]');
      await iso.scrollIntoViewIfNeeded();
      await expect(iso, "the Iso-surface section").toBeVisible();
      await iso.locator('[data-ws="results-iso-field"]').selectOption("T");
      await expect.soft(iso.locator('[data-ws="results-iso-units"]'), "the value's unit").toHaveText("K");
      await iso.locator('[data-ws="results-iso-value"]').fill("300");
      await iso.getByRole("button", { name: "Show iso" }).click();
      const state = iso.locator('[data-ws="results-iso-state"]');
      await expect(state, "the iso state settles").toHaveAttribute("data-state", /^(ready|empty|refused)$/, { timeout: 240_000 });
      const line = ((await state.textContent()) ?? "").trim();
      testInfo.annotations.push({ type: "iso control 300 K", description: line });
      await expect.soft(state, "iso 300 K is ready").toHaveAttribute("data-state", "ready");
      await expect.soft(state, "iso 300 K state line").toHaveText(/^Ready: T = 300 K, [\d,]+ triangles$/);
      await iso.scrollIntoViewIfNeeded();
      await shot("e-iso-control");
    });

    await test.step("g. the iso-surface is drawn in the viewport, and Hide takes it out", async () => {
      const iso = page.locator('[data-ws="results-iso"]');
      // The viewport's line is written once the mesh is in the scene, with the scene's own triangle count.
      const status = page.getByTestId("viewport-iso-status");
      await expect(status, "the viewport says an iso mesh is drawn").toHaveText(/^iso T = 300 K, [\d,]+ triangles$/, { timeout: 120_000 });
      const line = ((await status.textContent()) ?? "").trim();
      const count = Number(/, ([\d,]+) triangles$/.exec(line)?.[1].replace(/,/g, "") ?? "0");
      expect.soft(count, "the drawn surface has triangles").toBeGreaterThan(0);
      testInfo.annotations.push({ type: "viewport iso", description: line });
      await shot("g-iso-viewport");
      // The switch hides it without taking it away; on again brings the line back.
      const visible = iso.getByRole("switch", { name: "Show in viewport" });
      await visible.click();
      await expect.soft(status, "switched off, the line goes").toHaveCount(0);
      await visible.click();
      await expect.soft(status, "switched on, the line is back").toHaveText(line);
      await iso.getByRole("button", { name: "Hide iso" }).click();
      await expect(status, "Hide removes the iso mesh").toHaveCount(0);
      await expect.soft(iso.getByRole("button", { name: "Hide iso" }), "nothing left to hide").toHaveCount(0);
      await shot("g-iso-hidden");
    });

    await test.step("f. the Capping row opens the capping card", async () => {
      await expandRow(page, "Mesh", 1);
      await page.locator('[role=treeitem][data-row="mesh-capping"]').first().click();
      const card = page.locator('[data-ws="operation-card"]');
      await expect.soft(card, "the capping card").toBeVisible();
      await expect.soft(card, "the capping card is the cap operation's").toHaveAttribute("aria-label", /openings|cap/i);
      await expect.soft(page.locator('[data-ws="details-pane"] .details-title'), "the Details pane is not the Mesh panel").not.toHaveText(/^Mesh$/);
      testInfo.annotations.push({ type: "capping card", description: (await card.getAttribute("aria-label")) ?? "" });
      await shot("f-capping-card");
      await page.keyboard.press("Escape");
    });

    await test.step("d. no console errors beyond the known benign ones", async () => {
      const unexpected = consoleErrors.filter((text) => !BENIGN_CONSOLE_ERRORS.some((b) => b.pattern.test(text)));
      expect(unexpected, "unexpected console errors").toEqual([]);
    });
  });
});
