// Generate README screenshots from synthetic data without reading a real account.
// Requires Playwright and Chromium: node tests/capture_ui.cjs
const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { chromium } = require('playwright');

async function main() {
  const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'ollama-buddy-captures-'));
  const output = path.join(__dirname, '..', 'docs', 'readme');
  const server = spawn('python3', [path.join(__dirname, 'fixture_server.py')], {
    env: { ...process.env, OLLAMA_BUDDY_DATA: temporary, PYTHONDONTWRITEBYTECODE: '1' },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  let browser;
  const errors = [];
  server.stderr.on('data', data => errors.push(data.toString()));
  try {
    const fixture = await new Promise((resolve, reject) => {
      let text = '';
      const timer = setTimeout(() => reject(new Error('Fixture startup timed out')), 5000);
      server.once('exit', code => { clearTimeout(timer); reject(new Error(`Fixture exit ${code}`)); });
      server.stdout.on('data', chunk => {
        text += chunk;
        if (text.includes('\n')) {
          clearTimeout(timer);
          resolve(JSON.parse(text.split('\n')[0]));
        }
      });
    });
    fs.mkdirSync(output, { recursive: true });
    browser = await chromium.launch({ headless: true });
    for (const language of ['en', 'fr']) {
      const suffix = language === 'en' ? '' : '.fr';
      const context = await browser.newContext({
        locale: language === 'fr' ? 'fr-FR' : 'en-US', timezoneId: 'Europe/Paris',
        viewport: { width: 1120, height: 880 }, colorScheme: 'light', reducedMotion: 'reduce',
      });
      // Preference changes affect only this isolated fixture, never the real app.
      await context.request.post(fixture.url + '/api/lang', { data: { lang: language } });
      await context.request.post(fixture.url + '/api/theme', { data: { theme: 'light' } });
      const page = await context.newPage();
      page.on('pageerror', error => errors.push(error.message));
      await page.goto(fixture.url);
      await page.locator('.quota .meter').waitFor();
      await page.evaluate(() => document.fonts.ready);
      await page.screenshot({ path: path.join(output, `dashboard${suffix}.png`), fullPage: true });
      await page.locator('.nav [data-view="activity"]').click();
      await page.locator('[data-chart-range="cycle"]').click();
      await page.screenshot({ path: path.join(output, `activity${suffix}.png`), fullPage: true });
      await page.locator('.nav [data-view="settings"]').click();
      // This is the native fixture; no macOS permissions or Keychain requests run here.
      await page.screenshot({ path: path.join(output, `settings${suffix}.png`), fullPage: true });
      await page.locator('#theme-select').selectOption('dark');
      await page.locator('#save-status').waitFor({ state: 'visible' });
      await page.locator('#save-status').waitFor({ state: 'hidden' });
      await page.locator('.nav [data-view="overview"]').click();
      await page.waitForFunction(() => document.documentElement.dataset.theme === 'dark');
      await page.screenshot({ path: path.join(output, `dashboard-dark${suffix}.png`), fullPage: true });
      await context.request.post(fixture.url + '/api/theme', { data: { theme: 'light' } });
      const mini = await context.newPage();
      mini.on('pageerror', error => errors.push(error.message));
      await mini.setViewportSize({ width: 300, height: 520 });
      await mini.goto(fixture.url + '/mini');
      await mini.locator('.meter').waitFor();
      await mini.waitForFunction(() => document.documentElement.dataset.theme === 'light');
      await mini.locator('body').screenshot({ path: path.join(output, `menu-bar${suffix}.png`) });
      await context.close();
    }
    assert.deepEqual(errors, []);
    console.log('Generated 10 README screenshots in docs/readme/ using synthetic data (English and French).');
  } finally {
    if (browser) await browser.close();
    server.kill('SIGTERM');
    await new Promise(resolve => server.exitCode !== null || server.signalCode !== null ? resolve() : server.once('exit', resolve));
    fs.rmSync(temporary, { recursive: true, force: true });
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
