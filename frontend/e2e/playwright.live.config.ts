import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: ".",
  testMatch: "live.spec.ts",
  use: {
    baseURL: process.env.LB_URL || "http://127.0.0.1:5174",
    browserName: "chromium",
    launchOptions: {
      executablePath:
        process.env.LB_CHROMIUM ||
        `${process.env.HOME}/Library/Caches/ms-playwright/chromium_headless_shell-1234/chrome-headless-shell-mac-arm64/chrome-headless-shell`,
    },
  },
  workers: 1,
  timeout: 60_000,
});
