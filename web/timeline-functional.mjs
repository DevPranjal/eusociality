import assert from "node:assert/strict";
import { mkdirSync } from "node:fs";
import { chromium } from "playwright-core";

const origin = process.argv[2] || "http://127.0.0.1:8765";
const browser = await chromium.launch({ channel: "msedge", headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1100 } });
  const errors = [];
  const external = [];
  page.on("pageerror", (error) => errors.push(String(error)));
  page.on("request", (request) => {
    if (new URL(request.url()).origin !== origin) external.push(request.url());
  });
  const read = async (route, parameters) => {
    const target = new URL(`/api/${route}`, origin);
    for (const [key, value] of Object.entries(parameters)) target.searchParams.set(key, value);
    const response = await page.request.get(target.href);
    assert.equal(response.status(), 200, target.href);
    return response.json();
  };
  for (const run of ["society-scale-20261106/01-final", "society-scale-20261106/10-postfix"]) {
    const history = await read("history", { run, all_events: "true", limit: 200 });
    const timeline = await read("history", { run, limit: 200 });
    let publication = timeline.items.find((event) => event.kind === "integrate");
    let offset = timeline.items.length;
    while (!publication && offset < timeline.total) {
      const next = await read("history", { run, offset, limit: 200 });
      publication = next.items.find((event) => event.kind === "integrate");
      offset += next.items.length;
    }
    assert.ok(publication, "Retained real run must contain a publication");
    const early = await read("files", { run, at: 1 });
    const delivered = await read("files", { run, at: publication.seq });
    const final = await read("files", { run });
    const visible = (listing) => listing.files.filter((file) => /^(project|operations)\//.test(file.path));
    assert.equal(visible(early).length, 0);
    assert.ok(visible(delivered).length > 0);
    await page.goto(`${origin}/?run=${encodeURIComponent(run)}&view=timeline&scoutTheme=light`);
    await page.locator("[data-deliverable]").first().waitFor();
    await page.locator("[data-timeline-event]").first().waitFor();
    assert.equal(await page.locator("[data-deliverable]").count(), visible(final).length);
    await page.getByLabel("Jump to event", { exact: true }).fill("1");
    await page.getByRole("button", { name: "Go to event", exact: true }).click();
    await page.getByText("Published at event #1", { exact: false }).waitFor();
    await page.getByText("No deliverables had been published at this point.", { exact: true }).waitFor();
    assert.equal(await page.locator("[data-deliverable]").count(), 0);
    await page.getByLabel("Jump to event", { exact: true }).fill(String(publication.seq));
    await page.getByRole("button", { name: "Go to event", exact: true }).click();
    await page.locator(`[data-timeline-event="${publication.seq}"][aria-pressed="true"]`).waitFor();
    await page.locator("[data-deliverable]").first().waitFor();
    assert.equal(await page.locator("[data-deliverable]").count(), visible(delivered).length);
    const chosen = visible(delivered)[0].path;
    const expected = await read("file", { run, at: publication.seq, path: chosen });
    await page.locator(`[data-deliverable="${chosen}"]`).click();
    await page.getByRole("tab", { name: "Source", exact: true }).click();
    await page.waitForFunction(
      (text) => [...document.querySelectorAll(".timeline-file-preview pre")].some((element) => element.textContent === text),
      expected.content,
    );
    await page.locator(".timeline-detail summary").click();
    await page.locator(".timeline-detail pre").waitFor();
    await page.getByRole("button", { name: "Back to final state", exact: true }).click();
    await page.locator("[data-deliverable]").first().waitFor();
    await page.waitForFunction((count) => document.querySelectorAll("[data-deliverable]").length === count, visible(final).length);
    assert.equal(history.status, "completed");
    console.log(`${run}: exact early/publication/final files, event detail and source content passed`);
  }
  const chooser = page.getByRole("button", { name: "Run", exact: true });
  await chooser.click();
  await page.getByRole("option").filter({ hasText: "society-scale-20261106/01-final" }).first().click();
  await page.getByRole("heading", { name: "01-final", exact: true }).waitFor();
  await page.locator("[data-deliverable]").first().click();
  await page.getByRole("tab", { name: "Preview", exact: true }).click();
  mkdirSync("..\\docs", { recursive: true });
  await page.screenshot({ path: "..\\docs\\run-timeline.png", fullPage: false });
  const cards = await page.locator(".timeline-columns > .timeline-event-panel, .timeline-deliverables").evaluateAll(
    (elements) => elements.map((element) => ({ x: element.getBoundingClientRect().x, y: element.getBoundingClientRect().y })),
  );
  assert.ok(cards[0].x < cards[1].x, "Timeline and deliverables must be side by side on desktop");
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: "..\\docs\\run-timeline-mobile.png", fullPage: false });
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), "No page-level horizontal overflow");
  await page.getByRole("button", { name: "Switch to dark theme", exact: true }).click();
  await page.screenshot({ path: "..\\docs\\run-timeline-dark.png", fullPage: false });
  assert.deepEqual(errors, []);
  assert.deepEqual(external, []);
  console.log("Run switching, desktop/mobile layout, both themes and no external requests passed.");
} finally {
  await browser.close();
}
