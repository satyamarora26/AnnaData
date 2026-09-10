const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const { chromium } = require('playwright');

async function main() {
  const live = process.argv.includes('--live');
  const url = process.env.APP_URL || 'http://127.0.0.1:3000';
  const output = process.env.EVIDENCE_DIR || '/tmp/annadata-browser-evidence';
  await fs.mkdir(output, { recursive: true });
  const browser = await chromium.launch({ headless: true, ...(process.env.CHROME_PATH ? { executablePath: process.env.CHROME_PATH } : {}) });
  const results = [];
  try {
    for (const viewport of [{ width: 1440, height: 900 }, { width: 390, height: 844 }]) {
      const context = await browser.newContext({ viewport });
      const page = await context.newPage();
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      await page.addInitScript(() => {
        navigator.geolocation.getCurrentPosition = (_, fail) => fail({ code: 1, message: 'Test denies location' });
      });
      if (!live) {
        await page.route('**/agent/stream', route => route.fulfill({
          json: { answer: 'PM-KISAN provides Rs. 6,000 per year in three installments. Source: official guidelines.' },
        }));
      }
      await page.goto(url);
      const input = page.getByPlaceholder('Enter a prompt here');
      await input.fill('How much does PM-KISAN pay each year?');
      const start = Date.now();
      await input.press('Enter');
      if (live) {
        await page.waitForFunction(() => {
          const status = document.querySelector('[role="status"]');
          return status && /Understanding|Checking|Preparing/.test(status.textContent);
        }, null, { timeout: 90000 });
        await page.screenshot({ path: path.join(output, `progress-${viewport.width}.png`) });
      }
      await page.locator('.result-data').first().waitFor({ timeout: 180000 });
      const answer = await page.locator('.result-data').first().innerText();
      assert.match(answer, /6[,.]?000/);
      assert.doesNotMatch(answer, /could not reach|API error/i);
      const box = await input.boundingBox();
      const send = await page.locator('button[type="submit"]').boundingBox();
      assert(box && send && box.x >= 0 && send.x + send.width <= viewport.width, 'Composer must fit viewport');
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth);
      assert.equal(overflow, false, 'Page must not overflow horizontally');
      assert.deepEqual(errors, [], 'No uncaught browser errors');
      await page.screenshot({ path: path.join(output, `${live ? 'live' : 'mock'}-${viewport.width}.png`), fullPage: true });
      results.push({ viewport, mode: live ? 'live provider' : 'mocked backend', answerReceived: true, elapsedMs: Date.now() - start });
      if (!live) {
        await page.unroute('**/agent/stream');
        await page.route('**/agent/stream', route => route.fulfill({ status: 429, json: { detail: 'Too many requests. Please retry later.' } }));
        await input.fill('Second request');
        await input.press('Enter');
        await page.getByText(/Sorry, could not reach.*429/).waitFor();
        assert.equal(await page.locator('button[type="submit"]').isEnabled(), true);
        results.push({ viewport, mode: 'mocked backend', rateLimitErrorVisible: true, composerRecovered: true });
      }
      await context.close();
    }
    const report = { timestamp: new Date().toISOString(), results };
    await fs.writeFile(path.join(output, `${live ? 'live' : 'mock'}-report.json`), JSON.stringify(report, null, 2));
    console.log(JSON.stringify(report, null, 2));
  } finally {
    await browser.close();
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
