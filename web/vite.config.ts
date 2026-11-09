import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// The Python server (stokes.server) serves web/dist in production. In development the
// page is served by Vite and talks to the API URL given in the ?api= query parameter.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: { port: 5173, strictPort: true, host: "127.0.0.1" },
  build: { outDir: "dist", sourcemap: false, target: "es2022", chunkSizeWarningLimit: 1500 },
});
