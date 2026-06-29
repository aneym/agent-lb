import { defineConfig } from "@playwright/test";

const SCREENSHOT_PORT = 4174;
const SCREENSHOT_BASE_URL = `http://127.0.0.1:${SCREENSHOT_PORT}`;

export default defineConfig({
  testDir: ".",
  testMatch: "capture.spec.ts",
  timeout: 60_000,
  workers: 1,
  use: {
    baseURL: SCREENSHOT_BASE_URL,
    viewport: { width: 1440, height: 900 },
    deviceScaleFactor: 2,
    launchOptions: {
      args: ["--use-gl=angle", "--use-angle=swiftshader"],
    },
  },
  webServer: {
    command: `bun run build && bun run preview --host 127.0.0.1 --port ${SCREENSHOT_PORT}`,
    url: SCREENSHOT_BASE_URL,
    reuseExistingServer: false,
    timeout: 120_000,
  },
});
