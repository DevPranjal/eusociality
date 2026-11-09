import { defineConfig } from "vite";
import tailwindcss from "@tailwindcss/vite";
import { fileURLToPath, URL } from "node:url";

export default defineConfig({
  base: "/assets/",
  plugins: [tailwindcss()],
  resolve: { alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) } },
  build: {
    outDir: "../src/institution_lab/observer/assets",
    emptyOutDir: true,
    assetsDir: "bundled",
    rollupOptions: {
      onwarn(warning, warn) {
        if (warning.code === "MODULE_LEVEL_DIRECTIVE" && warning.message.includes('"use client"')) return;
        warn(warning);
      },
    },
  },
  server: {
    proxy: { "/api": "http://127.0.0.1:8765" },
  },
});
