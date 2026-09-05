import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { chromium } from "playwright-core";

const [inputPath, outputDir] = process.argv.slice(2);
if (!inputPath || !outputDir) throw new Error("usage: node h5_acceptance.mjs INPUT_JSON OUTPUT_DIR");
const request = JSON.parse(fs.readFileSync(inputPath, "utf8").replace(/^\uFEFF/, ""));
fs.mkdirSync(outputDir, { recursive: true });

const edge = process.env.EDGE_PATH || "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const viewports = Array.isArray(request.viewports) && request.viewports.length
  ? request.viewports
  : [
      { name: "desktop", width: 1440, height: 1000 },
      { name: "mobile", width: 390, height: 844 },
    ];
const launchOptions = {
  executablePath: edge,
  headless: request.headed === false,
  args: ["--disable-background-mode", "--no-first-run"],
};
const persistent = Boolean(request.profile_dir);
const persistentContext = persistent
  ? await chromium.launchPersistentContext(request.profile_dir, launchOptions)
  : null;
const browser = persistent ? null : await chromium.launch(launchOptions);
const expectedHost = new URL(request.url).hostname.toLowerCase();
const results = [];
try {
  for (const viewport of viewports) {
    const context = persistentContext || await browser.newContext();
    const page = await context.newPage();
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    const consoleErrors = [];
    const pageErrors = [];
    page.on("console", message => {
      if (message.type() === "error") consoleErrors.push(message.text().slice(0, 1000));
    });
    page.on("pageerror", error => pageErrors.push(String(error).slice(0, 1000)));
    const response = await page.goto(request.url, { waitUntil: "domcontentloaded", timeout: request.timeout_ms || 60000 });
    if (new URL(page.url()).hostname.toLowerCase() !== expectedHost) {
      throw new Error("top-level navigation left the approved host");
    }
    await page.waitForTimeout(3000);
    const finalPath = new URL(page.url()).pathname.toLowerCase();
    const forbiddenPath = (request.forbidden_path_prefixes || []).find(prefix =>
      finalPath.startsWith(String(prefix).toLowerCase()),
    );
    if (forbiddenPath) throw new Error("authenticated page inspection redirected to a forbidden login route");
    for (const selector of request.required_selectors || []) {
      await page.locator(selector).first().waitFor({ state: "visible", timeout: 10000 });
    }
    const metrics = await page.evaluate(() => ({
      title: document.title,
      scrollWidth: document.documentElement.scrollWidth,
      clientWidth: document.documentElement.clientWidth,
      scrollHeight: document.documentElement.scrollHeight,
      clientHeight: document.documentElement.clientHeight,
    }));
    const screenshot = `${viewport.name}.png`;
    await page.screenshot({ path: path.join(outputDir, screenshot), fullPage: true });
    results.push({
      viewport,
      status: response?.status() || 0,
      url: page.url(),
      screenshot,
      metrics,
      horizontal_overflow: metrics.scrollWidth > metrics.clientWidth,
      console_errors: consoleErrors,
      page_errors: pageErrors,
      passed: Boolean(response?.ok()),
    });
    await page.close();
    if (!persistent) await context.close();
  }
} finally {
  if (persistentContext) await persistentContext.close();
  if (browser) await browser.close();
}

const report = {
  url: request.url,
  headed: request.headed !== false,
  persistent_profile: persistent,
  passed: results.every(item => item.passed),
  results,
};
const reportPath = path.join(outputDir, "report.json");
fs.writeFileSync(reportPath, JSON.stringify(report, null, 2));
const files = fs.readdirSync(outputDir).filter(name => name !== "manifest.json").sort().map(name => {
  const data = fs.readFileSync(path.join(outputDir, name));
  return { name, size: data.length, sha256: crypto.createHash("sha256").update(data).digest("hex") };
});
const manifest = { created_at: new Date().toISOString(), files };
fs.writeFileSync(path.join(outputDir, "manifest.json"), JSON.stringify(manifest, null, 2));
process.stdout.write(JSON.stringify({ ...report, artifacts: manifest }));
if (!report.passed) process.exitCode = 2;
