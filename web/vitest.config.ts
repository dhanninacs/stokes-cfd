/** Vitest over the front end's pure helpers (T-040): no DOM and no React, so the tests run in Node. */
import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    environment: "node",
    include: ["src/**/*.test.ts"],
  },
});