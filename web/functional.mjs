import assert from "node:assert/strict";
import { mkdirSync, readFileSync } from "node:fs";
import { chromium } from "playwright-core";

const entry = new URL(process.argv[2] || "http://127.0.0.1:8765");
entry.searchParams.set("scoutTheme", "light");
entry.searchParams.set("view", "overview");
const browser = await chromium.launch({ channel: "msedge", headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1100 } });
  const errors = [];
  const externalRequests = new Set();
  page.on("pageerror", (error) => errors.push(String(error)));
  page.on("request", (request) => {
    if (new URL(request.url()).origin !== entry.origin) externalRequests.add(request.url());
  });
  const catalog = await (await page.request.get(new URL("/api/runs", entry).href)).json();
  const run = entry.searchParams.get("run") || catalog.selected;
  entry.searchParams.set("run", run);
  const api = async (route, parameters = {}) => {
    const target = new URL(`/api/${route}`, entry);
    for (const [key, value] of Object.entries({ run, ...parameters })) target.searchParams.set(key, value);
    const response = await page.request.get(target.href);
    assert.equal(response.status(), 200, target.href);
    return response.json();
  };
  const expected = await api("view");
  await page.goto(entry.href);
  await page.getByRole("heading", { name: run.split("/").at(-1), exact: true }).waitFor();
  await page.waitForFunction(() => !document.querySelector(".eyebrow")?.textContent.includes("Loading"));
  await page.evaluate(() => document.fonts.ready);
  assert.equal(await page.title(), "eusociality");
  assert.equal(await page.locator(".brand strong").innerText(), "eusociality.");
  for (const removed of ["InstitutionLab", "Real SDK events", "No synthetic activity", "Read-only observer", "Run workspace"]) {
    assert.ok(!(await page.locator("body").innerText()).includes(removed), `Removed filler/branding: ${removed}`);
  }
  const typography = await page.evaluate(() => ({
    heading: getComputedStyle(document.querySelector("h1")).fontFamily,
    body: getComputedStyle(document.body).fontFamily,
    loaded: [...document.fonts].filter((font) => font.status === "loaded").map((font) => font.family),
  }));
  assert.ok(typography.heading.includes("Manrope Variable"));
  assert.ok(typography.body.includes("DM Sans Variable"));
  assert.ok(typography.loaded.includes("Manrope Variable") && typography.loaded.includes("DM Sans Variable"));
  assert.equal(await page.locator(".agent-card").count(), Object.keys(expected.state.agents).length);

  const actor = Object.keys(expected.state.agents)[0];
  const agent = await api("agent", { actor, limit: 100 });
  await page.locator(".agent-card").first().click();
  await page.getByRole("heading", { name: actor, exact: true }).waitFor();
  const tool = agent.items.find((item) => item.kind === "tool" && item.output);
  assert.ok(tool, "Real run must contain a recorded tool output");
  const activity = page.locator(`[data-event-seq="${tool.seq}"]`);
  await activity.locator("[aria-expanded=false]").first().click();
  await activity.locator(".tool-result").waitFor();
  const output = typeof tool.output === "string" ? tool.output : JSON.stringify(tool.output, null, 2);
  assert.ok((await activity.locator(".tool-result").textContent()).replace(/\s+/g, "").includes(output.slice(0, 60).replace(/\s+/g, "")));
  assert.ok(await activity.locator(".tool-result").evaluate((element) => element.clientWidth < 1100), "Tool results must fit the workspace");
  mkdirSync("..\\docs", { recursive: true });
  await page.screenshot({ path: "..\\docs\\workspace-agent.png", fullPage: false });

  await page.getByRole("button", { name: "Agent files", exact: true }).click();
  await page.locator(".file-link").first().waitFor();
  assert.ok(agent.workspaces.length, "Fresh run must have real private-workspace checkpoints");
  await page.getByRole("combobox", { name: "File source", exact: true }).click();
  const initial = agent.workspaces[0];
  await page.getByRole("option", { name: `${initial.label} / ${initial.file_count} files`, exact: true }).click();
  const files = await api("files", { source: "workspace", actor, snapshot: initial.id });
  const chosen = files.files.find((file) => file.readable && file.path === "app.py");
  assert.ok(chosen);
  await page.getByRole("button", { name: "app.py", exact: true }).click();
  const file = await api("file", { source: "workspace", actor, snapshot: initial.id, path: chosen.path });
  const fileLine = file.content.split(/\r?\n/).find((line) => line.trim().length > 20).trim();
  await page.waitForFunction((line) => document.querySelector(".file-content")?.textContent.includes(line), fileLine);
  await page.screenshot({ path: "..\\docs\\workspace-files.png", fullPage: false });

  await page.getByRole("button", { name: "Work & proposals", exact: true }).click();
  await page.getByRole("heading", { name: "Submitted proposals" }).waitFor();
  assert.ok(await page.locator(".proposal").count(), "Actual submitted proposals");
  await page.getByRole("button", { name: "Session", exact: true }).click();
  await page.getByText(agent.sessions[0].session_id, { exact: false }).first().waitFor();

  await page.getByRole("button", { name: "Project files", exact: true }).click();
  await page.getByRole("button", { name: "app.py", exact: true }).click();
  await page.waitForFunction(() => document.querySelector(".file-content")?.textContent.includes("import importlib"));

  await page.getByRole("button", { name: "Communications", exact: true }).click();
  const messages = await api("communications");
  assert.ok(messages.messages.length, "Fresh real company must include delivered communication");
  await page.locator(".message-card").first().waitFor();
  assert.ok((await page.locator(".message-card").first().textContent()).includes(messages.messages[0].content.slice(0, 50)));

  await page.getByRole("button", { name: "Configuration", exact: true }).click();
  await page.getByRole("heading", { name: "Institutional rules" }).waitFor();
  assert.ok(await page.locator(".rule-card").count());
  await page.getByText("Exact resolved JSON & download", { exact: true }).click();
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "Download configuration", exact: true }).click();
  const downloaded = await download;
  assert.ok(downloaded.suggestedFilename().endsWith("resolved.json"));
  assert.deepEqual(JSON.parse(readFileSync(await downloaded.path(), "utf8")), (await api("config")).configuration);
  await page.getByLabel("Configuration source", { exact: true }).click();
  await page.getByRole("option", { name: "Template / smoke.yaml", exact: true }).click();
  await page.getByText("Previewing an actual YAML template.", { exact: false }).waitFor();

  await page.getByRole("button", { name: "Network", exact: true }).click();
  await page.locator(".network canvas").first().waitFor();
  await page.getByRole("button", { name: "Runtime & systems", exact: true }).click();
  await page.locator(".system-card").first().waitFor();
  assert.ok((await page.locator(".system-card").first().textContent()).includes(agent.sessions[0].session_id));

  await page.getByRole("button", { name: "Overview", exact: true }).click();
  await page.getByRole("slider", { name: "Replay event" }).focus();
  await page.keyboard.press("Home");
  await page.getByText("Replay / #1", { exact: true }).waitFor();
  await page.getByText("Unclaimed", { exact: true }).first().waitFor();
  const latest = page.waitForResponse((response) => {
    const target = new URL(response.url());
    return target.pathname === "/api/view" && !target.searchParams.has("at");
  });
  await page.getByRole("button", { name: "Back to latest", exact: true }).click();
  await latest;
  await page.getByRole("button", { name: "Latest", exact: true }).waitFor();
  await page.locator(".metric-grid").waitFor();
  await page.screenshot({ path: "..\\docs\\workspace-overview.png", fullPage: false });
  const lightColor = await page.locator("body").evaluate((element) => getComputedStyle(element).backgroundColor);
  await page.getByRole("button", { name: "Switch to dark theme", exact: true }).click();
  await page.getByRole("button", { name: "Switch to light theme", exact: true }).waitFor();
  assert.equal(await page.locator("html").getAttribute("data-theme"), "dark");
  assert.equal(new URL(page.url()).searchParams.get("run"), run);
  assert.equal(new URL(page.url()).searchParams.get("scoutTheme"), "dark");
  assert.notEqual(await page.locator("body").evaluate((element) => getComputedStyle(element).backgroundColor), lightColor);
  await page.screenshot({ path: "..\\docs\\eusociality-dark.png", fullPage: false });
  await page.getByRole("button", { name: "Switch to light theme", exact: true }).click();
  assert.equal(await page.locator("html").getAttribute("data-theme"), "light");

  const other = catalog.runs.find((item) => item.id !== run && item.status === "completed");
  assert.ok(other);
  await page.getByLabel("Run", { exact: true }).click();
  await page.getByRole("option", { name: other.id, exact: false }).click();
  await page.getByRole("heading", { name: other.id.split("/").at(-1), exact: true }).waitFor();

  entry.searchParams.set("scoutTheme", "dark");
  await page.goto(entry.href);
  await page.getByRole("heading", { name: run.split("/").at(-1), exact: true }).waitFor();
  assert.equal(await page.locator("html").getAttribute("data-theme"), "dark");
  assert.deepEqual(errors, []);
  assert.deepEqual([...externalRequests], [], "Observer must not load CDN or external-content resources");
  console.log(JSON.stringify({ passed: true, run, actor, views: 7, toolOutput: true, privateFiles: true, configurationDownload: true, runSelection: true, replay: true, branding: true, bundledFonts: true, themeToggle: true, errors }));
} finally {
  await browser.close();
}
