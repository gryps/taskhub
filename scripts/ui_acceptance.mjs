import fs from "node:fs";
import { chromium } from "playwright-core";

const [url, outputRoot] = process.argv.slice(2);
if (!url || !outputRoot) throw new Error("usage: node ui_acceptance.mjs URL OUTPUT_DIR");
const token = (process.env.TASKHUB_ADMIN_TOKEN || fs.readFileSync(0, "utf8")).trim();
if (!token) throw new Error("administrator token is required on stdin");
fs.mkdirSync(outputRoot, { recursive: true });
const edge = process.env.EDGE_PATH || "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const browser = await chromium.launch({ executablePath: edge, headless: true, args: ["--no-first-run"] });
const results = [];
try {
  for (const viewport of [{name: "desktop", width: 1440, height: 1000}, {name: "mobile", width: 390, height: 844}]) {
    const page = await browser.newPage({ viewport });
    await page.goto(url, { waitUntil: "domcontentloaded", timeout: 30000 });
    await page.locator('#loginForm [name="token"]').fill(token);
    await page.locator('#loginForm button[type="submit"]').click();
    await page.locator('nav button[data-view="advanced"]').click();
    await page.locator("#onboardingWizard").waitFor({ state: "visible", timeout: 30000 });
    await page.locator("#advancedStatus").filter({ hasNotText: "等待刷新" }).waitFor({ timeout: 30000 });
    for (let step = 0; step < 3; step += 1) await page.locator("#onboardNext").click();
    await page.locator("#onboardPreflight").click();
    await page.locator("#onboardApply:not([disabled])").waitFor({ timeout: 30000 });
    const layout = await page.evaluate(() => ({
      bodyWidth: document.body.scrollWidth,
      viewportWidth: document.documentElement.clientWidth,
      wizardVisible: Boolean(document.querySelector("#onboardingWizard")),
      deviceTableVisible: Boolean(document.querySelector("#deviceTable")),
      preflightPassed: document.querySelectorAll("#onboardingChecks .check-mark.ok").length >= 3,
    }));
    await page.screenshot({ path: `${outputRoot}/${viewport.name}.png`, fullPage: true });
    results.push({ ...viewport, ...layout, horizontalOverflow: layout.bodyWidth > layout.viewportWidth + 1 });
    await page.close();
  }
} finally {
  await browser.close();
}
console.log(JSON.stringify({ status: results.every(item => !item.horizontalOverflow && item.preflightPassed) ? "passed" : "failed", results }));
