/**
 * Playwright over the real app (end-to-end GUI tests in `e2e/`). Kept apart from Vitest, whose tests
 * are pure helpers under `src/`: these start `python -m stokes.server` against a project and drive the
 * page it serves from `web/dist`, so `npm run build` must have run first.
 *
 *     STOKES_ELBOW_ENERGY_PROJECT=<path> npx playwright test e2e/elbow-energy.spec.ts
 *
 * Machine paths never live in the repository: each test reads its project from an environment
 * variable and skips with the variable's name when it is absent.
 */
import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "e2e",
  // One server per spec file, and an iso cut is not cheap: a run is a few minutes at most.
  timeout: 300_000,
  expect: { timeout: 30_000 },
  workers: 1,
  fullyParallel: false,
  retries: 0,
  reporter: [["list"]],
  use: {
    viewport: { width: 1600, height: 1000 },
    // The viewport is WebGL; software GL keeps it working on a machine with no GPU (CI, a VM).
    launchOptions: { args: ["--use-gl=swiftshader", "--enable-unsafe-swiftshader"] },
    trace: "off",
  },
});
