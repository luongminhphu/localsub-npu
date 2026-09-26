import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  clearScreen: false,
  server: {
    port: 1420,
    strictPort: true,
    watch: {
      // Cargo writes and locks build artifacts under src-tauri/target while `tauri dev`
      // compiles; watching them crashes Vite on Windows with EBUSY.
      ignored: ["**/src-tauri/**"],
    },
  },
});
