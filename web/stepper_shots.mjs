// The M-502 pictures: the stepper in the Mesh stage, on the running demo (the protocol:
// .coordination/shots/<package>/ and ~/stokes-shots/<package>/).
import { chromium } from "playwright";
import { readFileSync, mkdirSync } from "node:fs";

const urlFile = "/home/chandra/stokes-demo/server-url.txt";
const base = process.argv[2] ?? readFileSync(urlFile, "utf8").trim();
const out1 = "/home/chandra/Documents/stokes/.coordination/shots/meshing-gui-m502";
const out2 = "/home/chandra/stokes-shots/meshing-gui-m502";
for (const d of [out1, out2]) mkdirSync(d, { recursive: true });

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1500, height: 900 } });
await page.goto(base + (base.includes("?") ? "&drive=1" : "?drive=1"));
await page.waitForTimeout(4000);

// The Mesh stage, fresh: the stepper visible with the tasks.
await page.getByRole("button", { name: "Mesh", exact: true }).first().click().catch(() => {});
await page.waitForTimeout(2500);
await page.screenshot({ path: out1 + "/stepper-fresh.png" });

// The collapsed form: hide the full stepper through the line's own toggle.
const toggle = page.locator("[data-ws='stepper-line-toggle']");
if (await toggle.count()) {
  await toggle.click();
  await page.waitForTimeout(800);
  await page.screenshot({ path: out1 + "/stepper-collapsed.png" });
  await toggle.click();
  await page.waitForTimeout(800);
}

// A task page: Geometry check, then Bodies.
const geoRow = page.locator("[data-ws='stepper-geometry']");
if (await geoRow.count()) {
  await geoRow.click();
  await page.waitForTimeout(1200);
  await page.screenshot({ path: out1 + "/geometry-check-page.png" });
}
const bodiesRow = page.locator("[data-ws='stepper-bodies']");
if (await bodiesRow.count()) {
  await bodiesRow.click();
  await page.waitForTimeout(1200);
  await page.screenshot({ path: out1 + "/bodies-page.png" });
}
const boundRow = page.locator("[data-ws='stepper-boundaries']");
if (await boundRow.count()) {
  await boundRow.click();
  await page.waitForTimeout(1200);
  await page.screenshot({ path: out1 + "/stepper-boundaries-todo.png" });
}

// Narrow window: the pane overlays.
await page.setViewportSize({ width: 1000, height: 800 });
await page.waitForTimeout(800);
await page.screenshot({ path: out1 + "/narrow-overlay.png" });

await browser.close();
console.log("shots written to", out1);
