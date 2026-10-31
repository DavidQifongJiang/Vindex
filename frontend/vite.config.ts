import { fileURLToPath, URL } from "node:url";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url))
    }
  },
  server: {
    port: 5173,
    proxy: {
      "/health": "http://localhost:8000",
      "/me": "http://localhost:8000",
      "/public": "http://localhost:8000",
      "/search": "http://localhost:8000",
      "/videos": "http://localhost:8000"
    }
  }
});
