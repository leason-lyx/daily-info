import path from "node:path";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "."),
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./test/setup.ts"],
    environmentOptions: {
      jsdom: {
        url: "http://localhost/",
      },
    },
    include: ["**/*.{test,spec}.{ts,tsx}"],
    exclude: ["node_modules", ".next", "coverage", "e2e"],
    coverage: {
      provider: "v8",
      reporter: ["text", "html"],
      include: ["app/**/*.{ts,tsx}", "features/**/*.{ts,tsx}", "lib/**/*.{ts,tsx}"],
      exclude: ["app/layout.tsx", "**/*.test.{ts,tsx}", "test/**", "e2e/**"],
      thresholds: {
        lines: 75,
        statements: 75,
        functions: 75,
        branches: 60,
      },
    },
  },
});
