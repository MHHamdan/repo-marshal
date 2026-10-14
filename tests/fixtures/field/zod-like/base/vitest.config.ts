import { defineConfig } from "vitest/config";

// Shortened from colinhacks/zod vitest.config.ts at 680d19f^: the root `vitest run` runs every
// workspace package as a project.
export default defineConfig({
  test: {
    projects: ["packages/*", "./vitest.compile.config.ts"],
    watch: false,
    isolate: true,
  },
});
