import fs from "node:fs";
import { chromium } from "playwright-core";

const [url, outputRoot] = process.argv.slice(2);
if (!url || !outputRoot) throw new Error("usage: node flow_workbench_acceptance.mjs URL OUTPUT_DIR");
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
    await page.locator("#flowdesk").waitFor({ state: "visible", timeout: 30000 });
    await page.locator("#flowDeskStatus").filter({ hasNotText: "加载中" }).waitFor({ timeout: 30000 });
    const layout = await page.evaluate(() => {
      const flowDesk = document.querySelector("#flowdesk");
      const canvasScroll = document.querySelector(".flow-canvas-scroll");
      return {
        bodyWidth: document.body.scrollWidth,
        viewportWidth: document.documentElement.clientWidth,
        flowDeskVisible: Boolean(flowDesk && !flowDesk.classList.contains("hidden")),
        stageCount: document.querySelectorAll("[data-flow-stage]").length,
        canvasContained: Boolean(canvasScroll && canvasScroll.scrollWidth >= canvasScroll.clientWidth),
        attentionText: document.querySelector("#flowAttention")?.textContent?.trim() || "",
      };
    });
    await page.screenshot({ path: `${outputRoot}/${viewport.name}.png`, fullPage: true });
    results.push({...viewport, ...layout, horizontalOverflow: layout.bodyWidth > layout.viewportWidth + 1});
    await page.close();
  }
} finally {
  await browser.close();
}
const passed = results.every(item => item.flowDeskVisible && item.stageCount === 7 && item.canvasContained && !item.horizontalOverflow);
console.log(JSON.stringify({status: passed ? "passed" : "failed", results}));
if (!passed) process.exitCode = 1;
