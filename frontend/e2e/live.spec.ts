import { test, expect } from "@playwright/test";
import { mkdir } from "node:fs/promises";
import path from "node:path";
const pages = (process.env.LB_PAGES || "/")
  .split(",")
  .map((s) => s.trim())
  .filter(Boolean);
const shots = process.env.LB_SHOTS || "/private/tmp/claude-501/lb-shots";
for (const pagePath of pages)
  for (const viewport of [
    { width: 1440, height: 900 },
    { width: 390, height: 844 },
  ])
    for (const scheme of ["light", "dark"] as const) {
      test(`${pagePath} ${viewport.width} ${scheme}`, async ({ browser, baseURL }) => {
        const context = await browser.newContext({ viewport, colorScheme: scheme });
        const page = await context.newPage();
        const errors: string[] = [];
        page.on("console", (message) => {
          if (message.type() === "error") errors.push(message.text());
        });
        page.on("pageerror", (error) => errors.push(error.message));
        page.on("response", (response) => {
          if (response.url().includes("/api/") && !response.ok())
            errors.push(`${response.status()} ${response.url()}`);
        });
        page.on("requestfailed", (request) => {
          if (request.url().includes("/api/")) errors.push(`Failed ${request.url()}`);
        });
        await page.goto(`${baseURL}${pagePath}`, { waitUntil: "networkidle" });
        await page.evaluate(() => document.fonts.ready);
        await page.waitForTimeout(500);
        const slug =
          pagePath === "/" ? "providers" : pagePath.replace(/^\//, "").replaceAll("/", "-");
        await mkdir(shots, { recursive: true });
        await page.screenshot({
          path: path.join(shots, `${slug}-${viewport.width}-${scheme}.png`),
          fullPage: true,
        });
        const width = await page.evaluate(() => document.documentElement.scrollWidth);
        expect(width, `Horizontal overflow: ${width}px > ${viewport.width}px`).toBeLessThanOrEqual(
          viewport.width,
        );
        expect(errors, `Browser/API errors: ${errors.join("; ")}`).toEqual([]);
        await context.close();
      });
    }
